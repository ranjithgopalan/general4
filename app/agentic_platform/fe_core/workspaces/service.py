"""Workspace lifecycle: open the Global one, fan out a Mini one per EPIC.

FR-P4 is the interesting requirement: "A Mini Workspace MUST be created
automatically for each EPIC when the EPIC set is approved". Two consequences
shape this module.

First, fan-out is triggered by *approval*, not by generation. A Draft EPIC set
must not open ten workspaces, because the Product Owner may reject it and the
EPIC list may change.

Second, fan-out must be idempotent. Approval can be replayed (a retried request,
a redelivered event, an operator re-approving after a rejection), and each replay
must converge on the same set of workspaces rather than adding more. Workspace
ids are therefore derived from the EPIC key instead of generated.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from app.agentic_platform.fe_core.auth.personas import WorkspaceTier
from app.agentic_platform.fe_core.kb.models import ArtifactStatus, SdlcArtifact
from app.agentic_platform.fe_core.workspaces.models import (
    Epic,
    Workspace,
    architecture_workspace_id,
    global_workspace_id,
    new_architecture_workspace,
    new_global_workspace,
    new_mini_workspace,
)

logger = logging.getLogger(__name__)

#: Artefact types whose approval opens the Mini Workspaces.
EPIC_SET_ARTIFACT_TYPES = frozenset({"epic", "epic-set"})


class WorkspaceService:
    """Reads and writes workspaces through the store."""

    def __init__(self, store, pipeline_name: str = "uw-cr"):
        self.store = store
        self.pipeline_name = pipeline_name

    # -- the Global workspace ---------------------------------------------
    #: Project-level facts carried on the Global Workspace.
    INTAKE_FIELDS = ("category", "intake_source", "source_language",
                     "target_framework", "gear_id")

    def ensure_global(self, kb_application_id: str,
                      pipeline: str | None = None,
                      intake: dict | None = None) -> Workspace:
        """Idempotent: exactly one Global Workspace per programme (FR-P4).

        `intake` records category, intake source and the source/target stack.
        Supplied values are written even when the workspace already exists, so
        re-onboarding corrects a wrong choice rather than silently keeping the
        first one -- but a value that is absent from `intake` leaves the stored
        one alone, so a partial re-onboard cannot erase what was already known.
        """
        supplied = {k: v for k, v in (intake or {}).items()
                    if k in self.INTAKE_FIELDS and v is not None}

        existing = self.store.get_workspace(global_workspace_id(kb_application_id))
        if existing is not None:
            changed = {k: v for k, v in supplied.items()
                       if getattr(existing, k, None) != v}
            if changed:
                for key, value in changed.items():
                    setattr(existing, key, value)
                logger.info("Global Workspace %s: intake updated (%s)",
                            existing.id, ", ".join(sorted(changed)))
                return self.store.put_workspace(existing)
            return existing

        workspace = new_global_workspace(kb_application_id,
                                         pipeline or self.pipeline_name)
        for key, value in supplied.items():
            setattr(workspace, key, value)
        logger.info("Opened Global Workspace %s", workspace.id)
        return self.store.put_workspace(workspace)

    # -- the Architecture workspace ---------------------------------------
    def ensure_architecture(self, kb_application_id: str,
                            pipeline: str | None = None) -> Workspace:
        """Idempotent: exactly one Architecture Workspace per programme.

        Created lazily by arch_sync_gate after FRD is approved. Its id is derived
        (`{app}--architecture`) so a concurrent create converges instead of racing.
        """
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415

        arch_pipeline = pipeline or getattr(
            get_settings(), "fe_pipeline_architecture", "uw-cr-architecture")
        existing = self.store.get_workspace(architecture_workspace_id(kb_application_id))
        if existing is not None:
            return existing
        workspace = new_architecture_workspace(kb_application_id, arch_pipeline)
        logger.info("Opened Architecture Workspace %s", workspace.id)
        return self.store.put_workspace(workspace)

    # -- the Epic Assembler workspace ------------------------------------
    def ensure_assembler(self, kb_application_id: str) -> Workspace:
        """Idempotent: exactly one Epic Assembler Workspace per programme.

        Created lazily once every per-EPIC Mini Workspace has its last stage
        approved. Its id is derived (`{app}--epic-assembler`) so concurrent
        creates converge safely.
        """
        from app.agentic_platform.fe_core.config import get_settings  # noqa: PLC0415

        assembler_pipeline = getattr(
            get_settings(), "fe_pipeline_assembler", "uw-cr-epic-assembler")
        ws_id = f"{kb_application_id}--epic-assembler"
        existing = self.store.get_workspace(ws_id)
        if existing is not None:
            return existing
        workspace = Workspace(
            id=ws_id,
            kb_application_id=kb_application_id,
            pipeline=assembler_pipeline,
            tier=WorkspaceTier.MINI,
        )
        logger.info("Opened Epic Assembler Workspace %s", workspace.id)
        return self.store.put_workspace(workspace)

    # -- fan-out ----------------------------------------------------------
    def fan_out(self, artifact: SdlcArtifact,
                pipeline: str | None = None) -> list[Workspace]:
        """Open one Mini Workspace per EPIC in an approved EPIC-set artefact.

        Returns every workspace for the EPIC set -- pre-existing ones included --
        so a replay reports the same list rather than an empty one.
        """
        if artifact.artifact_type not in EPIC_SET_ARTIFACT_TYPES:
            return []
        if artifact.status is not ArtifactStatus.APPROVED:
            # Fan-out on a Draft would open workspaces for EPICs the Product
            # Owner has not accepted, and rejecting the set could not close them
            # cleanly once work had started inside.
            logger.debug(
                "Not fanning out %s: status is %s, not APPROVED",
                artifact.id, artifact.status.value,
            )
            return []

        epics = extract_epics(artifact)
        if not epics:
            logger.warning(
                "Approved EPIC set %s yielded no EPICs; no Mini Workspaces opened. "
                "Check the artefact at %s", artifact.id, artifact.path,
            )
            return []

        # Stamp owner_epic on KB nodes so Get-EpicKbCards can filter by epic.
        # Non-blocking: any failure is caught and logged.
        try:
            from app.agentic_platform.fe_core.kb.epic_map import compute as _compute_epic_map  # noqa: PLC0415
            from app.agentic_platform.fe_core.kb.pg_sink import update_owner_epic  # noqa: PLC0415
            _emap = _compute_epic_map(artifact.kb_application_id, epics)
            if _emap.card_to_epic:
                update_owner_epic(_emap.kb_version, _emap.card_to_epic)
        except Exception as _exc:  # noqa: BLE001
            logger.exception("fan_out: epic_map non-fatal: %s", _exc)

        parent = self.ensure_global(artifact.kb_application_id, pipeline)
        opened: list[Workspace] = []
        for epic in epics:
            workspace = new_mini_workspace(
                artifact.kb_application_id,
                pipeline or artifact.pipeline or self.pipeline_name,
                epic,
                parent_workspace_id=parent.id,
                created_from_artifact_id=artifact.id,
            )
            existing = self.store.get_workspace(workspace.id)
            if existing is not None:
                opened.append(existing)
                continue
            opened.append(self.store.put_workspace(workspace))

        logger.info(
            "EPIC set %s approved: %d Mini Workspace(s) (%d new)",
            artifact.id, len(opened),
            sum(1 for w in opened if w.created_from_artifact_id == artifact.id),
        )
        return opened

    # -- visibility -------------------------------------------------------
    def readable_workspace_ids(self, workspace: Workspace | str) -> list[str]:
        """Which workspaces a run in `workspace` may read approved artefacts from.

        Three-tier visibility (FR-P4 extended):
          * Global reads self + the Architecture Workspace (so epic-set consumes the
            SRD and the console can show architecture docs at the Global level).
          * Architecture reads self + its Global parent (FRD/PRD context).
          * Mini reads self + Global + Architecture (all upstream docs) — but never a
            sibling EPIC's work.
        """
        resolved = (self.store.get_workspace(workspace)
                    if isinstance(workspace, str) else workspace)
        if resolved is None:
            return []
        arch_id = architecture_workspace_id(resolved.kb_application_id)
        if resolved.tier is WorkspaceTier.GLOBAL:
            return [resolved.id, arch_id]
        parent = resolved.parent_workspace_id or global_workspace_id(
            resolved.kb_application_id)
        if resolved.tier is WorkspaceTier.ARCHITECTURE:
            return [resolved.id, parent]
        return [resolved.id, parent, arch_id]

    def close(self, workspace_id: str, *, cancelled: bool = False) -> Workspace | None:
        workspace = self.store.get_workspace(workspace_id)
        if workspace is None:
            return None
        return self.store.put_workspace(workspace.close(cancelled=cancelled))


# --- EPIC extraction ------------------------------------------------------

def extract_epics(artifact: SdlcArtifact) -> list[Epic]:
    """Read the EPIC list out of an approved EPIC-set artefact.

    An artefact's `path` is a directory the agent wrote into, so the EPIC list
    can arrive in several shapes. Structured files are preferred and Markdown is
    the fallback, because a generator that emits JSON should not be second-guessed
    by a heading parser.
    """
    from app.agentic_platform.fe_core.artifacts.reader import materialise  # noqa: PLC0415
    try:
        root = materialise(artifact)
    except Exception:  # noqa: BLE001
        root = None
    if root is None:
        root = Path(artifact.path) if artifact.path else None
    else:
        root = Path(root)
    if root is None or not root.exists():
        return []

    for candidate in _candidate_files(root, (".json",)):
        epics = _from_json(candidate)
        if epics:
            return _dedupe(epics, artifact.id)

    for candidate in _candidate_files(root, (".md", ".markdown")):
        epics = _from_markdown(candidate)
        if epics:
            return _dedupe(epics, artifact.id)
    return []


def _candidate_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in suffixes else []
    files = [p for p in sorted(root.rglob("*"))
             if p.is_file() and p.suffix.lower() in suffixes]
    # A file that names itself after epics is far more likely to be the list.
    files.sort(key=lambda p: (0 if "epic" in p.name.lower() else 1, len(p.parts)))
    return files[:40]


def _from_json(path: Path) -> list[Epic]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (json.JSONDecodeError, OSError):
        return []
    rows = None
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        for key in ("epics", "epic_set", "items", "EPICS"):
            if isinstance(data.get(key), list):
                rows = data[key]
                break
    if not rows:
        return []

    epics: list[Epic] = []
    for index, row in enumerate(rows, 1):
        if isinstance(row, str):
            title = row.strip()
            key = title
        elif isinstance(row, dict):
            title = str(row.get("title") or row.get("name")
                        or row.get("summary") or "").strip()
            key = str(row.get("key") or row.get("id") or row.get("epic_id")
                      or title).strip()
        else:
            continue
        if not (title or key):
            continue
        try:
            epics.append(Epic(key=key or title, title=title or key,
                              summary=(row.get("summary")
                                       if isinstance(row, dict) else None),
                              sequence=index))
        except ValueError:
            continue
    return epics


#: `## EPIC-1: Title`, `### Epic 2 - Title`, `- **EPIC 3**: Title`, `## 3. EPIC-1 — Title`
_EPIC_LINE = re.compile(
    r"^\s*(?:#{1,4}\s*|[-*]\s*)?(?:\d+[.)]\s*)?\**\s*epic[\s_-]*([A-Za-z0-9.]+)\**\s*[-:.)—]\s*(.+?)\s*\**\s*$",
    re.IGNORECASE,
)


def _from_markdown(path: Path) -> list[Epic]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []

    epics: list[Epic] = []
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue   # a code sample must not be mistaken for the EPIC list
        match = _EPIC_LINE.match(line)
        if not match:
            continue
        number, title = match.group(1), match.group(2).strip()
        title = re.sub(r"\s*\|.*$", "", title).strip()   # drop table remainders
        if not title or len(title) > 200:
            continue
        try:
            epics.append(Epic(key=f"{number}", title=title,
                              sequence=len(epics) + 1))
        except ValueError:
            continue
    return epics


def _dedupe(epics: list[Epic], source_artifact_id: str) -> list[Epic]:
    """First occurrence of each key wins, and sequence is renumbered.

    A generated document commonly lists each EPIC twice -- once in a summary
    table and once as a section -- and creating two workspaces for one EPIC would
    breach "one Mini Workspace per EPIC".
    """
    seen: dict[str, Epic] = {}
    for epic in epics:
        if epic.key in seen:
            continue
        seen[epic.key] = epic
    return [
        epic.model_copy(update={"sequence": index,
                                "source_artifact_id": source_artifact_id})
        for index, epic in enumerate(seen.values(), 1)
    ]
