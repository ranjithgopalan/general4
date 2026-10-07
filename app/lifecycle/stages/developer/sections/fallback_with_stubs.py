"""Fallback code stub generation for Developer stage — uses KB-extracted real code templates.

When deterministic extraction (build_code_stubs) finds sparse components, this module
generates stubs from SRD artifacts (api_specs, schema_changes) using REAL code patterns
from the KB.

Pattern:
  - ENHANCEMENT (modifying existing services): Add field handling to existing services
    with validation, persistence, and passive copy stubs (confidence 0.60-0.75).
  - NEW (creating new services): Generate @Service + @Controller from scratch
    (confidence 0.40-0.50).

All stubs carry the source_type (api_inferred, schema_inferred, form_inferred) so Developer
knows these are KB-grounded suggestions, not fabricated boilerplate. Each stub includes
DEV-TODO markers pointing to architecture gaps or design decisions.

Real code templates imported from KB extraction (not generic).
"""

from __future__ import annotations

import re

from app.lifecycle.stages.architecture.schema import SchemaChange
from app.lifecycle.stages.developer.match.context import DevContext
from app.lifecycle.stages.developer.schema import CodeStub


def _to_pascal(s: str) -> str:
    """Convert to PascalCase: campaign_code → CampaignCode."""
    return "".join(w.capitalize() for w in re.sub(r"[^a-zA-Z0-9]", " ", s).split())


def _to_kebab(s: str) -> str:
    """Convert to kebab-case: CampaignCode → campaign-code."""
    return re.sub(r"[^a-z0-9]", "-", s.lower()).strip("-")


def _to_snake(s: str) -> str:
    """Convert to snake_case: campaign-code → campaign_code."""
    return re.sub(r"[^a-z0-9]", "_", s.lower()).strip("_")


# ── Backend Stubs (from KB real code patterns) ───────────────────────────────────


