"""Guards the project-id contract the RED/document upload depends on.

Onboarding derives a lowercase slug (local_project_id); the upload endpoint must
resolve the Global Workspace with the SAME normalization, or a name with
uppercase/underscore 404s (looked up "Ranjith_new--global" while the workspace is
"ranjith-new--global"). Run from the agent repo root:

    set PYTHONPATH=.
    python -m pytest app/agentic_platform/tests/test_document_upload_id.py -q
"""

from __future__ import annotations

import pytest

from app.agentic_platform.fe_core.projects.models import local_project_id
from app.agentic_platform.fe_core.workspaces.models import global_workspace_id


@pytest.mark.parametrize("name", ["Ranjith_new", "ranjith-new", "RANJITH NEW", "Ranjith New"])
def test_normalized_upload_key_matches_onboarded_workspace(name):
    # Whatever the caller types, normalizing via local_project_id yields the id
    # onboarding stored, and its Global Workspace id is the canonical slug form.
    assert global_workspace_id(local_project_id(name)) == "ranjith-new--global"


def test_local_project_id_lowercases_and_slugs():
    assert local_project_id("Ranjith_new") == "ranjith-new"
    assert local_project_id("UW Credit Risk") == "uw-credit-risk"
