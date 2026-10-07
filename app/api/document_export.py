"""Document export endpoints — render templates and download artifacts."""

from __future__ import annotations

import io
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.core.dependencies import get_current_persona, get_fe_generate_service
from app.lifecycle.generate import FeGenerateService
from app.services.document_export_service import document_export_service
from app.services.template_service import template_service, TemplateMetadata
from app.utils.logging import log

router = APIRouter(prefix="/ws", tags=["document-export"])

GenDep = Annotated[FeGenerateService, Depends(get_fe_generate_service)]
PersonaDep = Annotated[str, Depends(get_current_persona)]


# ── Document Export Endpoints ──────────────────────────────────────────────────


@router.get("/{workspace_id}/export/impact-analysis.docx", summary="Export impact analysis as .docx")
async def export_impact_analysis_docx(
    workspace_id: str,
    svc: GenDep,
) -> FileResponse:
    """
    Export impact analysis using AIG template.
    Steps:
    1. Fetch latest analysis artifact
    2. Render with template (variable replacement)
    3. Save to S3
    4. Return as download
    """
    try:
        # Fetch latest analysis
        analysis = await svc.get_business_impact(workspace_id)
        if not analysis:
            raise HTTPException(404, "No impact analysis generated yet. Run analysis first.")

        # Render with template
        docx_bytes = await document_export_service.render_impact_analysis(workspace_id, analysis)

        # Save to S3 (optional, for versioning)
        # s3_key = f"japan/auto/workspaces/{workspace_id}/artifacts/impact-analysis-v1.docx"
        # await s3.put_object(Bucket="aig-kb-artifacts", Key=s3_key, Body=docx_bytes)

        log.info(f"Impact analysis exported | ws={workspace_id}")

        return FileResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"impact-analysis-{workspace_id}.docx",
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to export impact analysis | ws={workspace_id} | {e}")
        raise HTTPException(500, f"Failed to export document: {str(e)}")


@router.get("/{workspace_id}/export/fsd.docx", summary="Export FSD as .docx")
async def export_fsd_docx(
    workspace_id: str,
    svc: GenDep,
) -> FileResponse:
    """Export FSD (Functional Specification Document) using template."""
    try:
        # Fetch latest FSD artifact
        fsd = await svc.get_fsd(workspace_id)
        if not fsd:
            raise HTTPException(404, "No FSD generated yet")

        # Convert FSD to dict for template rendering
        fsd_dict = fsd.dict() if hasattr(fsd, "dict") else fsd

        # Render with template
        docx_bytes = await document_export_service.render_fsd(workspace_id, fsd_dict)

        log.info(f"FSD exported | ws={workspace_id}")

        return FileResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"fsd-{workspace_id}.docx",
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to export FSD | ws={workspace_id} | {e}")
        raise HTTPException(500, f"Failed to export document: {str(e)}")


@router.get("/{workspace_id}/export/brd.docx", summary="Export BRD as .docx")
async def export_brd_docx(
    workspace_id: str,
    svc: GenDep,
) -> FileResponse:
    """Export BRD (Business Requirements Document) using template."""
    try:
        # Fetch latest BRD artifact
        brd = await svc.get_brd(workspace_id)
        if not brd:
            raise HTTPException(404, "No BRD generated yet")

        brd_dict = brd.dict() if hasattr(brd, "dict") else brd
        docx_bytes = await document_export_service.render_brd(workspace_id, brd_dict)

        log.info(f"BRD exported | ws={workspace_id}")

        return FileResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"brd-{workspace_id}.docx",
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to export BRD | ws={workspace_id} | {e}")
        raise HTTPException(500, f"Failed to export document: {str(e)}")


@router.get("/{workspace_id}/export/srd.docx", summary="Export SRD as .docx")
async def export_srd_docx(
    workspace_id: str,
    svc: GenDep,
) -> FileResponse:
    """Export SRD (Solution Requirements Document) using template."""
    try:
        srd = await svc.get_srd(workspace_id)
        if not srd:
            raise HTTPException(404, "No SRD generated yet")

        srd_dict = srd.dict() if hasattr(srd, "dict") else srd
        docx_bytes = await document_export_service.render_srd(workspace_id, srd_dict)

        log.info(f"SRD exported | ws={workspace_id}")

        return FileResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"srd-{workspace_id}.docx",
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to export SRD | ws={workspace_id} | {e}")
        raise HTTPException(500, f"Failed to export document: {str(e)}")


