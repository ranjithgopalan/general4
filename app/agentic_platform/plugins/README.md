# Vendored ADLC plugins

These are **copies**, not the source of truth. Upstream is the genlite plugin collection; this
directory exists so the service runs on a machine that has no genlite checkout, and so a run can be
traced to a known plugin revision.

| | |
|---|---|
| Source | `C:\CTS_Git\general\genlite\plugin` |
| Commit | `09aa2d9` (2026-08-11) |
| Synced | 2026-08-11 |
| Command | `python scripts/sync-plugins.py --source <genlite>/plugin` |

## Do not edit anything in here

`scripts/sync-plugins.py` deletes and recopies each plugin, so a local edit is lost on the next
sync without warning. Two rules follow:

- **A name mismatch between a stage and a skill is fixed in `pipelines/*.yaml`,** never by renaming
  a vendored skill directory. `tests/test_pipeline_registry.py` asserts every stage's
  `plugin:skill` resolves against these manifests, so a mismatch fails a test rather than surfacing
  mid-run as `PluginVerificationError`.
- **A genuine plugin change belongs upstream in genlite,** followed by a re-sync here.

## What is vendored, and what is not

Ten of the eleven are what `Pipeline.required_plugins()` reports across both tiers. `AIDLC-db` is
the eleventh — G1's stage note and the fit analysis both depend on it, though no stage owner names
it yet (schema introspection is implemented in `fe_core/db/introspector.py` instead).

| Plugin | Needed by |
|---|---|
| `AIDLC-business-analyst` | Global G2 PRD, G3 FRD, G8 EPIC set |
| `AIDLC-tech-architect` | Global G4 ADR, G5 NFR, G6 SDD, G7 SRD |
| `AIDLC-scrum-master` | Mini M1 Feature, M2 User Story |
| `AIDLC-design` | Mini M3 LLD |
| `AIDLC-validate` | Mini M4 Coverage |
| `AIDLC-axis` | Mini M5 UI code, M10 UI smoke |
| `AIDLC-springboot` | Mini M6 API code, M7 DB integration, M9 API test |
| `AIDLC-security` | Mini M8 Security + OPA |
| `AIDLC-playwright` | Mini M11 UI E2E |
| `AIDLC-docs` | Mini M12 Docs, UAT and deploy |
| `AIDLC-db` | G1 Impact Analysis (schema surface) |

Excluded on purpose: `AIDLC-lambda` (Node on AWS Lambda — DISCARD in the fit analysis, kept
upstream only as a structural template for `AIDLC-springboot`), `AIDLC-telemetry` (not on disk in
genlite at all), and `AIDLC-code`, `AIDLC-opa`, `GATHER-help` (no stage in either pipeline owns
them). Add a name to `REQUIRED` in `scripts/sync-plugins.py` to vendor more.

`__tests__/`, `node_modules/` and `__pycache__/` are excluded from the copy — bulk with no bearing
on a run.

## Verifying

```bash
python scripts/sync-plugins.py --check   # manifests parse, every declared skill and agent exists
python run.py doctor                     # discovery + "all N plugins both tiers need are present"
```
