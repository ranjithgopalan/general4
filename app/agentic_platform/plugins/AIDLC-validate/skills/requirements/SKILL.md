---
name: requirements
description: Validate requirements coverage across SDLC stages (BRD→EPIC→Features→Stories→Code) Use when the user requests this functionality.
---

## Agent Invocation

Use Task tool with `subagent_type="requirements-validator-router"` with the following enriched prompt:

Prompt: """
## User Request
$ARGUMENTS

## Intent: VALIDATE (Requirements Completeness)

### 1. Analyze Validation Type
Determine from keywords:
- BRD/business requirements → epic-coverage-validator
- PRD/product requirements → feature-coverage-validator
- stories/user stories → story-coverage-validator
- code/implementation → traceability-analyzer
- full chain/complete → All validators sequentially

### 2. Identify Ground Truth
- Get ground truth document paths from user
- Identify target artifacts to validate

### 3. Present Plan Before Execution
- Validation type and scope
- Ground truth documents
- Target artifacts
- Wait for user confirmation

### 4. Delegate to Appropriate Validator
- epic-coverage-validator → BRD → EPIC coverage
- feature-coverage-validator → PRD → Features coverage
- story-coverage-validator → Features → Stories coverage
- traceability-analyzer → Stories → Code traceability

### 5. Aggregate Results
Generate coverage-report.md with:
- Coverage summary with percentages
- Gaps identified with evidence
- Recommendations for fixing gaps
"""

Request to process: $ARGUMENTS

---

## Reporting to the AIDLC Platform (ECS UI tracking)

> **SERVER CLI GUARD:** If `aidlc.config.json` does **not** exist in your current
> working directory, skip this entire ECS section and run only the main workflow
> above. These steps are for the **local Claude Code session only**.

The local session creates the ECS run and monitors progress. All generation
is performed by the ECS server CLI — do **not** generate or copy files locally.

---

**Step 0 — Read config**
```powershell
$cfg   = Get-Content "aidlc.config.json" | ConvertFrom-Json
$BASE  = $cfg.central_base_url
$TOKEN = [System.Environment]::GetEnvironmentVariable($cfg.auth.bearer_env)
$WS    = $cfg.workspace_id
$HDR   = @{ Authorization = "Bearer $TOKEN"; "Content-Type" = "application/json" }
$STAGE = "requirements"
```

**Step 1 — Cancel stale runs and create new run**
```powershell
try {
    $stale = (Invoke-RestMethod "$BASE/pipeline-runs?workspace_id=$WS" -Headers $HDR).runs
    $stale | Where-Object { $_.stage_key -eq $STAGE -and $_.state -eq "queued" -and (-not $_.worktree) } |
        ForEach-Object { Invoke-RestMethod "$BASE/pipeline-runs/$($_.run_id)/cancel" -Headers $HDR -Method Post | Out-Null }
} catch {}
$body   = @{ stage=$STAGE; workspace_id=$WS; execute=$true; force=$true } | ConvertTo-Json
$run    = (Invoke-RestMethod "$BASE/pipeline-runs" -Headers $HDR -Method Post -Body $body).run
$RUN_ID = $run.run_id
Write-Host "ECS run created: $RUN_ID — server CLI is generating. Monitoring..."
```

**Step 2 — Monitor and display elapsed time**
```powershell
do {
    Start-Sleep -Seconds 15
    $run     = (Invoke-RestMethod "$BASE/pipeline-runs/$RUN_ID" -Headers $HDR).run
    $elapsed = if ($run.started_at) {
        $s = [datetime]::Parse($run.started_at)
        $e = if ($run.finished_at) { [datetime]::Parse($run.finished_at) } else { Get-Date }
        "$([int]($e - $s).TotalSeconds)s"
    } else { "queued" }
    $files = if ($run.files_written) { $run.files_written.Count } else { 0 }
    Write-Host "  [$elapsed]  state=$($run.state)  files=$files"
} while ($run.state -notin @("waiting_for_approval","completed","failed","cancelled"))
```

**Step 3 — Final timing summary**
```powershell
$fmt = { param($t) if ($t) { [datetime]::Parse($t).ToString("HH:mm:ss") } else { "—" } }
$sec = if ($run.started_at -and $run.finished_at) {
    [int]([datetime]::Parse($run.finished_at) - [datetime]::Parse($run.started_at)).TotalSeconds
} else { 0 }
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
Write-Host "  Run     : $RUN_ID"
Write-Host "  Stage   : $STAGE"
Write-Host "  State   : $($run.state)"
Write-Host "  Queued  : $(& $fmt $run.queued_at)"
Write-Host "  Started : $(& $fmt $run.started_at)"
Write-Host "  Finished: $(& $fmt $run.finished_at)"
Write-Host "  Elapsed : ${sec}s"
Write-Host "  Files   : $($run.files_written.Count) written"
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
```

**Step 4 — Review in ECS UI**
```
https://poc-ecs-build-ui.npsdlchgapp.us-east-1.aws.aig.net/aidlc-platform-management/pipeline
```

Approval persona: **Business Analyst**