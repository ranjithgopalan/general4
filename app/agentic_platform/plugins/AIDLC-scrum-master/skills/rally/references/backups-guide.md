# Rally API Backups Guide

Complete guide for Rally API automatic backups and restoration.

---

## Automatic Backups

All update and delete operations automatically create backups before making changes.

**Key Points:**
- Backups are automatic for all `update_object()` and `delete_object()` calls
- Stored in `.claude/.fe-sm/backups/`
- Include full object state before the change
- No manual backup calls needed

---

## Where Backups Are Stored

Backups are saved to: `.claude/.fe-sm/backups/`

Format: `YYYYMMDD_HHMMSS_OPERATION_ITEM.json`

Example:
```
.claude/.fe-sm/backups/
├── 20260115_150245_UPDATE_US12345.json
├── 20260115_150530_DELETE_TA45678.json
└── 20260120_093015_BULK_ASSIGN.json
```

---

## Backup Contents

Each backup includes:
- Full object state before the change
- Timestamp
- Operation type (UPDATE, DELETE, BULK_ASSIGN, etc.)
- Context (object type, ID, name, fields being updated)

Example backup file structure:
```json
{
  "timestamp": "2026-01-15T15:02:45.123456",
  "operation": "UPDATE",
  "context": "US12345",
  "items": [
    {
      "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/12345",
      "FormattedID": "US12345",
      "Name": "Story Name",
      "PlanEstimate": 3,
      "Owner": {
        "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/user/67890",
        "UserName": "john.doe"
      }
      // ... full object state ...
    }
  ]
}
```

---

## Listing Backups

To see available backups:

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api.backup import RallyBackup
import json

backup = RallyBackup()

# List recent backups (default: 20)
backups = backup.list_backups(limit=20)

results = []
for b in backups:
    results.append({
        "filename": b["filename"],
        "timestamp": b["timestamp"],
        "operation": b["operation"],
        "item_count": b["item_count"],
        "context": b.get("context", "")
    })

print(json.dumps(results, indent=2))
PYTHON_EOF
```

Then format the output in your text response:

```
📦 Rally Backups in .claude/.fe-sm/backups/
══════════════════════════════════════════

20260115_150245_UPDATE_US12345.json
  • Type: UPDATE
  • Item: US12345
  • Count: 1 item
  • Time: 2026-01-15 15:02:45

20260115_150530_BULK_ASSIGN.json
  • Type: BULK_ASSIGN
  • Context: 2026.PI1.Iteration1
  • Count: 15 items
  • Time: 2026-01-15 15:05:30

Total: 2 backups
```

---

## Viewing Backup Details

To see what's in a backup:

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api.backup import RallyBackup
import json

backup = RallyBackup()

# View backup metadata
metadata = backup.view_backup("20260115_150245_UPDATE_US12345.json")

result = {
    "filename": metadata["filename"],
    "timestamp": metadata["timestamp"],
    "operation": metadata["operation"],
    "item_count": len(metadata["items"]),
    "context": metadata.get("context", ""),
    "items": [
        {
            "id": item.get("FormattedID", ""),
            "type": item["_type"],
            "name": item.get("Name", ""),
            "plan_estimate": item.get("PlanEstimate"),
            "owner": item["Owner"]["UserName"] if item.get("Owner") else None
        }
        for item in metadata["items"]
    ]
}

print(json.dumps(result, indent=2))
PYTHON_EOF
```

Then format as text:

```
📄 Backup Details: 20260115_150245_UPDATE_US12345.json
══════════════════════════════════════════

Operation: UPDATE
Timestamp: 2026-01-15 15:02:45
Context: US12345
Items: 1

Backed Up State:
  • US12345: Story Name
    - Plan Estimate: 3 points
    - Owner: john.doe
    - Release: 2026.PI1
    - Iteration: 2026.PI1.Iteration1
```

---

## Restoring from Backup

### Dry Run (Preview)

**ALWAYS** preview the restore first to see what will change:

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api import RallyAPI
from rally_api.backup import RallyBackup
import json