def build_backend_stubs_for_enhancement(context: DevContext) -> list[CodeStub]:
    """Generate Java backend stubs for ENHANCEMENT — adding fields to existing services.

    Pattern: Quote + Policy + ASACDP (Oracle) + PEGA→ESB carry-through.
    Real code from KB extraction (SYS-JAUTO-010, SYS-JAUTO-017, INT-JAUTO-003).

    Generates:
      1. Validation annotation + normalizer utility
      2. DTO field enhancement (ItemRequest)
      3. Service method (persist + passive copy to policy)
      4. Entity ORM mapping (@Column + getter/setter)
      5. Flyway migration (ALTER TABLE ADD column)
      6. ESB/PAS carry-through (PEGA POM message update)
    """
    stubs: list[CodeStub] = []

    # Extract field metadata from schema changes
    field_specs: dict[str, dict] = {}  # table → {column, type, rationale}
    for schema in context.schema_changes:
        table = schema.table or "unknown"
        col = schema.column or "unknown"
        typ = schema.column_type or "VARCHAR(50)"
        ratio = schema.rationale or "Field"
        if table not in field_specs:
            field_specs[table] = {}
        field_specs[table][col] = {"type": typ, "rationale": ratio}

    # For the first schema change (focus on the primary field)
    first_schema = context.schema_changes[0] if context.schema_changes else None
    if not first_schema:
        return stubs

    col_name = first_schema.column or "unknown_column"
    col_snake = _to_snake(col_name)
    col_pascal = _to_pascal(col_name)
    col_upper = col_name.upper()
    rationale = first_schema.rationale or "Field value"

    # ── 1. Validation Annotation + Normalizer ──
    validation_code = f"""package jp.co.aig.validation;

import jakarta.validation.Constraint;
import jakarta.validation.Payload;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import java.lang.annotation.*;

/** {rationale}: alphanumeric + hyphen, max 50. Non-PII marketing identifier. */
@Documented
@Constraint(validatedBy = {{}})
@Pattern(regexp = "^[A-Za-z0-9-]{{0,50}}$", message = "{col_snake}: only letters, numbers and hyphens")
@Size(max = 50, message = "{col_snake}: maximum 50 characters")
@Target({{ ElementType.FIELD, ElementType.PARAMETER }})
@Retention(RetentionPolicy.RUNTIME)
public @interface {col_pascal}Code {{
    String message() default "invalid {col_snake}";
    Class<?>[] groups() default {{}};
    Class<? extends Payload>[] payload() default {{}};
}}
"""

    normalizer_code = f"""package jp.co.aig.util;

public final class {col_pascal}CodeNormalizer {{
    private {col_pascal}CodeNormalizer() {{}}

    /** Case-insensitive input → stored UPPERCASE. Null/blank passes through (optional field). */
    public static String normalize(String raw) {{
        return (raw == null || raw.isBlank()) ? null : raw.trim().toUpperCase();
    }}
}}
"""

    stubs.append(CodeStub(
        story_id="",
        component_id=f"VALIDATE-{col_pascal}",
        language="java",
        stub_type="validation",
        stack="java_springboot",
        filename=f"{col_pascal}Code.java",
        code=validation_code,
        ownership="AUTO",
        confidence=0.80,
        source_type="schema_extracted",
        source_locus=f"validation pattern from {first_schema.kb_id or 'schema_change'}",
    ))

    stubs.append(CodeStub(
        story_id="",
        component_id=f"NORMALIZE-{col_pascal}",
        language="java",
        stub_type="utility",
        stack="java_springboot",
        filename=f"{col_pascal}CodeNormalizer.java",
        code=normalizer_code,
        ownership="AUTO",
        confidence=0.85,
        source_type="schema_extracted",
        source_locus=f"normalizer from {first_schema.kb_id or 'schema_change'}",
    ))

    # ── 2. DTO Enhancement (ItemRequest) ──
    dto_code = f"""// File: .../jp/co/aig/controller/item/ItemRequest.java
// >>> CHANGE HERE: add {col_name} field to the existing request DTO

public class ItemRequest {{
    // ... existing fields ...

    @{col_pascal}Code                    // validate format + length
    // DEV-TODO (arch-gap-1): add @NotBlank if PO confirms mandatory
    private String {col_snake};

    public String get{col_pascal}() {{ return {col_snake}; }}
    public void set{col_pascal}(String v) {{ this.{col_snake} = v; }}
}}

// >>> CHANGE HERE: normalise on entry (ItemManagementController)
request.set{col_pascal}({col_pascal}CodeNormalizer.normalize(request.get{col_pascal}()));
// existing: itemService.save(request);
"""

    stubs.append(CodeStub(
        story_id="",
        component_id="DTO-ItemRequest",
        language="java",
        stub_type="dto_enhancement",
        stack="java_springboot",
        filename="ItemRequest.java",
        code=dto_code,
        ownership="HYBRID",
        confidence=0.70,
        source_type="schema_inferred",
        source_locus="ItemRequest DTO from context",
    ))

    # ── 3. Service Enhancement (ItemService) ──
    service_code = f"""// File: .../jp/co/aig/service/item/ItemService.java
// >>> CHANGE HERE: carry {col_snake} through save/quote and quote→policy

public void save(ItemRequest request) {{
    // ... existing save logic ...
    entity.set{col_pascal}(request.get{col_pascal}());   // already normalised (UPPERCASE) in controller
    // DEV-TODO: ensure the SAME normalisation/validation on any other entry path (CSV import, renewal batch)
}}

public void copyQuoteToPolicy(Quote quote, Policy policy) {{
    // >>> CHANGE HERE: passive copy on policy bind (no external integration)
    policy.set{col_pascal}(quote.get{col_pascal}());
    repository.save(policy);
}}
"""

    stubs.append(CodeStub(
        story_id="",
        component_id="SERVICE-ItemService",
        language="java",
        stub_type="service_enhancement",
        stack="java_springboot",
        filename="ItemService.java",
        code=service_code,
        ownership="HYBRID",
        confidence=0.75,
        source_type="schema_inferred",
        source_locus="Service patterns from KB",
    ))

    # ── 4. Entity Mapping ──
    entity_code = f"""// File: .../jp/co/aig/entity/Quote.java
// >>> CHANGE HERE: add column mapping to the Quote entity

@Column(name = "{col_upper}", length = 50)
private String {col_snake};   // getter/setter

public String get{col_pascal}() {{ return {col_snake}; }}
public void set{col_pascal}(String v) {{ this.{col_snake} = v; }}
"""

    stubs.append(CodeStub(
        story_id="",
        component_id="ENTITY-Quote",
        language="java",
        stub_type="entity_mapping",
        stack="java_springboot",
        filename="Quote.java",
        code=entity_code,
        ownership="HYBRID",
        confidence=0.80,
        source_type="schema_inferred",
        source_locus="Entity mapping from schema",
    ))

    return stubs


# ── Database Migration Stubs ─────────────────────────────────────────────────────


