"""The eight personas, and who may approve what (PRD 5.1, FR-P3).

Two words in the PRD both mean "workspace", on different axes, and conflating
them is the easiest way to get authorisation wrong:

    AccessScope    what a person may SEE      -> global (no code) | hybrid | admin
    WorkspaceTier  which INSTANCE they are in -> global (one) | mini (one per EPIC)

An Architect sits in the Global *access scope* (no code) while working inside a
Mini *workspace tier* on EPIC 7. Both statements are true at once, so they are
kept as separate types rather than one overloaded enum.

FR-P3 is enforced through artefact tiers rather than a per-stage role list: the
PRD's "Approves" column is written in terms of artefact kinds ("Requirement-tier
artefacts", "Code at the human gate"), and a list keyed by stage would drift the
moment a stage is added.
"""

from __future__ import annotations

from enum import Enum


class AccessScope(str, Enum):
    """What a principal may see. Unchanged in meaning from FR-P1."""

    GLOBAL = "global"    # documents only -- never code
    HYBRID = "hybrid"    # documents and code
    ADMIN = "admin"      # administration; still cannot bypass approvals


class WorkspaceTier(str, Enum):
    """Which workspace instance an artefact belongs to (PRD 5.3)."""

    GLOBAL = "global"              # exactly one per programme; ends at the EPIC set
    ARCHITECTURE = "architecture"  # singleton per programme; ADR -> NFR -> SDD -> SRD
    MINI = "mini"                  # one per EPIC; ten EPICs means ten of these


class Persona(str, Enum):
    """The eight personas of PRD 5.1.

    Every Mini Workspace admits all eight, so this is not a per-tier list.
    """

    PRODUCT_OWNER = "product-owner"
    BUSINESS_ANALYST = "business-analyst"
    SCRUM_MASTER = "scrum-master"
    ARCHITECT = "architect"
    DEVELOPER = "developer"
    TESTER = "tester"
    DEVOPS = "devops"
    UAT = "uat"


class SystemRole(str, Enum):
    """Identities that are not personas.

    Kept out of `Persona` so that "all eight personas join every workspace" stays
    literally true in code, and so no system identity can satisfy FR-P3.
    """

    ADMINISTRATOR = "administrator"
    SERVICE = "service"          # the worker's own identity


class ArtifactTier(str, Enum):
    """What kind of thing an artefact is, for approval purposes.

    Drawn from the "Approves" column of PRD 5.1.
    """

    VALUE = "value"              # PRD, EPIC set -- Product Owner
    REQUIREMENT = "requirement"  # Impact Analysis, FRD -- Business Analyst
    BACKLOG = "backlog"          # Feature, Story -- Scrum Master
    DESIGN = "design"            # ADR, NFR, SDD, SRD, LLD/TDD -- Architect
    CODE = "code"                # UI, API, DB -- Developer
    TEST = "test"                # API, smoke, E2E results -- Tester
    RELEASE = "release"          # pipeline, image, deploy -- DevOps
    ACCEPTANCE = "acceptance"    # UAT sign-off -- UAT


#: Which access scope each persona works in (PRD 5.2).
#:
#: Architect is Global: the PRD gives the architect design ownership but not
#: repository browsing. UAT is Hybrid because accepting an EPIC means reading
#: test evidence, which is a code-tier artefact.
PERSONA_SCOPE: dict[Persona, AccessScope] = {
    Persona.PRODUCT_OWNER: AccessScope.GLOBAL,
    Persona.BUSINESS_ANALYST: AccessScope.GLOBAL,
    Persona.SCRUM_MASTER: AccessScope.GLOBAL,
    Persona.ARCHITECT: AccessScope.GLOBAL,
    Persona.DEVELOPER: AccessScope.HYBRID,
    Persona.TESTER: AccessScope.HYBRID,
    Persona.DEVOPS: AccessScope.HYBRID,
    Persona.UAT: AccessScope.HYBRID,
}

#: FR-P3: which artefact tiers each persona may approve.
#:
#: Product Owner holds ACCEPTANCE alongside UAT because PRD 5.1 gives them
#: "UAT exit"; the UAT persona accepts per EPIC, the PO closes it out.
PERSONA_APPROVES: dict[Persona, frozenset[ArtifactTier]] = {
    Persona.PRODUCT_OWNER: frozenset({ArtifactTier.VALUE, ArtifactTier.ACCEPTANCE}),
    Persona.BUSINESS_ANALYST: frozenset({ArtifactTier.REQUIREMENT}),
    Persona.SCRUM_MASTER: frozenset({ArtifactTier.BACKLOG}),
    Persona.ARCHITECT: frozenset({ArtifactTier.DESIGN}),
    Persona.DEVELOPER: frozenset({ArtifactTier.CODE}),
    Persona.TESTER: frozenset({ArtifactTier.TEST}),
    Persona.DEVOPS: frozenset({ArtifactTier.RELEASE}),
    Persona.UAT: frozenset({ArtifactTier.ACCEPTANCE}),
}

#: Okta groups configured against the previous role names keep working. Without
#: this, renaming the model would silently strip every existing user's access --
#: which fails closed, but fails closed at 3am on the day of the cutover.
LEGACY_ROLE_ALIASES: dict[str, Persona] = {
    "business-owner": Persona.PRODUCT_OWNER,
    "technical-architect": Persona.ARCHITECT,
    "tech-architect": Persona.ARCHITECT,
    "development-lead": Persona.DEVELOPER,
    "dev-lead": Persona.DEVELOPER,
    "qa-lead": Persona.TESTER,
    "qa": Persona.TESTER,
    "security-owner": Persona.DEVOPS,
    "business-analyst-lead": Persona.BUSINESS_ANALYST,
    "ba": Persona.BUSINESS_ANALYST,
    "po": Persona.PRODUCT_OWNER,
}

#: Every name a pipeline may legally put in `approval_persona`.
KNOWN_APPROVAL_PERSONAS = frozenset(p.value for p in Persona)


def persona_from_name(name: str) -> Persona | None:
    """Resolve a persona from a claim, alias or pipeline field. Fails closed."""
    candidate = str(name or "").strip().lower().replace("_", "-")
    if not candidate:
        return None
    for prefix in ("uwcr-", "scope-", "app-", "role-", "persona-"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
    try:
        return Persona(candidate)
    except ValueError:
        return LEGACY_ROLE_ALIASES.get(candidate)


def system_role_from_name(name: str) -> SystemRole | None:
    candidate = str(name or "").strip().lower().replace("_", "-")
    for prefix in ("uwcr-", "scope-", "app-", "role-"):
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix):]
    try:
        return SystemRole(candidate)
    except ValueError:
        return None


def may_approve_tier(persona: Persona, tier: ArtifactTier) -> bool:
    return tier in PERSONA_APPROVES.get(persona, frozenset())


def owners_of(tier: ArtifactTier) -> frozenset[Persona]:
    """Which personas may approve this tier. Used to explain a 403."""
    return frozenset(p for p, tiers in PERSONA_APPROVES.items() if tier in tiers)