api = RallyAPI()
backup = RallyBackup(api)

# Dry run restore (preview only, no changes)
results = backup.restore_backup("20260115_150245_UPDATE_US12345.json", execute=False)

result = {
    "mode": "dry_run",
    "backup_file": "20260115_150245_UPDATE_US12345.json",
    "would_restore": results["restored_count"],
    "would_fail": results["failed_count"],
    "items": [
        {
            "id": r["id"],
            "type": r["type"],
            "status": r["status"],
            "message": r.get("message", "")
        }
        for r in results["results"]
    ]
}

print(json.dumps(result, indent=2))
PYTHON_EOF
```

Format as preview:

```
🔍 Restore Preview (DRY RUN - No changes will be made)
══════════════════════════════════════════

Backup: 20260115_150245_UPDATE_US12345.json

Would restore:
  • US12345: Story Name
    - Plan Estimate: 3 points (currently: 5 points)
    - Owner: john.doe (currently: jane.smith)
    - Iteration: 2026.PI1.Iteration1 (currently: 2026.PI1.Iteration2)

Items to restore: 1
Would succeed: 1
Would fail: 0

Note: This is a DRY RUN. Execute to perform the restore.
```

### Execute Restore

After reviewing the dry run, execute the restore:

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api import RallyAPI
from rally_api.backup import RallyBackup
import json

api = RallyAPI()
backup = RallyBackup(api)

# Execute restore
results = backup.restore_backup("20260115_150245_UPDATE_US12345.json", execute=True)

result = {
    "mode": "executed",
    "backup_file": "20260115_150245_UPDATE_US12345.json",
    "restored": results["restored_count"],
    "failed": results["failed_count"],
    "items": [
        {
            "id": r["id"],
            "type": r["type"],
            "status": r["status"],
            "message": r.get("message", "")
        }
        for r in results["results"]
    ]
}

print(json.dumps(result, indent=2))
PYTHON_EOF
```

Format result:

```
✅ Restore Complete
══════════════════════════════════════════

Backup: 20260115_150245_UPDATE_US12345.json

Restored:
  ✓ US12345: Story Name
    - Plan Estimate: 3 points
    - Owner: john.doe
    - Iteration: 2026.PI1.Iteration1

Successfully restored: 1 item
Failed: 0

Note: A new backup was created before restoring.
You can undo this restore using the newer backup.
```

**IMPORTANT:**
- Restoring creates a NEW backup of the current state before restoring
- You can always undo a restore by restoring the newer backup
- Read-only fields (_ref, FormattedID, ObjectID) are automatically skipped

---

## Backup Management

### Cleanup Old Backups

Backups are not automatically deleted. To clean up old backups:

```bash
# Delete backups older than 30 days (bash)
find .claude/.fe-sm/backups/ -name "*.json" -mtime +30 -delete

# Or list and manually delete
ls -lh .claude/.fe-sm/backups/
```

### Backup Best Practices

1. **Review before deleting**: Always check backup contents before deletion
2. **Keep important backups**: Archive critical backups outside the backups directory
3. **Regular cleanup**: Set up a periodic cleanup process for old backups
4. **Monitor disk space**: Backups can accumulate and use disk space over time
5. **Preview before restore**: Always run dry run first

---

## Common Scenarios

### Undo Recent Change

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api import RallyAPI
from rally_api.backup import RallyBackup
import json

api = RallyAPI()
backup = RallyBackup(api)

# 1. List recent backups
backups = backup.list_backups(limit=10)

# 2. Find the one you need (most recent for this story)
target_backup = None
for b in backups:
    if "US12345" in b["filename"]:
        target_backup = b["filename"]
        break

if target_backup:
    # 3. Preview the restore
    preview = backup.restore_backup(target_backup, execute=False)

    # 4. If preview looks good, execute
    if preview["restored_count"] > 0:
        results = backup.restore_backup(target_backup, execute=True)

        result = {
            "status": "restored",
            "backup": target_backup,
            "items_restored": results["restored_count"]
        }
    else:
        result = {"status": "no_items_to_restore"}
