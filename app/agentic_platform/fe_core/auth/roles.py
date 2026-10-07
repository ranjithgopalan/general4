"""Principals, code visibility (FR-P1) and approval authority (FR-P3).

The persona model lives in `personas.py`; this module is the authenticated caller
and the two authorisation questions asked of it:

    can_see_code      FR-P1 -- Global personas never see code artefacts
    may_approve_tier  FR-P3 -- a persona may only approve tiers it owns

Both are enforced server-side, on the artefact, not by hiding buttons. the knowledge base's
`/fabric/*` surface has no auth at all and returns a mock admin when auth is
disabled, so a UI-only restriction here would be no restriction: anyone could
read a code artefact by calling the API directly.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agentic_platform.fe_core.auth.personas import (
    ArtifactTier,
    AccessScope,
    Persona,
    PERSONA_APPROVES,
    PERSONA_SCOPE,
    SystemRole,
    WorkspaceTier,
    may_approve_tier,
    owners_of,
    persona_from_name,
    system_role_from_name,
)

__all__ = [
    "AccessScope", "ArtifactTier", "Persona", "SystemRole", "WorkspaceTier",
    "Principal", "CODE_ARTIFACT_TYPES", "CODE_TARGETS", "ARTIFACT_TIERS",
    "code_artifact", "tier_of_artifact", "personas_from_claims", "owners_of",
]

#: Stage targets whose artefacts are code. A Global persona may not read these.
CODE_TARGETS = frozenset({"ui", "api", "tests"})

#: Artefact types that are code even when their stage target does not say so.
CODE_ARTIFACT_TYPES = frozenset({
    "ui-source", "ui-unit-tests", "api-source", "api-data-access", "api-tests",
    "ui-smoke-results", "e2e-results", "flyway-baseline",
    "api-messaging", "api-batch", "api-integration", "build-config",
})

#: Artefact type -> tier, for FR-P3. A type absent from here falls back to the
#: stage's declared tier; an artefact with neither is unapprovable, which is the
#: safe direction.
ARTIFACT_TIERS: dict[str, ArtifactTier] = {
    # Global workspace -- the programme-level ladder (PRD 8.1)
    "impact-analysis": ArtifactTier.REQUIREMENT,
    "prd": ArtifactTier.VALUE,
    "brd": ArtifactTier.REQUIREMENT,
    "frd": ArtifactTier.REQUIREMENT,
    "business-rules": ArtifactTier.REQUIREMENT,
    "adr": ArtifactTier.DESIGN,
    "nfr": ArtifactTier.DESIGN,
    "sdd": ArtifactTier.DESIGN,
    "srd": ArtifactTier.DESIGN,
    "architecture-diagram": ArtifactTier.DESIGN,
    "epic": ArtifactTier.VALUE,
    # Mini workspace -- per EPIC (PRD 8.2)
    "feature": ArtifactTier.BACKLOG,
    "user-story": ArtifactTier.BACKLOG,
    "feature-tdd": ArtifactTier.DESIGN,
    "ui-tdd": ArtifactTier.DESIGN,
    "service-tdd": ArtifactTier.DESIGN,
    "lld": ArtifactTier.DESIGN,
    "coverage-report": ArtifactTier.REQUIREMENT,
    "ui-source": ArtifactTier.CODE,
    "ui-unit-tests": ArtifactTier.CODE,
    "api-source": ArtifactTier.CODE,
    "api-openapi": ArtifactTier.CODE,
    "api-data-access": ArtifactTier.CODE,
    "flyway-baseline": ArtifactTier.CODE,
    "api-messaging": ArtifactTier.CODE,      # SQS/SNS/SES listeners, publishers, templates (band 6)
    "api-batch": ArtifactTier.CODE,          # Spring Batch jobs + schedules (band 9)
    "api-integration": ArtifactTier.CODE,    # S3 document service, report exports, feed handlers (bands 4/8)
    "build-config": ArtifactTier.CODE,       # pom, Dockerfile, Jenkinsfile -> ECR -> ECS (band 7)
    "schema-snapshot": ArtifactTier.DESIGN,
    "security-report": ArtifactTier.RELEASE,
    "opa-policies": ArtifactTier.RELEASE,
    "api-tests": ArtifactTier.TEST,
    "ui-smoke-results": ArtifactTier.TEST,
    "e2e-results": ArtifactTier.TEST,
    "documentation": ArtifactTier.RELEASE,
    "uat-signoff": ArtifactTier.ACCEPTANCE,
    "deployment": ArtifactTier.RELEASE,
}


def tier_of_artifact(artifact_type: str,
                     declared: str | ArtifactTier | None = None
                     ) -> ArtifactTier | None:
    """Tier for an artefact type, preferring the pipeline's declaration.

    The YAML wins so that adding an artefact type does not require editing this
    module (FR-18); this table is the fallback and the cross-check.
    """
    if declared is not None:
        try:
            return declared if isinstance(declared, ArtifactTier) else ArtifactTier(
                str(declared).strip().lower())
        except ValueError:
            pass
    return ARTIFACT_TIERS.get(artifact_type)


def code_artifact(artifact_type: str, target: str | None = None) -> bool:
    """Whether an artefact counts as code for FR-P1 purposes."""
    if artifact_type in CODE_ARTIFACT_TYPES:
        return True
    if ARTIFACT_TIERS.get(artifact_type) is ArtifactTier.CODE:
        return True
    return bool(target and target in CODE_TARGETS)


@dataclass(frozen=True)
class Principal:
    """An authenticated caller: personas plus any system role."""

    subject: str
    email: str | None = None
    personas: frozenset[Persona] = frozenset()
    system_roles: frozenset[SystemRole] = frozenset()
    scopes: frozenset[str] = frozenset()
    is_authenticated: bool = True
    auth_mode: str = "okta"   # okta | disabled | service

    @property
    def scopes_of_access(self) -> frozenset[AccessScope]:
        scopes = {PERSONA_SCOPE[p] for p in self.personas if p in PERSONA_SCOPE}
        if SystemRole.ADMINISTRATOR in self.system_roles:
            scopes.add(AccessScope.ADMIN)
        if SystemRole.SERVICE in self.system_roles:
            scopes.add(AccessScope.HYBRID)
        return frozenset(scopes)

    @property
    def can_see_code(self) -> bool:
        """True only for Hybrid or Admin access.

        A Global-only principal is denied code artefacts even holding an
        otherwise-senior persona such as Architect -- PRD 5.2 gives the
        architect no repository access unless also assigned Developer.
        """
        return bool(self.scopes_of_access & {AccessScope.HYBRID, AccessScope.ADMIN})

    @property
    def is_admin(self) -> bool:
        return SystemRole.ADMINISTRATOR in self.system_roles

    def has_persona(self, persona: Persona) -> bool:
        """Deliberately NOT satisfied by administrator.

        PRD 5.2: an administrator "cannot bypass audit, versioning, or mandatory
        approvals". Letting admin stand in for any persona would make FR-P3
        cosmetic.
        """
        return persona in self.personas

    def may_approve(self, approval_persona: str,
                    tier: str | ArtifactTier | None = None) -> bool:
        """FR-P3: the caller must hold the persona AND that persona must own the
        artefact's tier.

        Both halves are required. Holding the persona alone is not enough: a
        pipeline that names `architect` on a code artefact is a configuration
        error, and honouring it would let a Global persona approve code.
        """
        required = persona_from_name(approval_persona)
        if required is None or required not in self.personas:
            return False
        if tier is None:
            return True
        resolved = tier if isinstance(tier, ArtifactTier) else None
        if resolved is None:
            try:
                resolved = ArtifactTier(str(tier).strip().lower())
            except ValueError:
                return False   # an unknown tier fails closed
        return may_approve_tier(required, resolved)

    def approvable_tiers(self) -> frozenset[ArtifactTier]:
        tiers: set[ArtifactTier] = set()
        for persona in self.personas:
            tiers |= PERSONA_APPROVES.get(persona, frozenset())
        return frozenset(tiers)

    def describe(self) -> dict:
        return {
            "subject": self.subject,
            "email": self.email,
            "personas": sorted(p.value for p in self.personas),
            "system_roles": sorted(r.value for r in self.system_roles),
            "access_scopes": sorted(s.value for s in self.scopes_of_access),
            "can_see_code": self.can_see_code,
            "approvable_tiers": sorted(t.value for t in self.approvable_tiers()),
            "auth_mode": self.auth_mode,
        }


def personas_from_claims(
    groups: list[str] | None, scopes: list[str] | None = None
) -> tuple[frozenset[Persona], frozenset[SystemRole]]:
    """Map Okta groups and token scopes onto personas and system roles.

    Unrecognised groups are ignored rather than granted. Mapping an unknown group
    onto a default persona is how privilege escalation happens quietly.
    """
    personas: set[Persona] = set()
    system: set[SystemRole] = set()
    for raw in list(groups or []) + list(scopes or []):
        persona = persona_from_name(raw)
        if persona is not None:
            personas.add(persona)
            continue
        role = system_role_from_name(raw)
        if role is not None:
            system.add(role)
    return frozenset(personas), frozenset(system)
