---
name: rule-discover
description: Discover, classify, contextualize, and trace every business rule in a legacy application document across 15 passes. Produces 8 structured artifacts (rule_inventory.json, dependency graph, ambiguity report, conflict report, coverage report, traceability map). Must complete before PRD/FRD generation. Applies to ALL uploaded documents.
license: Proprietary
compatibility: Requires rule-discovery-agent.
metadata:
  author: AIDLC Business Analyst Team
  version: "1.0.0"
  organization: AIG
  plugin: AIDLC-business-analyst
allowed-tools: Task Read Glob Grep Write AskUserQuestion
---

# Rule Discovery Skill

Invokes the `rule-discovery-agent` to perform complete rule extraction from a legacy application document before any PRD or FRD generation is allowed.

## When to Use This Skill

Use this skill when:
- A legacy application document (RED, rules document, BRD source, policy document) is uploaded
- User requests rule extraction or rule inventory creation
- Pre-PRD gate check is required
- Traceability from source rules to requirements is needed

**Do NOT skip this skill** before PRD/FRD generation. PRD gate will be BLOCKED if rule discovery has not completed.

---

## Execution

### Step 1 — Identify the document

Ask the user for the document path if not already provided:
```
Which document should I analyze for rule discovery?
(Provide the file path to the RED / legacy rules document)
```

### Step 2 — Assess document size

Read ONLY the first 50 lines to estimate document size:

```
Read(file_path, limit=50)
```

Then count total lines:
```
Bash: wc -l <file_path>   (or PowerShell: (Get-Content <file>).Count)
```

**Size tiers and processing strategy:**

| Lines | Tokens (est.) | Strategy |
|-------|--------------|----------|
| < 500 | < 8K | Single pass — load full document |
| 500–2000 | 8K–30K | Section-by-section — split on headings |
| 2000–8000 | 30K–120K | Chunk by heading groups (max 500 lines/chunk) |
| > 8000 | > 120K | Split into numbered part files first, then process each part |

### Step 3 — Split large documents (if lines > 500)

Extract section headings first:
```
Grep(pattern="^#{1,3} |^[0-9]+\.", file=<path>, output_mode="content")
```

Split into chunks at heading boundaries. Save each chunk to:
```
.claude/AIDLC-business-analyst/rule-discovery/{workspace_id}/chunks/
  ├── chunk_01.md   (Section 1–N)
  ├── chunk_02.md   (Section N+1–M)
  └── ...
```

### Step 4 — Invoke rule-discovery-agent per chunk

For EACH chunk invoke the agent separately:

```
Task(
  agent: rule-discovery-agent,
  input: {
    document_path: <chunk_path>,
    workspace_id: <workspace_id>,
    chunk_index: <N>,
    total_chunks: <T>,
    expected_rule_count: <N or "unknown">
  }
)
```

Each chunk produces its own `rule_inventory_chunk_N.json`.

### Step 5 — Merge chunk inventories

After all chunks complete, merge into final artifacts:
- Deduplicate rule IDs across chunks (renumber if collision)
- Merge dependency graph edges
- Merge ambiguity + conflict reports
- Recalculate coverage metrics across full document

Save merged output to:
```
.claude/AIDLC-business-analyst/rule-discovery/{workspace_id}/
```

### Step 3 — Gate check

After the agent completes, verify:

```
PRD Generation: READY / BLOCKED
```

- **READY** → inform the user and proceed to `ba-generate` or `ba-frd`
- **BLOCKED** → show blocking reasons; do NOT invoke PRD agent

### Step 4 — Surface artifacts

Report artifact locations:
```
.claude/AIDLC-business-analyst/rule-discovery/{workspace_id}/
  ├── rule_inventory.json
  ├── rule_inventory.csv
  ├── rule_context.json
  ├── rule_dependency_graph.json
  ├── rule_ambiguity_report.json
  ├── rule_conflict_report.json
  ├── rule_source_traceability.json
  └── rule_coverage_report.json
```

---

## Output Summary Format

```
Document:               <filename>
Expected Rules:         N
Rules Discovered:       N
Rules Understood:       N
Ambiguous:              N
Potential Duplicates:   N
Conflicts:              N
Missing:                N

Discovery Coverage:     N%
Traceability Coverage:  N%

PRD Generation: READY / BLOCKED
```
