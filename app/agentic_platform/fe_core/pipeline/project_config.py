"""Per-project, per-stage and per-agent instructions — configuration, not prompt text.

Agents and plugins are generic. Everything an application needs that is specific to
it lives under ``src/workspaces/<project>/``:

    src/workspaces/<project>/
      domain-pack.json              the facts (stack, services, SP families, RBAC, ...)
      agent-config/
        README.md                   how to use this directory (optional)
        <stage-key>.md              extra instructions injected into that stage's prompt
        agents/<agent-name>.md      extra instructions a plugin sub-agent must read

Precedence when they disagree, highest first:

    1. the user's own words in the run request
    2. src/workspaces/<project>/agent-config/<stage-key>.md   (stage override)
    3. src/workspaces/<project>/agent-config/agents/<agent>.md (agent override, read by the plugin router)
    4. src/workspaces/<project>/domain-pack.json               (facts)
    5. the plugin's own agent .md                          (generic defaults)

A developer who wants an agent to behave differently for this application edits
(2) or (3) and re-runs the stage. Nothing under ``src/api/plugins/`` needs to change.

This module is deliberately tolerant: a project without a domain pack or without an
agent-config directory still runs — the block simply says so, so the agent knows it
is working from plugin defaults and must not invent application facts.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["ProjectConfig", "load_project_config", "project_config_block", "constraint_sentence"]

_MAX_INSTRUCTION_CHARS = 12_000  # keep a stage override from swallowing the prompt


@dataclass
class ProjectConfig:
    project_id: str
    root: Path
    domain_pack_path: Path | None = None
    domain_pack: dict[str, Any] = field(default_factory=dict)
    agent_config_dir: Path | None = None
    stage_instructions: dict[str, str] = field(default_factory=dict)   # stage key -> text
    agent_instruction_files: list[str] = field(default_factory=list)   # agent names with an override

    # -- convenience accessors -------------------------------------------
    @property
    def stack(self) -> dict[str, Any]:
        ta = self.domain_pack.get("target_architecture") or {}
        return ta.get("stack") or {}

    @property
    def hard_constraints(self) -> list[str]:
        hc = self.domain_pack.get("hard_constraints") or []
        return [str(c) for c in hc]

    @property
    def config_prefix(self) -> str | None:
        return (self.domain_pack.get("project") or {}).get("config_prefix")


def _read(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig", errors="replace").strip()
    if len(text) > _MAX_INSTRUCTION_CHARS:
        text = text[:_MAX_INSTRUCTION_CHARS] + "\n\n[... truncated: keep stage overrides short ...]"
    return text


def load_project_config(workspace_root: Path, project_id: str) -> ProjectConfig:
    """Read ``workspaces/<project_id>/`` configuration. Never raises for a missing file."""
    root = Path(workspace_root) / project_id
    cfg = ProjectConfig(project_id=project_id, root=root)

    dp = root / "domain-pack.json"
    if dp.is_file():
        try:
            cfg.domain_pack = json.loads(dp.read_text(encoding="utf-8-sig"))
            cfg.domain_pack_path = dp
        except (OSError, ValueError):
            cfg.domain_pack = {}
            cfg.domain_pack_path = dp   # present but unreadable — say so in the block

    ac = root / "agent-config"
    if ac.is_dir():
        cfg.agent_config_dir = ac
        for md in sorted(ac.glob("*.md")):
            if md.name.lower() == "readme.md":
                continue
            cfg.stage_instructions[md.stem] = _read(md)
        agents_dir = ac / "agents"
        if agents_dir.is_dir():
            cfg.agent_instruction_files = sorted(p.stem for p in agents_dir.glob("*.md"))
    return cfg


def constraint_sentence(cfg: ProjectConfig | None) -> str:
    """The one-line guard appended to every output contract.

    Generic by default; when the domain pack lists hard constraints, they replace
    the generic wording so the guard names the real system rather than a fixed one.
    """
    if cfg and cfg.hard_constraints:
        return "Hard constraints (from the project domain pack): " + "; ".join(cfg.hard_constraints) + "."
    return (
        "Do not modify the existing database schema or any stored procedure of the "
        "application being modernised."
    )


def project_config_block(cfg: ProjectConfig | None, stage_key: str) -> str:
    """Markdown block injected into every stage prompt.

    Tells the agent where the facts are, what the target stack is, what the hard
    constraints are, and gives it the stage-level override verbatim.
    """
    if cfg is None:
        return ""
    lines: list[str] = ["## Project configuration", ""]

    if cfg.domain_pack_path and cfg.domain_pack:
        lines.append(f"- Domain pack (facts about this application): `{cfg.domain_pack_path.as_posix()}` — "
                     "read the blocks your agent needs before generating; never invent an application fact "
                     "that this file could hold. If a required block is missing, stop and say so.")
        if cfg.stack:
            stack = ", ".join(f"{k}={v}" for k, v in cfg.stack.items())
            lines.append(f"- Target stack: {stack}")
        if cfg.config_prefix:
            lines.append(f"- Configuration/property prefix for generated code: `{cfg.config_prefix}`")
        if cfg.hard_constraints:
            lines.append("- Hard constraints:")
            lines.extend(f"  - {c}" for c in cfg.hard_constraints)
    elif cfg.domain_pack_path:
        lines.append(f"- Domain pack `{cfg.domain_pack_path.as_posix()}` exists but could not be parsed — "
                     "report this; do not guess application facts.")
    else:
        lines.append(f"- No domain pack found under `{cfg.root.as_posix()}` — you are working from plugin "
                     "defaults only. State every application-specific assumption explicitly.")

    if cfg.agent_config_dir:
        if cfg.agent_instruction_files:
            names = ", ".join(f"`{n}`" for n in cfg.agent_instruction_files)
            lines.append(f"- Agent-level instruction overrides exist for: {names} — each sub-agent MUST read "
                         f"`{(cfg.agent_config_dir / 'agents').as_posix()}/<agent-name>.md` before generating "
                         "and follow it over its own defaults.")
        else:
            lines.append(f"- Agent-level overrides directory: `{(cfg.agent_config_dir / 'agents').as_posix()}` (none present).")

    override = cfg.stage_instructions.get(stage_key)
    if override:
        lines += ["", f"### Stage instructions for `{stage_key}` (project override — takes precedence over plugin defaults)", "", override]

    lines += ["", "Precedence: the run request > stage override > agent override > domain pack > plugin defaults."]
    return "\n".join(lines) + "\n"