else:
    result = {"status": "backup_not_found"}

print(json.dumps(result, indent=2))
PYTHON_EOF
```

### Recover from Bulk Change Gone Wrong

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api import RallyAPI
from rally_api.backup import RallyBackup
import json

api = RallyAPI()
backup = RallyBackup(api)

# Find the bulk operation backup
backups = backup.list_backups(limit=20)
bulk_backups = [b for b in backups if b["operation"] == "BULK_ASSIGN"]

if bulk_backups:
    latest_bulk = bulk_backups[0]["filename"]

    # Preview what would be restored
    preview = backup.restore_backup(latest_bulk, execute=False)

    result = {
        "backup_found": latest_bulk,
        "items_in_backup": preview["restored_count"],
        "preview": [
            {"id": r["id"], "status": r["status"]}
            for r in preview["results"][:10]  # First 10
        ]
    }
else:
    result = {"backup_found": False}

print(json.dumps(result, indent=2))
PYTHON_EOF
```

### View Deleted Item Data

For DELETE operations, the backup contains the full item state:

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api.backup import RallyBackup
import json

backup = RallyBackup()

# Find delete backups
all_backups = backup.list_backups(limit=50)
delete_backups = [b for b in all_backups if "DELETE" in b["operation"]]

if delete_backups:
    # View the first one
    metadata = backup.view_backup(delete_backups[0]["filename"])

    deleted_item = metadata["items"][0] if metadata["items"] else None

    if deleted_item:
        result = {
            "deleted_item": {
                "id": deleted_item.get("FormattedID", ""),
                "type": deleted_item["_type"],
                "name": deleted_item.get("Name", ""),
                "full_data": deleted_item  # Contains everything needed to recreate
            }
        }
    else:
        result = {"deleted_item": None}
else:
    result = {"delete_backups_found": False}

print(json.dumps(result, indent=2))
PYTHON_EOF
```

**Note:** For DELETE operations, you'll need to manually recreate the item using `create_*` methods. The backup contains all the data needed.

### Audit Trail

Backups serve as an audit trail:

```bash
source ~/.claude/.fe-sm/env.sh 2>/dev/null || source "${FESM_RALLY_API_SCRIPTS_PATH}/load_env.sh"

python3 <<'PYTHON_EOF'
from rally_api.backup import RallyBackup
import json

backup = RallyBackup()

# Get all backups
all_backups = backup.list_backups(limit=100)

# Filter by date or item
date_filter = "20260115"
item_filter = "US12345"

filtered = [
    b for b in all_backups
    if date_filter in b["filename"] or item_filter in b.get("context", "")
]

result = {
    "total_backups": len(all_backups),
    "matching_backups": len(filtered),
    "backups": [
        {
            "filename": b["filename"],
            "operation": b["operation"],
            "timestamp": b["timestamp"]
        }
        for b in filtered
    ]
}

print(json.dumps(result, indent=2))
PYTHON_EOF
```

---

## Backup API Reference

### RallyBackup Class

```python
from rally_api.backup import RallyBackup

# Initialize without API (for read-only operations)
backup = RallyBackup()

# Initialize with API (for restore operations)
from rally_api import RallyAPI
api = RallyAPI()
backup = RallyBackup(api)
```

### Methods

**list_backups(limit=20)**
- Lists recent backup files
- Returns: List[dict] with metadata

**view_backup(backup_file)**
- Views details of a specific backup
- Parameters: backup_file (str) - filename or full path
- Returns: dict with backup metadata and items

**restore_backup(backup_file, execute=False)**
- Restores items from a backup
- Parameters:
  - backup_file (str) - filename or full path
  - execute (bool) - Actually perform restore (default: False for dry run)
- Returns: dict with restore results

---

## See Also

- **SKILL.md** - Main skill documentation
- **examples.md** - 50+ code examples including backup operations
- **reference.md** - Complete Python API reference
- **workflow-guide.md** - Best practices and workflows
- **setup-guide.md** - Configuration and setup
