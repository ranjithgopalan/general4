"""Project intake: how a project's starting knowledge arrives.

Two categories, because they are genuinely different situations and the pipeline
behaves differently in each:

    GREENFIELD   Nothing exists yet. The only input is a requirements document,
                 so the G1 PRD has no legacy code or schema to draw on
                 and must say so rather than inferring a current state.

    BROWNFIELD   A legacy system exists. Its knowledge arrives either as a
                 reverse-engineering report (already analysed) or as a git URL
                 (not yet analysed -- the knowledge base's RED engine has to run first).

The distinction is recorded on the project rather than inferred, because "no code
knowledge found" is ambiguous otherwise: for a greenfield project that is correct
and expected, and for a brownfield one it means indexing has not finished. Treating
those the same is how a PRD ends up asserting a current state that
was never read.
"""

from __future__ import annotations

import re
from enum import Enum


class ProjectCategory(str, Enum):
    GREENFIELD = "greenfield"
    BROWNFIELD = "brownfield"


class IntakeSource(str, Enum):
    """Where the starting knowledge comes from."""

    REQUIREMENTS = "requirements"           # greenfield
    REVERSE_ENGINEERING = "reverse-engineering"  # brownfield, already analysed
    GIT_REPOSITORY = "git-repository"       # brownfield, needs analysis
    EXISTING_KB = "existing-kb"             # already indexed in the knowledge base
    RE_GRAPH = "re-graph"                   # brownfield, rules fetched live from RE Graph at PRD run time


#: Which sources each category accepts. Enforced, so a greenfield project cannot
#: arrive with a reverse-engineering report of a system that does not exist.
ALLOWED_SOURCES: dict[ProjectCategory, frozenset[IntakeSource]] = {
    ProjectCategory.GREENFIELD: frozenset({
        IntakeSource.REQUIREMENTS,
        IntakeSource.EXISTING_KB,
    }),
    ProjectCategory.BROWNFIELD: frozenset({
        IntakeSource.REVERSE_ENGINEERING,
        IntakeSource.GIT_REPOSITORY,
        IntakeSource.EXISTING_KB,
        IntakeSource.RE_GRAPH,
    }),
}

#: KB authority tier per source. A tier is a claim about how much the content
#: can be trusted, so it has to differ by provenance:
#:
#:   B  a git repository is the source itself -- the highest tier this service can
#:      legitimately claim, because nothing has interpreted it
#:   C  a reverse-engineering report or a requirements document is documentary
#:      evidence, derived but produced against something real
#:   D  reserved for LLM output with no external verification (never used here)
AUTHORITY_TIER: dict[IntakeSource, str] = {
    IntakeSource.GIT_REPOSITORY: "B",
    IntakeSource.REVERSE_ENGINEERING: "C",
    IntakeSource.REQUIREMENTS: "C",
    IntakeSource.EXISTING_KB: "C",
    IntakeSource.RE_GRAPH: "C",
}

#: What the source is called in the KB's `source_type` vocabulary.
SOURCE_TYPE: dict[IntakeSource, str] = {
    IntakeSource.REQUIREMENTS: "SPEC",
    IntakeSource.REVERSE_ENGINEERING: "SPEC",
    IntakeSource.GIT_REPOSITORY: "CODE",
    IntakeSource.EXISTING_KB: "SPEC",
    IntakeSource.RE_GRAPH: "SPEC",
}


#: Which sources make the project's identity live in the knowledge base.
#:
#: Only `existing-kb` does. It *means* "work against knowledge already indexed
#: there", so a name that does not resolve is a genuine error -- there would be
#: nothing to read.
#:
#: The other three bring their own knowledge: a requirements document, a
#: reverse-engineering report, or a repository still to be analysed. Requiring a
#: catalogue entry for those made greenfield onboarding impossible, since a
#: greenfield project has no legacy system and therefore can never appear in a
#: reverse-engineering catalogue. Resolution is still *attempted* for them, so a
#: project that does happen to exist there gets linked instead of duplicated.
REQUIRES_KB_APPLICATION: frozenset[IntakeSource] = frozenset({
    IntakeSource.EXISTING_KB,
})


class IntakeError(ValueError):
    """The intake request is not usable. Surfaced as 422."""


def validate(category: ProjectCategory, source: IntakeSource) -> None:
    allowed = ALLOWED_SOURCES[category]
    if source not in allowed:
        raise IntakeError(
            f"a {category.value} project cannot be onboarded from "
            f"'{source.value}'. Allowed: {', '.join(sorted(s.value for s in allowed))}. "
            + (
                "A greenfield project has no legacy system to reverse-engineer."
                if category is ProjectCategory.GREENFIELD
                else "A brownfield project needs its existing system indexed."
            )
        )


#: Deliberately strict. A URL is handed to a clone, so an unrecognised scheme is
#: refused rather than passed through -- `file://`, `ext::` and `ssh://` with a
#: command payload are all ways to turn a clone into code execution.
_GIT_URL = re.compile(
    r"^(?:https://|git@)"                 # https or scp-style ssh only
    r"[A-Za-z0-9._~-]+(?::\d+)?"          # host, optional port
    r"[/:][A-Za-z0-9._~/-]+?"             # path
    r"(?:\.git)?/?$"
)


def normalise_git_url(raw: str) -> str:
    """Validate and tidy a repository URL, or raise IntakeError.

    Rejects anything that is not https or scp-style ssh. A clone runs code from
    the remote in some configurations, so the scheme is an allowlist rather than a
    blocklist.
    """
    url = (raw or "").strip()
    if not url:
        raise IntakeError("a git URL is required")
    if len(url) > 2048:
        raise IntakeError("that git URL is implausibly long")
    if any(c in url for c in ("\n", "\r", "\t", " ")):
        raise IntakeError("a git URL cannot contain whitespace")

    # Checked BEFORE the pattern: `https://token@host/org/repo.git` fails the
    # pattern too, and "not an https or ssh git URL" would send someone hunting a
    # typo in a URL whose only problem is the embedded secret.
    if url.startswith("https://") and "@" in url.split("/", 3)[2]:
        raise IntakeError(
            "remove the credentials from the URL. Use a deploy key or a "
            "credential helper -- an embedded token would be written to logs and "
            "to the KB source record."
        )

    if not _GIT_URL.match(url):
        raise IntakeError(
            f"'{url}' is not an https or ssh git URL. Expected forms: "
            "https://host/org/repo.git or git@host:org/repo.git. "
            "Other schemes are refused because cloning executes remote "
            "configuration in some setups."
        )
    return url


def describe(category: ProjectCategory, source: IntakeSource) -> str:
    """One line for the UI and the audit record."""
    if source is IntakeSource.GIT_REPOSITORY:
        return (
            "Brownfield from a git repository. the knowledge base's RED engine must analyse it "
            "before the PRD can read code knowledge."
        )
    if source is IntakeSource.REVERSE_ENGINEERING:
        return (
            "Brownfield from a reverse-engineering report. Already analysed, so "
            "the PRD can read it immediately."
        )
    if source is IntakeSource.REQUIREMENTS:
        return (
            "Greenfield from a requirements document. There is no legacy system, "
            "so the PRD will report no current state rather than infer one."
        )
    if source is IntakeSource.RE_GRAPH:
        return (
            "Brownfield via RE Graph. Business rules are fetched live from the RE Graph "
            "API when the PRD stage runs — no document upload required at onboarding."
        )
    return "Onboarded against knowledge already indexed in the knowledge base."
