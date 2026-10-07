"""Source language and target framework, and the agents each implies.

A brownfield project is a translation: *from* something *to* something. Both ends
change which agents can do the work, so recording them is not metadata — it
decides whether the configured pipeline can run at all.

The check this enables is the useful part. A project targeting Angular + FastAPI
cannot be built by a pipeline whose API stage owns `AIDLC-springboot`, and the
failure would otherwise appear eight stages later as Java arriving in a Python
service. `required_plugins()` states what a target needs; the registry compares it
with what the pipeline actually declares.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SourceLanguage(str, Enum):
    """What the legacy system is written in."""

    VB6_VBA = "vb6-vba"
    VBSCRIPT_ASP = "vbscript-asp"
    MAINFRAME = "mainframe"


class TargetFramework(str, Enum):
    """What it becomes. UI and API are named together because the UI generator
    targets the API's contract, so they cannot be chosen independently."""

    ANGULAR_SPRINGBOOT = "angular-springboot"
    ANGULAR_FASTAPI = "angular-fastapi"


@dataclass(frozen=True)
class SourceProfile:
    label: str
    extensions: tuple[str, ...]
    note: str
    #: Whether the reverse-engineering engine can parse it today. Recorded because
    #: "no code knowledge" then has a known cause instead of looking like an
    #: indexing failure.
    analysable: bool = True


SOURCES: dict[SourceLanguage, SourceProfile] = {
    SourceLanguage.VB6_VBA: SourceProfile(
        label="VB6 / VBA",
        extensions=(".frm", ".bas", ".cls", ".vbp", ".vba"),
        note=(
            "Forms carry both UI and business logic, so screen layout and rules "
            "have to be separated during analysis rather than after it."
        ),
    ),
    SourceLanguage.VBSCRIPT_ASP: SourceProfile(
        label="VB Script / ASP",
        extensions=(".asp", ".asa", ".inc", ".vbs"),
        note=(
            "Inline SQL and includes are the norm, so data access is scattered "
            "across pages rather than isolated in a layer."
        ),
    ),
    SourceLanguage.MAINFRAME: SourceProfile(
        label="Mainframe (COBOL / JCL / CICS)",
        extensions=(".cbl", ".cob", ".cpy", ".jcl"),
        note=(
            "Copybooks define the data contract, and batch job flow lives in JCL "
            "rather than in the programs. Both must be read to size a change."
        ),
    ),
}


@dataclass(frozen=True)
class TargetProfile:
    label: str
    ui: str
    api: str
    #: GATHER plugins that must exist for the code stages to run.
    plugins: frozenset[str]
    #: Which design agent produces the service-tier TDD.
    tdd_agent: str
    note: str


TARGETS: dict[TargetFramework, TargetProfile] = {
    TargetFramework.ANGULAR_SPRINGBOOT: TargetProfile(
        label="Angular + Spring Boot",
        ui="Angular 18 on Axis v18.0.32",
        api="Java Spring Boot",
        plugins=frozenset({"AIDLC-axis", "AIDLC-springboot"}),
        tdd_agent="springboot-tdd-designer",
        note=(
            "Reaches existing logic through stored-procedure DAOs, so the database "
            "schema stays unmodified."
        ),
    ),
    TargetFramework.ANGULAR_FASTAPI: TargetProfile(
        label="Angular + FastAPI",
        ui="Angular 18 on Axis v18.0.32",
        api="Python FastAPI",
        plugins=frozenset({"AIDLC-axis", "AIDLC-fastapi"}),
        tdd_agent="fastapi-tdd-designer",
        note=(
            "No FastAPI generator exists yet: AIDLC-fastapi and its TDD designer "
            "have to be built before the API stages can run."
        ),
    ),
}


def required_plugins(target: TargetFramework) -> frozenset[str]:
    return TARGETS[target].plugins


def describe(source: SourceLanguage | None,
             target: TargetFramework | None) -> str:
    """One line for the UI and the audit record."""
    parts: list[str] = []
    if source is not None:
        parts.append(f"From {SOURCES[source].label}.")
    if target is not None:
        profile = TARGETS[target]
        parts.append(f"To {profile.ui} and {profile.api}.")
        parts.append(profile.note)
    return " ".join(parts) or "No source or target recorded."


def alignment(target: TargetFramework, pipeline_plugins: set[str]) -> list[str]:
    """Plugins the target needs that the pipeline does not declare.

    Empty means the configured pipeline can build this target. Anything returned
    is a stage that will fail -- reported at onboarding rather than eight stages
    later, when the wrong language has already been generated.
    """
    return sorted(required_plugins(target) - set(pipeline_plugins))
