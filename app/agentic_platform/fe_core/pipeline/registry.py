"""Declarative pipeline registry.

Pipelines are YAML under `pipelines/`. Adding or reordering a stage is a data
change, not a code change (FR-18) -- unlike the knowledge base, where the stage vocabulary is
hardcoded across several dicts, and unlike ADLC, where 7 tier names are spread
over four dicts plus the RBAC graphs.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import ValidationError

from app.agentic_platform.fe_core.config import get_settings
from app.agentic_platform.fe_core.pipeline.models import Pipeline

logger = logging.getLogger(__name__)


class PipelineNotFoundError(KeyError):
    pass


class PipelineInvalidError(ValueError):
    pass


def load_pipeline_file(path: Path) -> Pipeline:
    if not path.is_file():
        raise PipelineNotFoundError(f"pipeline file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    except yaml.YAMLError as exc:
        raise PipelineInvalidError(f"{path.name}: invalid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise PipelineInvalidError(f"{path.name}: expected a mapping at the top level")
    try:
        return Pipeline.model_validate(raw)
    except ValidationError as exc:
        raise PipelineInvalidError(f"{path.name}: {exc}") from exc


def discover_pipelines(pipeline_dir: Path) -> dict[str, Pipeline]:
    if not pipeline_dir.is_dir():
        raise PipelineNotFoundError(f"pipeline directory not found: {pipeline_dir}")
    found: dict[str, Pipeline] = {}
    for path in sorted(pipeline_dir.glob("*.yaml")) + sorted(pipeline_dir.glob("*.yml")):
        pipeline = load_pipeline_file(path)
        if pipeline.name in found:
            raise PipelineInvalidError(
                f"duplicate pipeline name '{pipeline.name}' in {path.name}"
            )
        found[pipeline.name] = pipeline
    if not found:
        raise PipelineNotFoundError(f"no pipeline YAML found in {pipeline_dir}")
    _validate_tier_links(found)
    logger.info(
        "Loaded %d pipeline(s) from %s: %s",
        len(found), pipeline_dir, ", ".join(sorted(found)),
    )
    return found


def _validate_tier_links(found: dict[str, Pipeline]) -> None:
    """Cross-pipeline checks that a single Pipeline cannot make about itself.

    A Mini pipeline declares `inherited_inputs` because its stages consume
    artefacts produced in the Global tier. Checking each entry against the
    parent's actual `produces` turns a silent "this stage will never become
    ready" into a load-time error -- the failure mode otherwise appears as a
    stage stuck at not_ready with no explanation.
    """
    # Architecture pipelines indexed by the Global pipeline they extend, so a
    # Global pipeline can validate its arch_inputs and a Mini can inherit the
    # Architecture Workspace's outputs.
    arch_by_parent: dict[str, Pipeline] = {
        p.parent: p for p in found.values()
        if p.tier == "architecture" and p.parent
    }

    for pipeline in found.values():
        if not pipeline.parent:
            # Global pipeline: its arch_inputs must be produced by the paired
            # architecture pipeline (if any is declared).
            if pipeline.arch_inputs:
                arch = arch_by_parent.get(pipeline.name)
                if arch is None:
                    raise PipelineInvalidError(
                        f"pipeline '{pipeline.name}' declares arch_inputs "
                        f"{pipeline.arch_inputs} but no architecture pipeline names "
                        f"it as parent"
                    )
                arch_produced = {t for s in arch.stages for t in s.produces}
                bad = [t for t in pipeline.arch_inputs if t not in arch_produced]
                if bad:
                    raise PipelineInvalidError(
                        f"pipeline '{pipeline.name}' arch_inputs {bad} are not "
                        f"produced by architecture pipeline '{arch.name}'. Produced "
                        f"there: {sorted(arch_produced)}"
                    )
            continue

        parent = found.get(pipeline.parent)
        if parent is None:
            raise PipelineInvalidError(
                f"pipeline '{pipeline.name}' names parent '{pipeline.parent}', "
                f"which does not exist. Available: {', '.join(sorted(found))}"
            )
        if parent.tier != "global":
            raise PipelineInvalidError(
                f"pipeline '{pipeline.name}' has parent '{parent.name}', which is "
                f"tier '{parent.tier}'; a parent must be tier 'global'"
            )
        global_produced = {t for stage in parent.stages for t in stage.produces}

        if pipeline.tier == "architecture":
            stage_keys = {s.key for s in parent.stages}
            if pipeline.arch_after not in stage_keys:
                raise PipelineInvalidError(
                    f"architecture pipeline '{pipeline.name}' arch_after="
                    f"'{pipeline.arch_after}' is not a stage of parent '{parent.name}'. "
                    f"Stages: {sorted(stage_keys)}"
                )
            missing = [t for t in pipeline.inherited_inputs if t not in global_produced]
            if missing:
                raise PipelineInvalidError(
                    f"architecture pipeline '{pipeline.name}' inherits {missing}, "
                    f"which parent '{parent.name}' never produces. Produced there: "
                    f"{sorted(global_produced)}"
                )
            continue

        # Mini pipeline: inherits from Global and/or the Architecture Workspace.
        arch = arch_by_parent.get(pipeline.parent)
        arch_produced = {t for s in arch.stages for t in s.produces} if arch else set()
        available = global_produced | arch_produced
        missing = [t for t in pipeline.inherited_inputs if t not in available]
        if missing:
            raise PipelineInvalidError(
                f"pipeline '{pipeline.name}' inherits {missing}, which neither parent "
                f"'{parent.name}' nor its architecture workspace produces. Available: "
                f"{sorted(available)}"
            )


def pipelines_for_tier(tier: str) -> list[Pipeline]:
    return [p for p in registry().values() if p.tier == tier]


def global_pipeline() -> Pipeline:
    return get_pipeline(get_settings().fe_pipeline_global)


def mini_pipeline() -> Pipeline:
    return get_pipeline(get_settings().fe_pipeline_mini)


def architecture_pipeline() -> Pipeline | None:
    """The Architecture pipeline if configured and loaded, else None.

    None is a valid state: a deployment without an architecture pipeline keeps
    the two-tier Global -> Mini behaviour and the Global graph skips arch_sync_gate.
    """
    name = getattr(get_settings(), "fe_pipeline_architecture", None)
    if not name:
        return None
    return registry().get(name)


@lru_cache
def registry() -> dict[str, Pipeline]:
    return discover_pipelines(get_settings().fe_pipeline_dir)


def get_pipeline(name: str | None = None) -> Pipeline:
    name = name or get_settings().fe_pipeline
    pipelines = registry()
    if name not in pipelines:
        raise PipelineNotFoundError(
            f"unknown pipeline '{name}'. Available: {', '.join(sorted(pipelines))}"
        )
    return pipelines[name]


def reset_registry() -> None:
    """Test hook."""
    registry.cache_clear()
