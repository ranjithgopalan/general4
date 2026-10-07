# Rally Update Safety Protocol

All Rally write operations require explicit user approval before execution.

---

## Operations Requiring Approval

| Operation | Command | Risk |
|-----------|---------|------|
| Update fields | `rally update_object` | Modifies Rally data |
| Move story | `rally move_story` | Changes parent hierarchy |
| Delete item | `rally delete_object` | Permanently removes item |
| Bulk create | `rally bulk_create_stories` | Creates multiple items |
| Fix estimates | `rally fix_task_estimates` | Modifies task hours |

---

## Operations Exempt from Approval

Read-only operations do **not** require approval:

- `rally get_*` — Get items by type
- `rally query` — Query with filters
- `rally find_*` — Find by ID or name
- `rally search_*` — Search by name
- `rally validate_*` — Validate hierarchy/estimates
- `rally analyze_*` — Analyze data consistency
- `rally is_stage_story` — Check story type
- `rally get_expected_task_estimate` — Calculate expected hours

---

## Approval Workflow

### Step 1: Display Proposed Changes

Show the user exactly what will change before any write operation. Nest when updates span parent → children:

```markdown
## Proposed Rally Updates (3 items)

**F12345** — User Authentication Feature
├── State: Backlog → Implementation
├── Reason: Starting sprint work
└── Stories:
    ├── **US67890** — Login page
    │   ├── ScheduleState: Defined → In-Progress
    │   └── Iteration: _(none)_ → Sprint 23
    └── **US67891** — Logout button
        ├── ScheduleState: Defined → In-Progress
        └── Iteration: _(none)_ → Sprint 23
```

For a single-item update, flatten to one level:

```markdown
## Proposed Rally Updates (1 item)

**US774991** — Create BRD template system
├── ScheduleState: Completed → Accepted
├── ActualEndDate: _(empty)_ → 2026-02-16
└── Reason: Plugin implementation is complete and deployed
```

### Step 2: Use AskUserQuestion

```json
{
  "questions": [{
    "question": "Do you approve updating US774991 in Rally?",
    "header": "Rally Update",
    "options": [
      {
        "label": "Approve changes",
        "description": "Update US774991 to Accepted status with ActualEndDate"
      },
      {
        "label": "Cancel",
        "description": "Do not make any changes to Rally"
      }
    ],
    "multiSelect": false
  }]
}
```

### Step 3: Act on Response

- **"Approve changes"** → Execute the Rally update commands
- **"Cancel"** → Do NOT make any changes; inform user that updates were cancelled

---

## Bulk Update Approval

For bulk operations affecting multiple items, summarize all changes in one approval:

```markdown
## Proposed Rally Updates (5 items)

**US12345** — Implement login page
├── Iteration: _(none)_ → Sprint 23
└── Reason: Sprint planning assignment

**US12346** — Add logout button
├── Iteration: _(none)_ → Sprint 23
└── Reason: Sprint planning assignment

**US12347** — Fix session timeout
├── Iteration: Sprint 22 → Sprint 23
└── Reason: Carried over from previous sprint

**US12348** — Add password validation
├── Iteration: _(none)_ → Sprint 23
└── Reason: Sprint planning assignment

**US12349** — Create auth middleware
├── Iteration: _(none)_ → Sprint 23
└── Reason: Sprint planning assignment
```

```json
{
  "questions": [{
    "question": "Do you approve these Rally updates?",
    "header": "Rally Update",
    "options": [
      {
        "label": "Approve changes",
        "description": "Proceed with updating 5 Rally stories"
      },
      {
        "label": "Cancel",
        "description": "Do not make any changes to Rally"
      }
    ],
    "multiSelect": false
  }]
}
```

---

## See Also

- [reference.md](reference.md) — Complete CLI command reference
- [workflows.md](workflows.md) — Multi-step workflow patterns