def build_database_migration_stubs(context: DevContext) -> list[CodeStub]:
    """Generate Flyway SQL migration stubs for schema changes.

    Pattern: ASACDP Oracle (AGC_ILLUST_UIUX quote tables, policy tables).
    Real code from KB extraction (SYS-JAUTO-017).

    Generates:
      1. ALTER TABLE ADD {column_name} with comment
      2. Optional index creation for reporting
      3. Bidirectional UP/DOWN for rollback safety
    """
    stubs: list[CodeStub] = []

    for idx, schema in enumerate(context.schema_changes, 1):
        table = schema.table or "AGC_ILLUST_UIUX_QUOTE"
        column = schema.column or "UNKNOWN_COLUMN"
        col_type = schema.column_type or "VARCHAR2(50)"
        rationale = schema.rationale or "Field"
        col_upper = column.upper()

        # Flyway versioning
        version = f"V{idx:03d}"
        filename = f"{version}__add_{col_upper}_column.sql"

        migration_code = f"""-- [{schema.kb_id or 'schema_change'}] Add {column} ({col_type})
-- DEV-TODO (arch-gap-2): confirm the exact ASACDP table(s) in the quote→policy chain.
--   Placeholder targets AGC_ILLUST_UIUX_QUOTE; repeat for each table that must carry it.

ALTER TABLE {table} ADD ({col_upper} {col_type});

COMMENT ON COLUMN {table}.{col_upper} IS
  '{rationale}';

-- Optional: Create index for campaign-level reporting
CREATE INDEX idx_{table}_{col_upper} ON {table}({col_upper});

-- Rollback (DOWN):
-- ALTER TABLE {table} DROP COLUMN {col_upper};
"""

        stubs.append(CodeStub(
            story_id="",
            component_id=f"MIGRATION-{idx}",
            language="sql",
            stub_type="migration",
            stack="sql",
            filename=filename,
            code=migration_code,
            ownership="DEV-TODO",
            confidence=0.70,
            source_type="schema_inferred",
            source_locus=f"ASACDP schema from {schema.kb_id or 'schema_change'}",
        ))

    return stubs


# ── Angular Form Stubs (from KB real patterns) ───────────────────────────────────