@router.get("/{workspace_id}/export/test-plan.docx", summary="Export QA Test Plan as .docx")
async def export_test_plan_docx(
    workspace_id: str,
    svc: GenDep,
) -> FileResponse:
    """Export QA Test Plan using template."""
    try:
        test_plan = await svc.get_test_plan(workspace_id)
        if not test_plan:
            raise HTTPException(404, "No test plan generated yet")

        test_plan_dict = test_plan.dict() if hasattr(test_plan, "dict") else test_plan
        docx_bytes = await document_export_service.render_test_plan(workspace_id, test_plan_dict)

        log.info(f"Test plan exported | ws={workspace_id}")

        return FileResponse(
            io.BytesIO(docx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"test-plan-{workspace_id}.docx",
        )

    except HTTPException:
        raise
    except Exception as e:
        log.error(f"Failed to export test plan | ws={workspace_id} | {e}")
        raise HTTPException(500, f"Failed to export document: {str(e)}")


# ── Template Management Endpoints ──────────────────────────────────────────────


@router.get("/templates", summary="List available templates", tags=["templates"])
async def list_templates(lob: str = "japan-auto") -> list[TemplateMetadata]:
    """List all available templates for a LOB."""
    templates = await template_service.list_templates(lob)
    return templates


@router.get("/templates/{artifact_type}", summary="Get template schema", tags=["templates"])
async def get_template_manifest(artifact_type: str, lob: str = "japan-auto") -> dict:
    """
    Get template schema (variables, sections, etc.).
    Used by UI to show available fields for template customization.
    """
    manifest = await template_service.get_template_manifest(artifact_type, lob)
    if not manifest:
        raise HTTPException(404, f"Template not found for {artifact_type}")
    return manifest


@router.post("/templates/{artifact_type}/upload", summary="Upload template", tags=["templates"])
async def upload_template(
    artifact_type: str,
    file: UploadFile,
    workspace_id: str | None = None,
    lob: str = "japan-auto",
    persona: PersonaDep = None,
) -> dict:
    """
    Upload new template.
    - workspace_id=None → LOB-wide template (requires admin)
    - workspace_id=XXX → workspace-specific override (workspace owner)

    Returns: S3 key of uploaded template
    """
    try:
        # Read file contents
        contents = await file.read()

        # Upload to S3
        s3_key = await template_service.upload_template(
            artifact_type=artifact_type,
            docx_bytes=contents,
            workspace_id=workspace_id,
            lob=lob,
        )

        scope = "workspace" if workspace_id else "lob"
        log.info(f"Template uploaded | type={artifact_type} scope={scope} key={s3_key} by={persona}")

        return {
            "message": f"Template uploaded successfully",
            "artifact_type": artifact_type,
            "scope": scope,
            "s3_key": s3_key,
        }

    except Exception as e:
        log.error(f"Failed to upload template | type={artifact_type} | {e}")
        raise HTTPException(500, f"Failed to upload template: {str(e)}")


@router.post("/templates/{artifact_type}/variables", summary="Update template variables", tags=["templates"])
async def update_template_variables(
    artifact_type: str,
    variables: dict[str, str],
    lob: str = "japan-auto",
    persona: PersonaDep = None,
) -> dict:
    """
    Update variable schema for a template.
    Called when adding new variables to a template.
    """
    try:
        await template_service.update_template_variables(
            artifact_type=artifact_type,
            variables=variables,
            lob=lob,
        )

        log.info(f"Template variables updated | type={artifact_type} by={persona}")

        return {
            "message": "Template variables updated",
            "artifact_type": artifact_type,
            "variables_updated": len(variables),
        }

    except Exception as e:
        log.error(f"Failed to update template variables | type={artifact_type} | {e}")
        raise HTTPException(500, f"Failed to update variables: {str(e)}")
