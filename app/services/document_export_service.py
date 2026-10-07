"""Document export service — render templates with artifact data."""

from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from docx import Document

from app.lifecycle.stages.analysis.sections.business import BusinessImpactView
from app.services.template_service import template_service
from app.utils.logging import log


class DocumentExportService:
    """Renders templates with artifact data using manual variable replacement."""

    async def render_impact_analysis(
        self,
        workspace_id: str,
        analysis: BusinessImpactView,
        lob: str = "japan-auto",
    ) -> bytes:
        """
        Render impact analysis document from template.
        Steps:
        1. Fetch template
        2. Extract variables from analysis data
        3. Replace {variable} placeholders
        4. Return rendered docx bytes
        """
        try:
            # Fetch template
            template_bytes = await template_service.get_template(
                "impact_analysis",
                workspace_id,
                lob,
            )

            # Extract variables from analysis
            variables = self._extract_impact_variables(analysis)

            # Replace variables in docx
            rendered = self._replace_variables(template_bytes, variables)

            log.info(f"Impact analysis document rendered | ws={workspace_id}")
            return rendered

        except Exception as e:
            log.error(f"Failed to render impact analysis | ws={workspace_id} | {e}")
            raise

    async def render_fsd(
        self,
        workspace_id: str,
        fsd: dict[str, Any],
        lob: str = "japan-auto",
    ) -> bytes:
        """
        Render FSD (Functional Specification Document) from template.
        fsd parameter is a dict with all FSD sections and metadata.
        """
        try:
            template_bytes = await template_service.get_template("fsd", workspace_id, lob)
            variables = self._extract_fsd_variables(workspace_id, fsd)
            rendered = self._replace_variables(template_bytes, variables)

            log.info(f"FSD document rendered | ws={workspace_id}")
            return rendered

        except Exception as e:
            log.error(f"Failed to render FSD | ws={workspace_id} | {e}")
            raise

    async def render_brd(
        self,
        workspace_id: str,
        brd: dict[str, Any],
        lob: str = "japan-auto",
    ) -> bytes:
        """Render BRD (Business Requirements Document) from template."""
        try:
            template_bytes = await template_service.get_template("brd", workspace_id, lob)
            variables = self._extract_brd_variables(workspace_id, brd)
            rendered = self._replace_variables(template_bytes, variables)

            log.info(f"BRD document rendered | ws={workspace_id}")
            return rendered

        except Exception as e:
            log.error(f"Failed to render BRD | ws={workspace_id} | {e}")
            raise

    async def render_srd(
        self,
        workspace_id: str,
        srd: dict[str, Any],
        lob: str = "japan-auto",
    ) -> bytes:
        """Render SRD (Solution Requirements Document) from template."""
        try:
            template_bytes = await template_service.get_template("srd", workspace_id, lob)
            variables = self._extract_srd_variables(workspace_id, srd)
            rendered = self._replace_variables(template_bytes, variables)

            log.info(f"SRD document rendered | ws={workspace_id}")
            return rendered

        except Exception as e:
            log.error(f"Failed to render SRD | ws={workspace_id} | {e}")
            raise

    async def render_test_plan(
        self,
        workspace_id: str,
        test_plan: dict[str, Any],
        lob: str = "japan-auto",
    ) -> bytes:
        """Render QA Test Plan document from template."""
        try:
            template_bytes = await template_service.get_template("test_plan", workspace_id, lob)
            variables = self._extract_test_plan_variables(workspace_id, test_plan)
            rendered = self._replace_variables(template_bytes, variables)

            log.info(f"Test Plan document rendered | ws={workspace_id}")
            return rendered

        except Exception as e:
            log.error(f"Failed to render test plan | ws={workspace_id} | {e}")
            raise

    def _replace_variables(self, docx_bytes: bytes, variables: dict[str, str]) -> bytes:
        """
        Manual variable replacement in .docx file.

        Steps:
        1. Load docx from bytes
        2. Find all {variable_name} patterns in paragraphs, tables, headers, footers
        3. Replace with variables[variable_name]
        4. Return modified docx as bytes

        Current: Simple text replacement in paragraphs and tables
        Future: Support runs (preserves formatting), headers/footers, nested tables
        """
        try:
            doc = Document(io.BytesIO(docx_bytes))

            # Replace in body paragraphs
            for para in doc.paragraphs:
                for key, value in variables.items():
                    placeholder = f"{{{key}}}"
                    if placeholder in para.text:
                        # Simple replacement (preserves paragraph formatting)
                        para.text = para.text.replace(placeholder, str(value))

            # Replace in tables
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for key, value in variables.items():
                                placeholder = f"{{{key}}}"
                                if placeholder in para.text:
                                    para.text = para.text.replace(placeholder, str(value))

            # Replace in headers
            for section in doc.sections:
                for para in section.header.paragraphs:
                    for key, value in variables.items():
                        placeholder = f"{{{key}}}"
                        if placeholder in para.text:
                            para.text = para.text.replace(placeholder, str(value))

            # Replace in footers
            for section in doc.sections:
                for para in section.footer.paragraphs:
                    for key, value in variables.items():
                        placeholder = f"{{{key}}}"
                        if placeholder in para.text:
                            para.text = para.text.replace(placeholder, str(value))

            # Export to bytes
            output = io.BytesIO()
            doc.save(output)
            output.seek(0)
            return output.getvalue()

        except Exception as e:
            log.error(f"Failed to replace variables in docx | {e}")
            raise

    def _extract_impact_variables(self, analysis: BusinessImpactView) -> dict[str, str]:
        """
        Extract variables from impact analysis for template.
        Maps BusinessImpactView fields to template variables.
        """
        return {
            # Metadata
            "workspace_id": analysis.workspace_id or "",
            "title": "Impact Analysis",
            "date": analysis.generated_at.strftime("%B %d, %Y") if analysis.generated_at else "",
            "generated_at": analysis.generated_at.strftime("%B %d, %Y, %H:%M:%S %p") if analysis.generated_at else "",

            # Requirement & classification
            "requirement": analysis.requirement or "",
            "change_class": analysis.change_class or "",
            "change_statement": analysis.change_statement or "",
            "confidence": f"{analysis.confidence:.0%}" if analysis.confidence else "0%",

            # Sections
            "section_1_title": "Change Classification",
            "section_1_content": f"{analysis.change_class} (confidence {analysis.confidence:.2f})" if analysis.change_class else "",

            "section_2_title": "Summary",
            "section_2_content": analysis.summary or "",

            "section_3_title": "Scope — New / Enhancement / Existing",
            "section_3_new": ", ".join([s.get("name", "") for s in analysis.scope.get("new", [])]) if analysis.scope else "",
            "section_3_enhancement": ", ".join([s.get("name", "") for s in analysis.scope.get("enhancement", [])]) if analysis.scope else "",
            "section_3_existing": ", ".join([s.get("name", "") for s in analysis.scope.get("existing", [])]) if analysis.scope else "",

            "section_4_title": "How it touches the existing system",
            "section_4_content": "; ".join([f"{s.get('name', '')} ({s.get('kind', '')})" for s in analysis.touch_points]) if analysis.touch_points else "—",

            "section_5_title": "What is being modified",
            "section_5_content": "; ".join([f"{s.get('name', '')} ({s.get('kind', '')})" for s in analysis.modifications]) if analysis.modifications else "—",

            "section_6_title": "Downstream effect — what breaks",
            "section_6_content": "; ".join([f"{s.get('name', '')} ({s.get('kind', '')})" for s in analysis.downstream]) if analysis.downstream else "No downstream dependents found.",

            "section_7_title": "Where changes are needed",
            "section_7_content": "; ".join([f"{s.get('name', '')} ({s.get('kind', '')})" for s in analysis.change_locations]) if analysis.change_locations else "—",

            "section_8_title": "Conflicts with existing work",
            "section_8_content": "; ".join(analysis.conflicts) if analysis.conflicts else "No conflicts detected.",

            "section_9_title": "Coverage & blind spots",
            "section_9_coverage": f"{analysis.coverage.get('coverage_pct', 0):.0f}%" if analysis.coverage else "0%",
            "section_9_note": analysis.coverage.get("note", "") if analysis.coverage else "",

            "section_10_title": "Risks & effort",
            "section_10_effort": analysis.effort or "",
            "section_10_risks": "; ".join(analysis.risks) if analysis.risks else "No specific delivery risks identified.",

            "section_11_title": "Open questions & gaps",
            "section_11_content": "; ".join(analysis.questions) if analysis.questions else "None at this stage.",

            "section_12_title": "References",
            "section_12_content": ", ".join(analysis.references) if analysis.references else "—",
        }

    def _extract_fsd_variables(self, workspace_id: str, fsd: dict[str, Any]) -> dict[str, str]:
        """Extract variables from FSD for template."""
        return {
            "workspace_id": workspace_id,
            "title": "Functional Specification Document",
            "date": datetime.now().strftime("%B %d, %Y"),
            "requirement": fsd.get("requirement", ""),
            "change_class": fsd.get("change_class", ""),
            # ... map remaining FSD fields (13 sections)
            # For now, add FSD-specific fields as they become available
        }

    def _extract_brd_variables(self, workspace_id: str, brd: dict[str, Any]) -> dict[str, str]:
        """Extract variables from BRD for template."""
        return {
            "workspace_id": workspace_id,
            "title": "Business Requirements Document",
            "date": datetime.now().strftime("%B %d, %Y"),
            "requirement": brd.get("requirement", ""),
            "change_class": brd.get("change_class", ""),
            # ... map BRD-specific fields (10 sections)
        }

    def _extract_srd_variables(self, workspace_id: str, srd: dict[str, Any]) -> dict[str, str]:
        """Extract variables from SRD for template."""
        return {
            "workspace_id": workspace_id,
            "title": "Solution Requirements Document",
            "date": datetime.now().strftime("%B %d, %Y"),
            # ... map SRD-specific fields
        }

    def _extract_test_plan_variables(self, workspace_id: str, test_plan: dict[str, Any]) -> dict[str, str]:
        """Extract variables from test plan for template."""
        return {
            "workspace_id": workspace_id,
            "title": "QA Test Plan",
            "date": datetime.now().strftime("%B %d, %Y"),
            # ... map test plan fields
        }


# Global instance
document_export_service = DocumentExportService()
