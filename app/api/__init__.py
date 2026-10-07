"""HTTP surface (FastAPI routers)."""

from app.api.bundle import router as bundle_router
from app.api.chat import router as chat_router
from app.api.document_export import router as document_export_router
from app.api.sessions import router as sessions_router
from app.api.fe_architecture import router as fe_architecture_router
from app.api.fe_brd import router as fe_brd_router
from app.api.fe_developer import router as fe_developer_router
from app.api.fe_fsd import router as fe_fsd_router
from app.api.fe_stories import router as fe_stories_router
from app.api.fe_generate import router as fe_generate_router
from app.api.fe_merge import router as fe_merge_router
from app.api.fe_qa import router as fe_qa_router
from app.api.fe_relationships import router as fe_relationships_router
from app.api.fe_analysis import router as fe_impact_router
from app.api.fe_analysis_unified import router as fe_analysis_router
from app.api.health import router as health_router
from app.api.overview import router as overview_router
from app.api.personas import router as personas_router
from app.api.re_graph import router as re_graph_router
from app.api.re_review import router as re_review_router
from app.api.versions import router as versions_router
from app.api.workspace import router as workspace_router

__all__ = [
    "bundle_router",
    "chat_router",
    "document_export_router",
    "sessions_router",
    "fe_analysis_router",
    "fe_architecture_router",
    "fe_brd_router",
    "fe_developer_router",
    "fe_fsd_router",
    "fe_stories_router",
    "fe_generate_router",
    "fe_merge_router",
    "fe_qa_router",
    "fe_relationships_router",
    "fe_impact_router",
    "health_router",
    "overview_router",
    "personas_router",
    "re_graph_router",
    "re_review_router",
    "versions_router",
    "workspace_router",
]
