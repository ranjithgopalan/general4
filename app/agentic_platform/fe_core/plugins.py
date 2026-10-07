"""GATHER plugin discovery and load verification.

Why this module exists (PRD FR-6, R-4)
--------------------------------------
The Claude Agent SDK **silently skips a plugin whose path does not exist**.
There is no exception and no warning -- the session simply proceeds without
the skill, and the stage produces plausible-looking output generated without
the plugin's rules. That failure is indistinguishable from success unless you
check, so every stage run asserts against the `init` system message before any
work is trusted.

The genlite repo has a track record here: four artefacts were sitting on disk
unregistered in their manifests and had stopped loading entirely.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


class PluginLoadError(RuntimeError):
    """A required plugin or skill did not load into the agent session."""


@dataclass(frozen=True)
class GatherPlugin:
    name: str
    path: Path
    version: str | None = None
    skills: tuple[str, ...] = ()
    agents: tuple[str, ...] = ()

    def sdk_config(self) -> dict[str, str]:
        """The `plugins=[...]` entry the SDK expects. `type` must be 'local'."""
        return {"type": "local", "path": str(self.path)}

    def qualified_skill(self, skill: str) -> str:
        """Plugin skills are namespaced `plugin-name:skill-name`."""
        return f"{self.name}:{skill}"


def _read_manifest(plugin_dir: Path) -> dict[str, Any]:
    manifest = plugin_dir / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Invalid plugin.json in %s: %s", plugin_dir, exc)
        return {}


def load_plugin(plugin_dir: Path) -> GatherPlugin:
    data = _read_manifest(plugin_dir)
    skills = tuple(
        Path(s.rstrip("/")).name for s in data.get("skills", [])
    )
    agents = tuple(
        Path(a).stem for a in data.get("agents", [])
    )
    return GatherPlugin(
        name=data.get("name") or plugin_dir.name,
        path=plugin_dir,
        version=data.get("version"),
        skills=skills,
        agents=agents,
    )


def discover(plugin_root: Path) -> dict[str, GatherPlugin]:
    """Discover every GATHER plugin under `plugin_root`, keyed by name."""
    if not plugin_root.is_dir():
        raise PluginLoadError(
            f"GENLITE_PLUGIN_ROOT does not exist: {plugin_root}. "
            "Point it at <genlite>/plugin (singular -- the marketplace manifest "
            "historically said './plugins', which does not resolve)."
        )
    found: dict[str, GatherPlugin] = {}
    for child in sorted(plugin_root.iterdir()):
        if child.is_dir() and not child.name.startswith("."):
            plugin = load_plugin(child)
            found[plugin.name] = plugin
    if not found:
        raise PluginLoadError(f"No plugins found under {plugin_root}")
    logger.info("Discovered %d GATHER plugins under %s", len(found), plugin_root)
    return found


def select(
    available: dict[str, GatherPlugin], required: Iterable[str]
) -> list[GatherPlugin]:
    """Resolve required plugin names, failing loudly on anything missing."""
    required = list(required)
    missing = [name for name in required if name not in available]
    if missing:
        raise PluginLoadError(
            f"Required plugin(s) not found on disk: {', '.join(sorted(missing))}. "
            f"Available: {', '.join(sorted(available))}"
        )
    return [available[name] for name in required]


# ---------------------------------------------------------------------------
# init-message verification
# ---------------------------------------------------------------------------

@dataclass
class InitReport:
    """What the SDK actually loaded, parsed from the `init` system message."""

    plugins: list[str] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    slash_commands: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_system_message(cls, message: Any) -> "InitReport | None":
        """Parse an SDK `SystemMessage` with subtype 'init'.

        Duck-typed rather than isinstance-checked so this works under both the
        real SDK and the knowledge base's CLI shim types.
        """
        if getattr(message, "subtype", None) != "init":
            return None
        data = getattr(message, "data", None) or {}
        if not isinstance(data, dict):
            return None
        plugins = [
            p.get("name", "") if isinstance(p, dict) else str(p)
            for p in (data.get("plugins") or [])
        ]
        return cls(
            plugins=[p for p in plugins if p],
            skills=list(data.get("skills") or []),
            slash_commands=list(data.get("slash_commands") or []),
            raw=data,
        )


def verify_loaded(
    report: InitReport | None,
    required_plugins: Iterable[str],
    required_skills: Iterable[str] = (),
) -> None:
    """Raise unless every required plugin and namespaced skill actually loaded.

    `required_skills` entries must already be namespaced (`GATHER-axis:axis-implement`).
    """
    required_plugins = list(required_plugins)
    required_skills = list(required_skills)

    if report is None:
        raise PluginLoadError(
            "No `init` system message was observed, so plugin loading could not "
            "be verified. Refusing to trust this run: the SDK skips missing "
            "plugin paths silently."
        )

    missing_plugins = [p for p in required_plugins if p not in report.plugins]
    if missing_plugins:
        raise PluginLoadError(
            f"Plugin(s) did not load: {', '.join(sorted(missing_plugins))}. "
            f"Loaded: {', '.join(sorted(report.plugins)) or '(none)'}. "
            "Most likely the plugin path does not exist -- the SDK skips those "
            "silently."
        )

    known = set(report.skills) | set(report.slash_commands)
    missing_skills = [s for s in required_skills if s not in known]
    if missing_skills:
        raise PluginLoadError(
            f"Skill(s) did not load: {', '.join(sorted(missing_skills))}. "
            f"Loaded skills: {', '.join(sorted(report.skills)) or '(none)'}. "
            "Check that each is skills/<name>/SKILL.md and is declared in "
            "plugin.json (run genlite/scripts/validate-plugins.py)."
        )

    logger.info(
        "Verified %d plugin(s) and %d skill(s) loaded",
        len(required_plugins), len(required_skills),
    )
