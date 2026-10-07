"""Unit tests for the Global / Architecture / Mini three-tier separation.

Covers the workspace factory + service logic (id derivation, idempotent
ensure_architecture, three-tier readable_workspace_ids) and that the shipped
pipelines load with the expected tiers. Run from the agent repo root:

    set PYTHONPATH=.
    python -m pytest app/agentic_platform/tests/test_architecture_tier.py -q
"""

from __future__ import annotations

from pathlib import Path

from app.agentic_platform.fe_core.auth.personas import WorkspaceTier
from app.agentic_platform.fe_core.workspaces.models import (
    Epic,
    architecture_workspace_id,
    global_workspace_id,
    new_architecture_workspace,
    new_global_workspace,
    new_mini_workspace,
)
from app.agentic_platform.fe_core.workspaces.service import WorkspaceService

APP = "uw-credit-risk"


class _Store:
    """Minimal store surface used by WorkspaceService.ensure_architecture."""

    def __init__(self):
        self._ws: dict = {}

    def get_workspace(self, wid):
        return self._ws.get(wid)

    def put_workspace(self, ws):
        self._ws[ws.id] = ws
        return ws


def test_architecture_id_and_factory():
    assert architecture_workspace_id(APP).endswith("--architecture")
    ws = new_architecture_workspace(APP, "uw-cr-architecture")
    assert ws.tier is WorkspaceTier.ARCHITECTURE
    assert ws.id == architecture_workspace_id(APP)
    assert ws.parent_workspace_id == global_workspace_id(APP)


def test_ensure_architecture_is_idempotent():
    svc = WorkspaceService(_Store(), "uw-cr-global")
    a = svc.ensure_architecture(APP, "uw-cr-architecture")
    b = svc.ensure_architecture(APP, "uw-cr-architecture")
    assert a.id == b.id == architecture_workspace_id(APP)


def test_readable_workspace_ids_three_tiers():
    svc = WorkspaceService(_Store(), "uw-cr-global")
    g = new_global_workspace(APP, "uw-cr-global")
    a = new_architecture_workspace(APP, "uw-cr-architecture")
    m = new_mini_workspace(APP, "uw-cr-mini",
                           Epic(key="epic-1", title="X", source_artifact_id="art1"))
    # Global sees itself + architecture; Architecture sees itself + global;
    # Mini sees itself + global + architecture (never a sibling EPIC).
    assert svc.readable_workspace_ids(g) == [g.id, a.id]
    assert svc.readable_workspace_ids(a) == [a.id, g.id]
    assert svc.readable_workspace_ids(m) == [m.id, g.id, a.id]


def test_shipped_pipelines_load_with_expected_tiers():
    from app.agentic_platform.fe_core.pipeline.registry import discover_pipelines
    import app.agentic_platform.fe_core.config as config_mod

    pdir = Path(config_mod.__file__).parent.parent / "pipelines"
    found = discover_pipelines(pdir)
    assert found["uw-cr-global"].tier == "global"
    assert [s.key for s in found["uw-cr-global"].ordered] == ["prd", "frd", "epic-set"]
    assert found["uw-cr-global"].arch_inputs == ["srd"]

    arch = found["uw-cr-architecture"]
    assert arch.tier == "architecture"
    assert arch.parent == "uw-cr-global"
    assert arch.arch_after == "frd"
    assert [s.key for s in arch.ordered] == ["adr", "nfr", "sdd", "srd"]

    assert found["uw-cr-mini"].tier == "mini"