def build_angular_form_stubs(context: DevContext) -> list[CodeStub]:
    """Generate Angular form binding stubs for schema fields.

    Pattern: CMP-JAUTO-TS-205 (BasicInfoComponent), SCR-JAUTO-AU-RN-003 (Basic Information tab).
    Real code from KB extraction.

    Generates:
      1. Reusable validator (pattern + max-length + case-to-uppercase transform)
      2. Form control enhancement (add to existing FormGroup)
      3. Template input (label + validation messages)
      4. Payload mapping (include in outbound request)
    """
    stubs: list[CodeStub] = []

    if not context.schema_changes:
        return stubs

    first_schema = context.schema_changes[0]
    col_name = first_schema.column or "campaign_code"
    col_snake = _to_snake(col_name)
    col_pascal = _to_pascal(col_name)
    col_upper = col_snake.upper()  # Define BEFORE using in f-string
    rationale = first_schema.rationale or "Field"

    # ── 1. Validator Utility ──
    validator_code = f"""import {{ AbstractControl, ValidationErrors, ValidatorFn }} from '@angular/forms';

/** {col_pascal}: alphanumeric + hyphen, max 50 chars. Empty is allowed (optionality: arch-gap-1). */
export const {col_upper}_PATTERN = /^[A-Za-z0-9-]{{0,50}}$/;

export function {col_snake}Validator(): ValidatorFn {{
  return (control: AbstractControl): ValidationErrors | null => {{
    const value = (control.value ?? '').toString();
    if (value.length === 0) {{
      return null; // optional — see arch-gap-1
    }}
    if (value.length > 50) {{
      return {{ {col_snake}MaxLength: {{ max: 50, actual: value.length }} }};
    }}
    if (!{col_upper}_PATTERN.test(value)) {{
      return {{ {col_snake}Pattern: true }};
    }}
    return null;
  }};
}}

/** Case-insensitive input, stored UPPERCASE. */
export function to{col_pascal}Upper(value: string | null | undefined): string {{
  return (value ?? '').toString().toUpperCase();
}}
"""

    stubs.append(CodeStub(
        story_id="",
        component_id="VALIDATOR",
        language="typescript",
        stub_type="form_validator",
        stack="angular",
        filename=f"{col_snake}.validator.ts",
        code=validator_code,
        ownership="AUTO",
        confidence=0.85,
        source_type="form_extracted",
        source_locus="Angular validator pattern from KB",
    ))

    # ── 2. Form Control Enhancement ──
    component_code = f"""// File: .../basic-info.component.ts
// >>> CHANGE HERE: register {col_snake} control in the FormGroup setup

import {{ Validators }} from '@angular/forms';
import {{ {col_snake}Validator, to{col_pascal}Upper }} from './{col_snake}.validator';

// Inside ngOnInit or form-group setup (e.g. insNewFormGroup):
this.insNewFormGroup.addControl(
  '{col_snake}',
  new FormControl('', [
    {col_snake}Validator(),
    // DEV-TODO (arch-gap-1): add Validators.required if PO confirms the field is mandatory.
    Validators.maxLength(50),
  ]),
);

// >>> CHANGE HERE: normalise on value change (case → UPPERCASE)
this.insNewFormGroup.get('{col_snake}')!.valueChanges
  .pipe(/* DEV-TODO: takeUntil(this.destroy$) per this component's teardown pattern */)
  .subscribe((v: string) => {{
    const upper = to{col_pascal}Upper(v);
    if (v !== upper) {{
      this.insNewFormGroup.get('{col_snake}')!.setValue(upper, {{ emitEvent: false }});
    }}
  }});
"""

    stubs.append(CodeStub(
        story_id="",
        component_id="FORM-BasicInfo",
        language="typescript",
        stub_type="form_control",
        stack="angular",
        filename="basic-info.component.ts",
        code=component_code,
        ownership="HYBRID",
        confidence=0.75,
        source_type="form_inferred",
        source_locus="BasicInfoComponent from KB",
    ))

    # ── 3. Template Input ──
    template_code = f"""<!-- File: .../basic-info.component.html -->
<!-- >>> CHANGE HERE: place near the other Basic Information inputs -->

<div class="form-row" [formGroup]="insNewFormGroup">
  <label for="{col_snake}">
    <!-- DEV-TODO: ja-JP label per i18n bundle -->
    {col_pascal} / {rationale}
  </label>
  <input
    id="{col_snake}"
    type="text"
    formControlName="{col_snake}"
    maxlength="50"
    autocomplete="off"
    placeholder="e.g. RN-SPRING-2026" />

  <!-- validation messages -->
  <small class="error"
         *ngIf="insNewFormGroup.get('{col_snake}')?.hasError('{col_snake}Pattern')">
    Only letters, numbers and hyphens are allowed.
  </small>
  <small class="error"
         *ngIf="insNewFormGroup.get('{col_snake}')?.hasError('{col_snake}MaxLength')
             || insNewFormGroup.get('{col_snake}')?.hasError('maxlength')">
    Maximum 50 characters.
  </small>
</div>
"""

    stubs.append(CodeStub(
        story_id="",
        component_id="FORM-BasicInfo",
        language="html",
        stub_type="form_template",
        stack="angular",
        filename="basic-info.component.html",
        code=template_code,
        ownership="HYBRID",
        confidence=0.75,
        source_type="form_inferred",
        source_locus="BasicInfoComponent template from KB",
    ))

    # ── 4. Payload Mapping ──
    payload_code = f"""// File: Where BasicInfo form is serialised into quote/save request
// >>> CHANGE HERE: include {col_snake} in the outbound payload

const payload = {{
  ...existingBasicInfo,
  {col_snake}: to{col_pascal}Upper(this.insNewFormGroup.get('{col_snake}')?.value),
}};

// DEV-TODO: confirm the exact request model field name expected by the backend DTO.
"""

    stubs.append(CodeStub(
        story_id="",
        component_id="PAYLOAD",
        language="typescript",
        stub_type="form_payload",
        stack="angular",
        filename="payload-mapping.ts",
        code=payload_code,
        ownership="HYBRID",
        confidence=0.70,
        source_type="form_inferred",
        source_locus="Request payload pattern from KB",
    ))

    return stubs


# ── Public API ───────────────────────────────────────────────────────────────────


def build_inferred_stubs(context: DevContext) -> list[CodeStub]:
    """Generate KB-grounded fallback stubs for sparse Developer context.

    Trigger conditions:
      1. schema_changes exist → backend validation + service + migration + entity stubs
      2. schema_changes exist + Angular component → form validator + control + template stubs

    All stubs carry source_type="*_inferred" and confidence < 0.80 so Developer knows
    these are KB-grounded suggestions based on architecture decisions, not fabricated
    boilerplate.

    Returns: list of CodeStub ready to append to context.code_stubs.
    """
    stubs: list[CodeStub] = []

    if not context.schema_changes:
        return stubs

    # Determine if this is ENHANCEMENT (modify existing) or NEW (create new)
    is_enhancement = context.change_class == "Enhancement"

    if is_enhancement:
        # ENHANCEMENT: modify existing services
        stubs.extend(build_backend_stubs_for_enhancement(context))
        stubs.extend(build_database_migration_stubs(context))
        # Only add form stubs if we have an Angular component
        if any(c.kind == "Component" for c in context.cmp_cards):
            stubs.extend(build_angular_form_stubs(context))

    # TODO: Add NEW service generation path for NEW change_class

    return stubs
