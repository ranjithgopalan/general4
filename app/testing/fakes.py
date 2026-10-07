"""Test doubles for workspace service dependencies.

Re-usable across test modules — import the classes you need rather than
copy-pasting the class bodies into each test file. Lives in app/testing/
alongside fake_llm.py.
"""

from __future__ import annotations

from app.models.graph import SeedHit, SeedResult
from app.models.kb import KbAnswer


class FakeWorkspaceRepo:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}

    async def create(self, **kw):
        row = {**kw, "created_at": None, "updated_at": None, "closed_at": None}
        self.rows[kw["workspace_id"]] = row
        return dict(row)

    async def get(self, workspace_id):
        r = self.rows.get(workspace_id)
        return dict(r) if r else None

    async def list(self, *, gear_id=None, state=None, type=None, limit=50, offset=0):
        return [dict(r) for r in self.rows.values()][offset : offset + limit]

    async def set_state(self, workspace_id, new_state):
        self.rows[workspace_id]["state"] = new_state
        return dict(self.rows[workspace_id])

    async def close(self, workspace_id):
        self.rows[workspace_id]["state"] = "CLOSED"
        return dict(self.rows[workspace_id])


class FakeArtifactRepo:
    def __init__(self) -> None:
        self.items: list[dict] = []
        self.links: list[dict] = []

    async def add(self, **kw):
        self.items.append(kw)
        return {**kw, "created_at": None}

    async def list_for_workspace(self, workspace_id):
        return [dict(a) for a in self.items if a["workspace_id"] == workspace_id]

    async def latest_for_kind(self, workspace_id, kind):
        matches = [a for a in self.items if a.get("workspace_id") == workspace_id and a.get("kind") == kind]
        return dict(matches[-1]) if matches else None

    async def set_reviewed(self, workspace_id, kind, user=None, name=None):
        return None

    async def add_grounds_link(self, **kw):
        self.links.append({**kw, "link_type": "GROUNDS"})
        return {**kw, "link_type": "GROUNDS"}

    async def add_derives_link(self, **kw):
        self.links.append({**kw, "link_type": "DERIVES_FROM"})
        return {**kw, "link_type": "DERIVES_FROM"}


class FakeStore:
    """Disabled S3 store — keeps tests hermetic (no network); handler falls back to the DB mirror."""

    enabled = False

    def key(self, workspace_id, stage, name):
        return f"{workspace_id}/{stage}/{name}"

    def uri(self, key):
        return f"s3://test/{key}"

    async def put_json(self, key, obj):
        return None

    async def put_bytes(self, key, data, content_type=None):
        return None

    async def get_text(self, key):
        return None


class FakeAuditRepo:
    async def append(self, **kw):
        return 1


class FakeRefreshEnqueuer:
    async def enqueue(self, workspace_id, *, gear_id="japan"):
        return f"refresh-{workspace_id}"


class FakeTrace:
    """Traceability service double — stores links in FakeArtifactRepo without Postgres."""

    def __init__(self, art: "FakeArtifactRepo") -> None:
        self._art = art

    async def add_grounds(self, *, workspace_id, from_artifact_id, **kwargs) -> None:
        self._art.links.append({
            "from_artifact_id": from_artifact_id,
            "workspace_id": workspace_id,
            "link_type": "GROUNDS",
            **{k: v for k, v in kwargs.items() if k in ("to_kb_card_id", "stage", "persona", "artifact_kind")},
        })

    async def add_derives(self, workspace_id=None, from_artifact_id=None, to_artifact_id=None, **kwargs) -> None:
        self._art.links.append({
            "from_artifact_id": from_artifact_id,
            "to_artifact_id": to_artifact_id,
            "workspace_id": workspace_id,
            "link_type": "DERIVES_FROM",
        })

    async def auto_link_to_previous(self, *, workspace_id, current_artifact_id, current_kind, **kwargs) -> None:
        self._art.links.append({
            "from_artifact_id": current_artifact_id,
            "workspace_id": workspace_id,
            "link_type": "DERIVES_FROM",
            "kind": current_kind,
        })

    async def cleanup_stage_artifacts(self, workspace_id: str, kind: str) -> None:
        pass


class FakeKb:
    """KB query double — always abstains (no Postgres, no network)."""

    async def query(self, *, question, persona="ba", top_k=8, **kwargs) -> KbAnswer:
        return KbAnswer(question=question, persona=persona, abstained=True)

    async def read_many(self, ids: list[str]) -> dict:
        return {}


class FakeGraph:
    """Graph provider double — no-op seeds/expand (no Postgres, no graph file)."""

    async def seeds(self, query: str, category=None, limit=None) -> SeedResult:
        return SeedResult(query=query, seeds=[])

    async def expand_seeds(self, seed_ids: list[str], depth: int = 2, edge_labels=None) -> list[SeedHit]:
        return []
