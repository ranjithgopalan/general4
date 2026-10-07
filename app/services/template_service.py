"""Template management service — fetch, store, list, version templates in S3."""

from __future__ import annotations

import json
from typing import Optional

import boto3
from pydantic import BaseModel

from app.config.settings import settings
from app.utils.logging import log


class TemplateMetadata(BaseModel):
    """Template metadata from registry"""
    id: str
    name: str
    version: str
    artifact_type: str
    s3_key: str
    variables: dict[str, str]
    created_at: str
    created_by: str


class TemplateService:
    """Manages template lifecycle: fetch, store, list, update."""

    def __init__(self):
        self.s3 = None  # Lazy initialization — set on first use
        self.bucket = settings.S3_BUCKET_NAME or ""  # Use S3_BUCKET_NAME from settings
        self.manifest_key_template = "{lob}/templates/_manifest.json"

    def _ensure_s3_client(self):
        """Initialize S3 client on first use (deferred to avoid import-time failures)."""
        if self.s3 is None:
            # TODO: integrate with Vault for AWS credentials resolution
            # For now, boto3 will use credential chain: env > ~/.aws > EC2 role
            self.s3 = boto3.client("s3", region_name=settings.AWS_REGION)

    async def get_template(
        self,
        artifact_type: str,
        workspace_id: str,
        lob: str = "japan-auto",
    ) -> bytes:
        """
        Fetch template for artifact type.
        Priority:
        1. Workspace-specific override
        2. LOB default template
        3. Global default template
        """
        # Check workspace override first
        ws_template = await self._get_workspace_template(workspace_id, artifact_type, lob)
        if ws_template:
            log.info(f"Using workspace-specific template for {artifact_type} | ws={workspace_id}")
            return ws_template

        # Fall back to LOB template
        lob_template = await self._get_lob_template(artifact_type, lob)
        log.info(f"Using LOB template for {artifact_type} | lob={lob}")
        return lob_template

    async def _get_workspace_template(
        self,
        workspace_id: str,
        artifact_type: str,
        lob: str,
    ) -> Optional[bytes]:
        """Fetch workspace-specific template override (if exists)"""
        try:
            self._ensure_s3_client()
            key = f"{lob}/workspaces/{workspace_id}/templates/{artifact_type}-template.docx"
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None
        except Exception as e:
            log.warning(f"Failed to fetch workspace template | ws={workspace_id} type={artifact_type} | {e}")
            return None

    async def _get_lob_template(
        self,
        artifact_type: str,
        lob: str,
    ) -> bytes:
        """Fetch LOB default template"""
        try:
            self._ensure_s3_client()
            key = f"{lob}/templates/{artifact_type}-template.docx"
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            return response["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            log.error(f"Template not found | artifact_type={artifact_type} lob={lob}")
            raise FileNotFoundError(f"No template found for {artifact_type} in LOB {lob}")
        except Exception as e:
            log.error(f"Failed to fetch LOB template | type={artifact_type} lob={lob} | {e}")
            raise

    async def list_templates(self, lob: str = "japan-auto") -> list[TemplateMetadata]:
        """List all available templates for a LOB"""
        try:
            manifest = await self._get_manifest(lob)
            return [TemplateMetadata(**t) for t in manifest.get("templates", [])]
        except Exception as e:
            log.error(f"Failed to list templates | lob={lob} | {e}")
            return []

    async def _get_manifest(self, lob: str) -> dict:
        """Fetch template registry manifest from S3"""
        try:
            self._ensure_s3_client()
            key = self.manifest_key_template.format(lob=lob)
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
            return json.loads(response["Body"].read().decode("utf-8"))
        except self.s3.exceptions.NoSuchKey:
            log.warning(f"Template manifest not found | lob={lob}")
            return {"templates": []}
        except Exception as e:
            log.error(f"Failed to fetch manifest | lob={lob} | {e}")
            return {"templates": []}

    async def upload_template(
        self,
        artifact_type: str,
        docx_bytes: bytes,
        workspace_id: Optional[str] = None,
        lob: str = "japan-auto",
    ) -> str:
        """
        Upload new template.
        workspace_id=None → LOB-wide template
        workspace_id=XXX → workspace-specific override

        Returns: S3 key of uploaded template
        """
        try:
            self._ensure_s3_client()
            if workspace_id:
                # Workspace-specific override
                key = f"{lob}/workspaces/{workspace_id}/templates/{artifact_type}-template.docx"
                scope = "workspace"
            else:
                # LOB-wide template
                key = f"{lob}/templates/{artifact_type}-template.docx"
                scope = "lob"

            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=docx_bytes,
                ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )

            log.info(f"Template uploaded | type={artifact_type} scope={scope} key={key}")
            return key

        except Exception as e:
            log.error(f"Failed to upload template | type={artifact_type} | {e}")
            raise

    async def get_template_manifest(
        self,
        artifact_type: str,
        lob: str = "japan-auto",
    ) -> dict:
        """
        Fetch template schema — variables, sections, etc.
        Used by UI to show what fields are available for template customization
        """
        try:
            manifest = await self._get_manifest(lob)
            for template in manifest.get("templates", []):
                if template.get("artifact_type") == artifact_type:
                    return template

            log.warning(f"Template not found in manifest | type={artifact_type} lob={lob}")
            return {}

        except Exception as e:
            log.error(f"Failed to get template manifest | type={artifact_type} | {e}")
            return {}

    async def update_template_variables(
        self,
        artifact_type: str,
        variables: dict[str, str],
        lob: str = "japan-auto",
    ) -> None:
        """
        Update variable schema for a template in the manifest.
        Called when adding new variables to a template.
        """
        try:
            self._ensure_s3_client()
            manifest = await self._get_manifest(lob)

            # Find and update the template
            for template in manifest.get("templates", []):
                if template.get("artifact_type") == artifact_type:
                    template["variables"].update(variables)
                    break

            # Save updated manifest back to S3
            key = self.manifest_key_template.format(lob=lob)
            self.s3.put_object(
                Bucket=self.bucket,
                Key=key,
                Body=json.dumps(manifest, indent=2).encode("utf-8"),
                ContentType="application/json",
            )

            log.info(f"Template variables updated | type={artifact_type}")

        except Exception as e:
            log.error(f"Failed to update template variables | type={artifact_type} | {e}")
            raise


# Global instance
template_service = TemplateService()
