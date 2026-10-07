"""Versioned template registry (single source; file-backed now, DB later).

Resolution order (adopted from lmod `steering_loader` 4-tier): **DB `fe_templates` → domain-pack →
default file**. Only the default-file tier is wired for v1; the DB/domain-pack tiers are seams
(`_resolve_path`). A template is a section spec consumed by BOTH renderers (HTML + AIG .docx), so
there is exactly one source per artifact type — never a separate HTML vs docx template.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

_TEMPLATES_DIR = Path(__file__).resolve().parent


class TemplateSection(BaseModel):
    key: str
    title: str
    kind: str = "data"  # data (rendered from the structured object) | llm (generated prose)


class Template(BaseModel):
    template_id: str
    template_version: str
    title: str
    artifact_type: str
    sections: list[TemplateSection]


def _resolve_path(artifact_type: str, version: str | None) -> Path:
    """Default-file tier. (DB `fe_templates` + domain-pack tiers are future seams.)"""
    folder = _TEMPLATES_DIR / artifact_type
    if version:
        return folder / f"{artifact_type}.{version}.json"
    # latest = highest-sorted versioned file (v1, v2, …)
    candidates = sorted(folder.glob(f"{artifact_type}.*.json"))
    if not candidates:
        raise FileNotFoundError(f"no template for artifact_type={artifact_type!r}")
    return candidates[-1]


@lru_cache(maxsize=32)
def get_template(artifact_type: str, version: str | None = None) -> Template:
    """Load a versioned template (cached). Raises FileNotFoundError if none exists."""
    path = _resolve_path(artifact_type, version)
    return Template.model_validate(json.loads(path.read_text(encoding="utf-8")))
