#!/usr/bin/env python
"""Vendor the GATHER plugins this service needs into `plugins/`.

The service used to load plugins straight out of a sibling genlite checkout via
an absolute `GENLITE_PLUGIN_ROOT`. That made the service unrunnable on any
machine without that checkout at that exact path, and left no record of which
plugin revision a run was made against.

Vendored copies are kept **byte-identical to upstream** -- this script only
copies, never edits. Fixing a name mismatch belongs in the pipeline YAML, not in
a divergent copy, or the next sync silently reverts it.

    python scripts/sync-plugins.py --source C:/CTS_Git/general/genlite/plugin
    python scripts/sync-plugins.py --check          # verify, change nothing

Excluded on purpose: GATHER-lambda (Node on Lambda, DISCARD in the fit
analysis), GATHER-telemetry (not on disk anywhere), and GATHER-code /
GATHER-opa / GATHER-help (no stage in either pipeline owns them).
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).resolve().parents[3]  # app/agentic_platform/scripts -> repo root
DEFAULT_SOURCE = Path("../genai-aig-core-aidlc-platform-plugins-7419/plugins")
DEST = SERVICE_ROOT / "app" / "agentic_platform" / "plugins"

#: All plugins required by the AIDLC pipeline tiers (Global, Mini, Assembler).
REQUIRED = (
    "AIDLC-axis",
    "AIDLC-business-analyst",
    "AIDLC-db",
    "AIDLC-design",
    "AIDLC-docs",
    "AIDLC-integrator",
    "AIDLC-kb",
    "AIDLC-playwright",
    "AIDLC-runner",
    "AIDLC-scrum-master",
    "AIDLC-security",
    "AIDLC-springboot",
    "AIDLC-tech-architect",
    "AIDLC-validate",
    "fe-develop",
)

#: Installed packages and scratch output are bulk with no bearing on a run.
#: `__tests__` IS copied -- those are the plugins' own acceptance cases, and a
#: vendored copy that cannot be tested is not a faithful mirror. ~4.8 MB of the
#: total.
IGNORE = shutil.ignore_patterns("node_modules", "__pycache__", "*.pyc",
                                ".git", ".scratch")


def _manifest(plugin_dir: Path) -> dict:
    path = plugin_dir / ".claude-plugin" / "plugin.json"
    if not path.is_file():
        raise FileNotFoundError(f"{plugin_dir.name}: no .claude-plugin/plugin.json")
    return json.loads(path.read_text(encoding="utf-8"))


def verify(plugin_dir: Path) -> list[str]:
    """Problems that would make this plugin fail to load. Empty means good."""
    problems: list[str] = []
    try:
        data = _manifest(plugin_dir)
    except Exception as exc:  # noqa: BLE001
        return [f"{plugin_dir.name}: {exc}"]

    if data.get("name") != plugin_dir.name:
        problems.append(
            f"{plugin_dir.name}: manifest name is {data.get('name')!r}"
        )
    # A skill declared but absent loads as nothing, and the stage that invokes it
    # fails only once the agent is already running.
    for entry in data.get("skills") or []:
        skill_dir = plugin_dir / Path(str(entry).lstrip("./"))
        if not (skill_dir / "SKILL.md").is_file():
            problems.append(f"{plugin_dir.name}: {entry} has no SKILL.md")
    for entry in data.get("agents") or []:
        if not (plugin_dir / Path(str(entry).lstrip("./"))).is_file():
            problems.append(f"{plugin_dir.name}: agent {entry} is missing")
    return problems


def sync(source: Path) -> int:
    if not source.is_dir():
        print(f"  source not found: {source}")
        return 2

    missing = [n for n in REQUIRED if not (source / n).is_dir()]
    if missing:
        print(f"  not in source: {', '.join(missing)}")
        return 2

    DEST.mkdir(exist_ok=True)
    for name in REQUIRED:
        target = DEST / name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source / name, target, ignore=IGNORE)
        print(f"  copied {name}")
    return 0


def check() -> int:
    if not DEST.is_dir():
        print(f"  {DEST} does not exist -- run without --check first")
        return 1

    problems: list[str] = []
    for name in REQUIRED:
        plugin_dir = DEST / name
        if not plugin_dir.is_dir():
            problems.append(f"{name}: not vendored")
            continue
        problems.extend(verify(plugin_dir))

    if problems:
        print(f"\n  {len(problems)} problem(s):")
        for p in problems:
            print(f"    {p}")
        return 1
    print(f"\n  {len(REQUIRED)} plugin(s) vendored and valid: {DEST}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                       help=f"genlite plugin root (default: {DEFAULT_SOURCE})")
    parser.add_argument("--check", action="store_true",
                       help="verify the vendored copy without changing it")
    args = parser.parse_args()

    if args.check:
        return check()
    rc = sync(args.source)
    return rc or check()


if __name__ == "__main__":
    sys.exit(main())
