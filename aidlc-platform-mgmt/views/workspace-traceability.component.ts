import { Component, OnInit, OnDestroy, signal } from "@angular/core";
import { ActivatedRoute } from "@angular/router";
import { forkJoin, of, Subscription } from "rxjs";
import { catchError } from "rxjs/operators";

import {
  Artifact,
  AidlcPlatformMgmtService,
  Run,
  Stage,
  TraceabilityArtifact,
  TraceabilityCoverageCell,
  TraceabilityKbGrounding,
  TraceabilityRelationship,
  TraceabilityResponse,
} from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

/** Ordered AIDLC pipeline stage chain — correct order: PRD→FRD→ADR/NFR→SDD→SRD→EPIC Set→… */
const CHAIN_STAGES = [
  "prd", "frd", "adr", "nfr", "sdd", "srd",
  "epic-set", "feature", "user-story",
  "lld-and-tdds", "coverage-validation",
  "ui-code-generation", "ui-smoke-test",
  "api-code-generation", "db-integration", "security-review-and-gpa-policies", "api-testing",
  "ui-e2e-testing",
] as const;

/** Human-friendly label for each stage key. */
const STAGE_LABELS: Record<string, string> = {
  prd:                                  "PRD / BRD",
  frd:                                  "FRD",
  adr:                                  "ADR",
  nfr:                                  "NFR",
  sdd:                                  "SDD",
  srd:                                  "SRD",
  "epic-set":                           "EPIC Set",
  feature:                              "Feature",
  "user-story":                         "User Story",
  "lld-and-tdds":                       "LLD / TDD",
  "coverage-validation":                "Coverage",
  "ui-code-generation":                 "UI Code",
  "ui-smoke-test":                      "UI Smoke",
  "api-code-generation":                "API Code",
  "db-integration":                     "DB Integration",
  "security-review-and-gpa-policies":   "Security OPA",
  "api-testing":                        "API Test",
  "ui-e2e-testing":                     "UI E2E",
  // legacy aliases kept for older workspaces
  dev:  "Code",
  test: "Test",
};

/** Each hop in the AIDLC pipeline relationship chain. */
interface RelHop {
  fromStage:   string;
  fromLabel:   string;
  fromWsLabel: string;
  toStage:     string;
  toLabel:     string;
  toWsLabel:   string;
  relType:     string;
}

/**
 * Correct AIDLC relationship chain:
 *   PRD → FRD → (ADR, NFR → SDD → SRD) → EPIC Set → Feature → User Story
 *   → LLD/TDD → (UI Code → UI Smoke, API Code → DB/Security/API Test, Coverage, UI E2E)
 */
const REL_CHAIN: RelHop[] = [
  // ── Global: Requirements ─────────────────────────────────────────────────
  { fromStage: "prd",               fromLabel: "PRD / BRD",   fromWsLabel: "Global",       toStage: "frd",                              toLabel: "FRD",           toWsLabel: "Global",       relType: "derives_from" },
  // ── Architecture: ADR + NFR branch from FRD ──────────────────────────────
  { fromStage: "frd",               fromLabel: "FRD",          fromWsLabel: "Global",       toStage: "adr",                              toLabel: "ADR",           toWsLabel: "Architecture", relType: "derives_from" },
  { fromStage: "frd",               fromLabel: "FRD",          fromWsLabel: "Global",       toStage: "nfr",                              toLabel: "NFR",           toWsLabel: "Architecture", relType: "derives_from" },
  { fromStage: "nfr",               fromLabel: "NFR",          fromWsLabel: "Architecture", toStage: "sdd",                              toLabel: "SDD",           toWsLabel: "Architecture", relType: "derives_from" },
  { fromStage: "sdd",               fromLabel: "SDD",          fromWsLabel: "Architecture", toStage: "srd",                              toLabel: "SRD",           toWsLabel: "Architecture", relType: "derives_from" },
  // ── EPIC fan-out ──────────────────────────────────────────────────────────
  { fromStage: "srd",               fromLabel: "SRD",          fromWsLabel: "Architecture", toStage: "epic-set",                         toLabel: "EPIC Set",      toWsLabel: "Global",       relType: "derives_from" },
  // ── Mini workspace: Features & User Stories ───────────────────────────────
  { fromStage: "epic-set",          fromLabel: "EPIC Set",     fromWsLabel: "Global",       toStage: "feature",                          toLabel: "Feature",       toWsLabel: "Mini",         relType: "traces_to"    },
  { fromStage: "feature",           fromLabel: "Feature",      fromWsLabel: "Mini",         toStage: "user-story",                       toLabel: "User Story",    toWsLabel: "Mini",         relType: "traces_to"    },
  // ── Developer workspace: LLD → Code ─────────────────────────────────────
  { fromStage: "user-story",        fromLabel: "User Story",   fromWsLabel: "Mini",         toStage: "lld-and-tdds",                     toLabel: "LLD / TDD",     toWsLabel: "Developer",    relType: "implements"   },
  { fromStage: "lld-and-tdds",      fromLabel: "LLD / TDD",   fromWsLabel: "Developer",    toStage: "ui-code-generation",               toLabel: "UI Code",       toWsLabel: "Developer",    relType: "implements"   },
  { fromStage: "lld-and-tdds",      fromLabel: "LLD / TDD",   fromWsLabel: "Developer",    toStage: "api-code-generation",              toLabel: "API Code",      toWsLabel: "Developer",    relType: "implements"   },
  { fromStage: "lld-and-tdds",      fromLabel: "LLD / TDD",   fromWsLabel: "Developer",    toStage: "coverage-validation",              toLabel: "Coverage",      toWsLabel: "Developer",    relType: "implements"   },
  // ── API Code sub-stages ───────────────────────────────────────────────────
  { fromStage: "api-code-generation", fromLabel: "API Code",  fromWsLabel: "Developer",    toStage: "db-integration",                   toLabel: "DB Integration",toWsLabel: "Developer",    relType: "implements"   },
  { fromStage: "api-code-generation", fromLabel: "API Code",  fromWsLabel: "Developer",    toStage: "security-review-and-gpa-policies", toLabel: "Security OPA",  toWsLabel: "Developer",    relType: "implements"   },
  // ── Tester workspace ──────────────────────────────────────────────────────
  { fromStage: "ui-code-generation",fromLabel: "UI Code",      fromWsLabel: "Developer",   toStage: "ui-smoke-test",                    toLabel: "UI Smoke",      toWsLabel: "Tester",       relType: "tests"        },
  { fromStage: "api-code-generation",fromLabel: "API Code",   fromWsLabel: "Developer",    toStage: "api-testing",                      toLabel: "API Test",      toWsLabel: "Tester",       relType: "tests"        },
  { fromStage: "ui-code-generation",fromLabel: "UI Code",      fromWsLabel: "Developer",   toStage: "ui-e2e-testing",                   toLabel: "UI E2E",        toWsLabel: "Tester",       relType: "tests"        },
];

/** A single node in the horizontal pipeline flow diagram. */
interface FlowNode {
  id:        string;   // G1, G2, M1 …
  label:     string;   // display name
  stageKeys: string[]; // possible stage_key values for artifact lookup
  wsType:    "root" | "global" | "arch" | "mini" | "developer" | "tester" | "deploy";
  isLeaf?:   boolean;  // true = branch terminates here (no further arrow)
  isFanOut?: boolean;  // true = fan-out to N workspaces
}

/** A vertical "column" in the pipeline flow — one or more nodes stacked. */
interface FlowStep {
  nodes: FlowNode[];
}

/**
 * Horizontal pipeline flow — left to right.
 *
 * Correct order confirmed by reference diagram:
 *   KB/RED → PRD → FRD → [ADR(leaf), NFR] → SDD → SRD → EPIC Set[×N]
 *   → Feature → User Story → LLD/TDD
 *   → [UI Code, API Code, Coverage(leaf)]
 *   → [UI Smoke, DB Integration, Security OPA, API Test, UI E2E]
 *   → Docs & UAT Deploy
 */
const FLOW_STEPS: readonly FlowStep[] = [
  { nodes: [{ id: "G1",  label: "PRD / BRD",        stageKeys: ["prd"],                                            wsType: "global"   }] },
  { nodes: [{ id: "G2",  label: "FRD",               stageKeys: ["frd"],                                            wsType: "global"   }] },
  // FRD branches: ADR (leaf) + NFR (continues)
  { nodes: [
    { id: "G3", label: "ADR",  stageKeys: ["adr"],   wsType: "arch", isLeaf: true },
    { id: "G4", label: "NFR",  stageKeys: ["nfr"],   wsType: "arch" },
  ] },
  { nodes: [{ id: "G5",  label: "SDD",               stageKeys: ["sdd"],                                            wsType: "arch"     }] },
  { nodes: [{ id: "G6",  label: "SRD",               stageKeys: ["srd"],                                            wsType: "arch"     }] },
  { nodes: [{ id: "G7",  label: "EPIC Set",          stageKeys: ["epic-set", "epic"],                               wsType: "global",  isFanOut: true }] },
  { nodes: [{ id: "M1",  label: "Feature",           stageKeys: ["feature"],                                        wsType: "mini"     }] },
  { nodes: [{ id: "M2",  label: "User Story",        stageKeys: ["user-story"],                                     wsType: "mini"     }] },
  { nodes: [{ id: "M3",  label: "LLD / TDD",         stageKeys: ["lld-and-tdds", "lld"],                           wsType: "developer"}] },
  // LLD/TDD branches: UI Code, API Code, Coverage
  { nodes: [
    { id: "M5", label: "UI Code",  stageKeys: ["ui-code-generation",  "ui-code"],  wsType: "developer" },
    { id: "M6", label: "API Code", stageKeys: ["api-code-generation", "api-code"], wsType: "developer" },
    { id: "M4", label: "Coverage", stageKeys: ["coverage-validation"],              wsType: "developer", isLeaf: true },
  ] },
  // Tests: UI Smoke (from UI Code), DB Integration + Security OPA + API Test (from API Code), UI E2E
  { nodes: [
    { id: "M1B", label: "UI Smoke",        stageKeys: ["ui-smoke-test",  "ui-smoke"],                    wsType: "tester"    },
    { id: "M7",  label: "DB Integration",  stageKeys: ["db-integration"],                                wsType: "developer" },
    { id: "M8",  label: "Security OPA",    stageKeys: ["security-review-and-gpa-policies", "security"],  wsType: "developer" },
    { id: "M9",  label: "API Test",        stageKeys: ["api-testing", "api-test"],                       wsType: "tester"    },
    { id: "M11", label: "UI E2E",          stageKeys: ["ui-e2e-testing", "ui-e2e"],                      wsType: "tester"    },
  ] },
  { nodes: [{ id: "M12", label: "Docs & UAT Deploy", stageKeys: [],                                                 wsType: "deploy"   }] },
];

export type TraceTab = "timeline" | "artifacts" | "relations" | "graph" | "coverage" | "grounding" | "matrix";

/** One row in the end-to-end traceability matrix. */
interface MatrixRow {
  userStory: Artifact | null;
  feature:   Artifact | null;
  frd:       Artifact | null;
  lld:       Artifact | null;
  uiCodes:   Artifact[];
  apiCodes:  Artifact[];
  tests:     Artifact[];
  status:    "done" | "partial" | "in-progress" | "pending";
}

/** Workspace Traceability view — 6 tabs covering the full PRD→Test chain. */
@Component({
  selector: "app-workspace-traceability",
  template: `
    <div class="apm-trace-page">
      <!-- Header -->
      <div class="apm-hdr">
        <div class="apm-hdr-icon">↺</div>
        <div>
          <h2>Workspace Traceability</h2>
          <p class="sub">
            <span class="apm-chip">{{ workspaceId() }}</span>
            <span class="apm-chip">{{ data().artifacts.length }} artifacts</span>
            <span class="apm-chip">{{ data().relationships.length }} relationships</span>
            <span class="apm-chip">{{ uniqueKbCards().length }} KB cards</span>
          </p>
        </div>
      </div>

      <!-- Loading / empty -->
      <div class="apm-banner" *ngIf="loading()">Loading traceability data…</div>
      <div class="apm-banner" *ngIf="!loading() && data().relationships.length === 0 && data().kb_grounding.length === 0 && stageMap().size === 0">
        No traceability data yet for this workspace. Data is populated automatically as pipeline stages complete.
      </div>

      <!-- Tab bar -->
      <div class="apm-trace-tabs">
        <button *ngFor="let t of tabs"
                class="apm-trace-tab"
                [class.active]="activeTab() === t.key"
                (click)="activeTab.set(t.key)">
          {{ t.icon }} {{ t.label }}
        </button>
      </div>

      <!-- ── Timeline ──────────────────────────────────────────────────────── -->
      <div class="apm-trace-body" *ngIf="activeTab() === 'timeline'">

        <!-- Pipeline execution summary — always shown when any runs exist -->
        <div class="apm-exec-summary" *ngIf="completedStageCount() > 0 || totalExecutionTime()">
          <div class="apm-exec-stat">
            <span class="apm-exec-icon">✔</span>
            <div>
              <div class="apm-exec-label">Stages Completed</div>
              <div class="apm-exec-value">{{ completedStageCount() }}</div>
            </div>
          </div>
          <ng-container *ngIf="pipelineStartTime() as start">
            <div class="apm-exec-divider"></div>
            <div class="apm-exec-stat">
              <span class="apm-exec-icon">▶</span>
              <div>
                <div class="apm-exec-label">Pipeline Started</div>
                <div class="apm-exec-value">{{ start | date:'MMM d · HH:mm' }}</div>
              </div>
            </div>
          </ng-container>
          <ng-container *ngIf="pipelineEndTime() as end">
            <div class="apm-exec-divider"></div>
            <div class="apm-exec-stat">
              <span class="apm-exec-icon">⏹</span>
              <div>
                <div class="apm-exec-label">Last Completed</div>
                <div class="apm-exec-value">{{ end | date:'MMM d · HH:mm' }}</div>
              </div>
            </div>
          </ng-container>
          <ng-container *ngIf="totalExecutionTime() as total">
            <div class="apm-exec-divider"></div>
            <div class="apm-exec-stat">
              <span class="apm-exec-icon">⏱</span>
              <div>
                <div class="apm-exec-label">Total Execution Time</div>
                <div class="apm-exec-value apm-exec-highlight">{{ total }}</div>
              </div>
            </div>
          </ng-container>
        </div>

        <div class="apm-timeline">
          <div class="apm-tl-item" *ngFor="let s of timelineStages(); let i = index">
            <div class="apm-tl-num" [ngClass]="statusClass(s.status)">{{ i + 1 }}</div>
            <div class="apm-tl-content">

              <!-- Stage name + status badge + duration chip -->
              <div class="apm-tl-head">
                <span class="apm-tl-stage">{{ s.stageName }}</span>
                <span class="apm-badge" [ngClass]="statusClass(s.status)">{{ statusLabel(s.status) }}</span>
                <span class="apm-exec-duration-inline" *ngIf="stageDuration(s.key) as dur">⏱ {{ dur }}</span>
                <span class="apm-exec-duration-inline" *ngIf="s.isAggregate && epicWsDuration(s.wsId) as dur">⏱ {{ dur }}</span>
              </div>

              <!-- ── Debug: shows run lookup state while timing fix is being verified ── -->
              <div class="apm-tl-debug" *ngIf="!stageRun(s.key) && !s.isAggregate">
                ⚙ no run for key <code>{{ s.key }}</code>
                ({{ stageRunMap().size }} runs loaded)
              </div>

              <!-- ── Run timing block (start / end / duration) from pipeline runs ── -->
              <ng-container *ngIf="stageRun(s.key) as run">
                <div class="apm-tl-timing-row">
                  <div class="apm-tl-timing-cell" *ngIf="run.started_at">
                    <span class="apm-tl-timing-icon">▶</span>
                    <div>
                      <div class="apm-tl-timing-label">Started</div>
                      <div class="apm-tl-timing-val">{{ run.started_at | date:'MMM d, y · HH:mm:ss' }}</div>
                    </div>
                  </div>
                  <div class="apm-tl-timing-arrow" *ngIf="run.started_at && run.finished_at">→</div>
                  <div class="apm-tl-timing-cell" *ngIf="run.finished_at">
                    <span class="apm-tl-timing-icon">⏹</span>
                    <div>
                      <div class="apm-tl-timing-label">Completed</div>
                      <div class="apm-tl-timing-val">{{ run.finished_at | date:'MMM d, y · HH:mm:ss' }}</div>
                    </div>
                  </div>
                  <div class="apm-tl-timing-dur" *ngIf="stageDuration(s.key) as dur">
                    <span class="apm-tl-timing-icon">⏱</span>
                    <div>
                      <div class="apm-tl-timing-label">Duration</div>
                      <div class="apm-tl-timing-val apm-tl-timing-dur-val">{{ dur }}</div>
                    </div>
                  </div>
                </div>
              </ng-container>

              <!-- ── Aggregate EPIC timing — workspace-level start/end/duration ── -->
              <ng-container *ngIf="s.isAggregate">
                <div class="apm-tl-timing-row"
                     *ngIf="epicWsFirstStarted(s.wsId) || epicWsLastFinished(s.wsId)">
                  <div class="apm-tl-timing-cell" *ngIf="epicWsFirstStarted(s.wsId) as started">
                    <span class="apm-tl-timing-icon">▶</span>
                    <div>
                      <div class="apm-tl-timing-label">Started</div>
                      <div class="apm-tl-timing-val">{{ started | date:'MMM d, y · HH:mm:ss' }}</div>
                    </div>
                  </div>
                  <div class="apm-tl-timing-arrow"
                       *ngIf="epicWsFirstStarted(s.wsId) && epicWsLastFinished(s.wsId)">→</div>
                  <div class="apm-tl-timing-cell" *ngIf="epicWsLastFinished(s.wsId) as finished">
                    <span class="apm-tl-timing-icon">⏹</span>
                    <div>
                      <div class="apm-tl-timing-label">Completed</div>
                      <div class="apm-tl-timing-val">{{ finished | date:'MMM d, y · HH:mm:ss' }}</div>
                    </div>
                  </div>
                  <div class="apm-tl-timing-dur" *ngIf="epicWsDuration(s.wsId) as dur">
                    <span class="apm-tl-timing-icon">⏱</span>
                    <div>
                      <div class="apm-tl-timing-label">Duration</div>
                      <div class="apm-tl-timing-val apm-tl-timing-dur-val">{{ dur }}</div>
                    </div>
                  </div>
                </div>
              </ng-container>

              <!-- Approval metadata -->
              <div class="apm-tl-detail-grid" *ngIf="s.stage as stage">
                <ng-container *ngIf="stage.approval_persona">
                  <span class="apm-tl-det-label">Approval by</span>
                  <span class="apm-tl-det-val">{{ stage.approval_persona }}</span>
                </ng-container>
                <ng-container *ngIf="stage.reason">
                  <span class="apm-tl-det-label">Reason</span>
                  <span class="apm-tl-det-val apm-muted">{{ stage.reason }}</span>
                </ng-container>
              </div>

              <!-- Artifact metadata: triggered by / reviewed by -->
              <div class="apm-tl-detail-grid" *ngIf="s.artifact as a">
                <ng-container *ngIf="a.triggered_by_name">
                  <span class="apm-tl-det-label">Agent</span>
                  <span class="apm-tl-det-val">
                    {{ a.triggered_by_name }}
                    <span class="apm-muted" *ngIf="a.triggered_by_persona">({{ a.triggered_by_persona }})</span>
                  </span>
                </ng-container>
                <ng-container *ngIf="a.reviewed_by_name">
                  <span class="apm-tl-det-label">Approved by</span>
                  <span class="apm-tl-det-val">{{ a.reviewed_by_name }}</span>
                </ng-container>
                <ng-container *ngIf="a.reviewed_at">
                  <span class="apm-tl-det-label">Approved at</span>
                  <span class="apm-tl-det-val">{{ a.reviewed_at | date:'MMM d, y · HH:mm' }}</span>
                </ng-container>
              </div>

              <!-- Created by / feedback from /artifacts API (richer Artifact type) -->
              <div class="apm-tl-detail-grid" *ngIf="stageLatestArtifact(s.key) as ra">
                <ng-container *ngIf="ra.created_by && !s.artifact?.triggered_by_name">
                  <span class="apm-tl-det-label">Created by</span>
                  <span class="apm-tl-det-val">{{ ra.created_by }}</span>
                </ng-container>
                <ng-container *ngIf="ra.approval_feedback">
                  <span class="apm-tl-det-label">Feedback</span>
                  <span class="apm-tl-det-val apm-muted">{{ ra.approval_feedback }}</span>
                </ng-container>
              </div>

            </div>
            <div class="apm-tl-connector" *ngIf="i < timelineStages().length - 1"></div>
          </div>
        </div>
      </div>

      <!-- ── Artifacts ─────────────────────────────────────────────────────── -->
      <div class="apm-trace-body" *ngIf="activeTab() === 'artifacts'">
        <ng-container *ngIf="stageEntries().length > 0; else noArtifacts">
          <div class="apm-art-stage" *ngFor="let entry of stageEntries()">
            <!-- Stage header row — no status badge, no run ID -->
            <div class="apm-tl-item">
              <div class="apm-tl-num apm-tl-num-neutral">{{ entry.seq }}</div>
              <div class="apm-art-stage-block">
                <!-- Stage label -->
                <div class="apm-art-stage-name">{{ entry.name }}</div>

                <!-- Artifacts inline under stage label -->
                <ng-container *ngIf="artifactsByStage().get(entry.key) as arts">
                  <div class="apm-art-list">
                    <div class="apm-art-row" *ngFor="let a of arts; let idx = index">
                      <span class="apm-art-idx">{{ idx + 1 }}</span>
                      <div class="apm-art-info">
                        <span class="apm-art-type">{{ a.artifact_type || entry.name }}</span>
                        <span class="apm-art-ver" *ngIf="a.version">v{{ a.version }}</span>
                      </div>
                      <div class="apm-art-actions">
                        <a [href]="downloadUrl(a.id)" target="_blank" class="apm-dl-btn apm-dl-primary" title="Download artifact">
                          ⬇ Download
                        </a>
                        <a [href]="exportDocxUrl(a.id)" target="_blank" class="apm-dl-btn apm-dl-secondary" title="Export as DOCX">
                          📄 DOCX
                        </a>
                      </div>
                    </div>
                  </div>
                </ng-container>
                <div class="apm-art-none" *ngIf="!artifactsByStage().get(entry.key)">
                  No artifact yet
                </div>
              </div>
            </div>
          </div>
        </ng-container>
        <ng-template #noArtifacts>
          <div class="apm-empty">No artifacts recorded yet.</div>
        </ng-template>
      </div>

      <!-- ── Relationships — hop-by-hop detail cards ───────────────────────── -->
      <div class="apm-trace-body" *ngIf="activeTab() === 'relations'">

        <!-- Hop-by-hop relationship cards -->
        <div class="apm-rel-hops">
          <div class="apm-rel-hop-card" *ngFor="let h of REL_CHAIN">

            <!-- Hop header — left stage → right stage (no rel-type text) -->
            <div class="apm-rel-hop-hdr">
              <span class="apm-rel-hop-ws apm-rel-ws-{{ wsTypeForStage(h.fromWsLabel) }}">{{ h.fromWsLabel }}</span>
              <span class="apm-rel-hop-stage">{{ h.fromLabel }}</span>
              <span class="apm-rel-hop-arrow apm-rel-type-{{ h.relType }}">→</span>
              <span class="apm-rel-hop-stage">{{ h.toLabel }}</span>
              <span class="apm-rel-hop-ws apm-rel-ws-{{ wsTypeForStage(h.toWsLabel) }}">{{ h.toWsLabel }}</span>
            </div>

            <!-- Two-column artifact mapping: FROM | rel-type | TO -->
            <div class="apm-rel-hop-body">
              <div class="apm-rel-hop-col">
                <div class="apm-rel-col-hdr">{{ h.fromLabel }} artifacts</div>
                <ng-container *ngIf="relArtifactsForStage(h.fromStage).length > 0; else fromPending">
                  <div class="apm-rel-art-row" *ngFor="let a of relArtifactsForStage(h.fromStage)">
                    <span class="apm-rel-art-dot"></span>
                    <span class="apm-rel-art-name">{{ a.artifact_type || (a.id | slice:0:18) }}</span>
                    <div class="apm-rel-art-btns">
                      <a [href]="downloadUrl(a.id)" target="_blank" class="apm-dl-btn apm-dl-primary apm-dl-sm">⬇ Download</a>
                      <a [href]="exportDocxUrl(a.id)" target="_blank" class="apm-dl-btn apm-dl-secondary apm-dl-sm">📄 DOCX</a>
                    </div>
                  </div>
                </ng-container>
                <ng-template #fromPending>
                  <span class="apm-rel-pending">— no artifacts yet</span>
                </ng-template>
              </div>

              <div class="apm-rel-center-col">
                <div class="apm-rel-center-line"></div>
                <div class="apm-rel-center-badge apm-rel-type-{{ h.relType }}">{{ h.relType }}</div>
                <div class="apm-rel-center-line"></div>
              </div>

              <div class="apm-rel-hop-col">
                <div class="apm-rel-col-hdr">{{ h.toLabel }} artifacts</div>
                <ng-container *ngIf="relArtifactsForStage(h.toStage).length > 0; else toPending">
                  <div class="apm-rel-art-row" *ngFor="let a of relArtifactsForStage(h.toStage)">
                    <span class="apm-rel-art-dot"></span>
                    <span class="apm-rel-art-name">{{ a.artifact_type || (a.id | slice:0:18) }}</span>
                    <div class="apm-rel-art-btns">
                      <a [href]="downloadUrl(a.id)" target="_blank" class="apm-dl-btn apm-dl-primary apm-dl-sm">⬇ Download</a>
                      <a [href]="exportDocxUrl(a.id)" target="_blank" class="apm-dl-btn apm-dl-secondary apm-dl-sm">📄 DOCX</a>
                    </div>
                  </div>
                </ng-container>
                <ng-template #toPending>
                  <span class="apm-rel-pending">— no artifacts yet</span>
                </ng-template>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- ── Trace Graph — End-to-End Artifact Lineage (horizontal flow) ───── -->
      <div class="apm-trace-body" *ngIf="activeTab() === 'graph'">

        <!-- Banner -->
        <div class="apm-flow-banner">
          <span style="font-size:22px">🔗</span>
          <div>
            <div class="apm-flow-banner-title">END-TO-END ARTIFACT LINEAGE — UW CREDIT RISK PIPELINE</div>
            <div class="apm-flow-banner-sub">Artifact chain · left to right · arrows show dependency direction</div>
          </div>
        </div>

        <!-- Horizontal scrollable pipeline flow -->
        <div class="apm-flow-wrap">
          <div class="apm-flow-track">

            <!-- Root: KB / RED -->
            <div class="apm-flow-step">
              <div class="apm-flow-node apm-flow-root">
                <div class="apm-flow-node-id">KB</div>
                <div class="apm-flow-node-label">KB / RED</div>
                <div class="apm-flow-node-sub">Source</div>
              </div>
            </div>
            <div class="apm-flow-connector"><div class="apm-flow-line"></div><div class="apm-flow-arrowhead">▶</div></div>

            <!-- Pipeline steps -->
            <ng-container *ngFor="let step of FLOW_STEPS; let last = last">
              <div class="apm-flow-step">
                <div class="apm-flow-node"
                     *ngFor="let n of step.nodes"
                     [ngClass]="['apm-flow-' + n.wsType, n.isLeaf ? 'apm-flow-leaf' : '']">

                  <!-- Step ID + label -->
                  <div class="apm-flow-node-id">{{ n.id }}</div>
                  <div class="apm-flow-node-label">{{ n.label }}</div>

                  <!-- Fan-out indicator -->
                  <div class="apm-flow-fanout" *ngIf="n.isFanOut">× N workspaces</div>

                  <!-- Leaf marker -->
                  <div class="apm-flow-leaf-tag" *ngIf="n.isLeaf">★ leaf</div>

                  <!-- Artifact status dot -->
                  <div class="apm-flow-art-row">
                    <ng-container *ngIf="lineageArtifacts(n.stageKeys) as arts">
                      <span class="apm-flow-art-dot" [class.apm-flow-art-live]="arts.length > 0"></span>
                      <span class="apm-flow-art-label" *ngIf="arts.length > 0">{{ arts.length }} artifact{{ arts.length !== 1 ? 's' : '' }}</span>
                      <span class="apm-flow-art-label apm-flow-art-grey" *ngIf="arts.length === 0 && n.stageKeys.length > 0">pending</span>
                    </ng-container>
                  </div>
                </div>
              </div>

              <!-- Arrow connector between steps -->
              <div class="apm-flow-connector" *ngIf="!last">
                <div class="apm-flow-line"></div>
                <div class="apm-flow-arrowhead">▶</div>
              </div>
            </ng-container>

          </div>
        </div>

        <!-- Color-coded legend -->
        <div class="apm-flow-legend">
          <span class="apm-flow-leg-item"><span class="apm-flow-leg-swatch apm-flow-leg-global"></span>Global</span>
          <span class="apm-flow-leg-item"><span class="apm-flow-leg-swatch apm-flow-leg-arch"></span>Architecture</span>
          <span class="apm-flow-leg-item"><span class="apm-flow-leg-swatch apm-flow-leg-mini"></span>Mini / EPIC</span>
          <span class="apm-flow-leg-item"><span class="apm-flow-leg-swatch apm-flow-leg-developer"></span>Developer</span>
          <span class="apm-flow-leg-item"><span class="apm-flow-leg-swatch apm-flow-leg-tester"></span>Tester</span>
          <span class="apm-flow-leg-item"><span class="apm-flow-leg-swatch apm-flow-leg-deploy"></span>Deploy</span>
          <span class="apm-flow-leg-item" style="margin-left:auto">
            <span class="apm-flow-art-dot apm-flow-art-live"></span>&nbsp;= artifacts available&nbsp;&nbsp;
            <span class="apm-flow-art-dot"></span>&nbsp;= pending
          </span>
        </div>
      </div>

      <!-- ── Coverage Matrix ───────────────────────────────────────────────── -->
      <div class="apm-trace-body" *ngIf="activeTab() === 'coverage'">
        <div class="apm-matrix-wrap">
          <table class="apm-matrix" *ngIf="uniqueKbCards().length > 0; else noMatrix">
            <thead>
              <tr>
                <th class="apm-matrix-sticky">KB Card</th>
                <th *ngFor="let s of CHAIN_STAGES">{{ stageLabel(s) }}</th>
                <th>Coverage</th>
              </tr>
            </thead>
            <tbody>
              <tr *ngFor="let card of uniqueKbCards()">
                <td class="apm-matrix-sticky apm-mono sm">{{ card | slice:0:20 }}…</td>
                <td *ngFor="let s of CHAIN_STAGES" class="apm-matrix-cell" [class.covered]="hasCoverage(card, s)">
                  {{ hasCoverage(card, s) ? '✓' : '—' }}
                </td>
                <td class="apm-matrix-pct">{{ coveragePct(card) }}%</td>
              </tr>
            </tbody>
          </table>
          <ng-template #noMatrix>
            <div class="apm-empty">No KB card grounding data yet. Coverage is tracked via GROUNDS links from fe_artifact_links.</div>
          </ng-template>
        </div>
      </div>

      <!-- ── KB Grounding ──────────────────────────────────────────────────── -->
      <div class="apm-trace-body" *ngIf="activeTab() === 'grounding'">
        <div class="apm-kb-grid" *ngIf="data().kb_grounding.length > 0; else noGrounding">
          <div class="apm-kb-card" *ngFor="let g of data().kb_grounding">
            <div class="apm-kb-card-id">{{ g.to_kb_card_id | slice:0:24 }}…</div>
            <div class="apm-kb-pct-bar">
              <div class="apm-kb-pct-fill" [style.width.%]="groundingPct(g)"></div>
            </div>
            <div class="apm-kb-pct-label">{{ groundingPct(g) }}% grounded</div>
            <div class="apm-muted" style="font-size:10px;margin-top:4px">{{ g.coverage_count }} link{{ g.coverage_count === 1 ? '' : 's' }}</div>
          </div>
        </div>
        <ng-template #noGrounding>
          <div class="apm-empty">No KB grounding data yet. Grounding is recorded when artifacts cite KB cards during generation.</div>
        </ng-template>
      </div>

      <!-- ── Trace Matrix ───────────────────────────────────────────────────── -->
      <div class="apm-trace-body" *ngIf="activeTab() === 'matrix'">
        <!-- Banner -->
        <div class="apm-mat-banner">
          <div>
            <strong style="font-size:15px">📋 End-to-End Traceability Matrix</strong>
            <div style="margin-top:4px;font-size:13px;color:rgba(255,255,255,.75)">
              FRD → Feature → User Story → LLD/TDD → Angular Code → Java Code → Tests
            </div>
          </div>
          <span class="apm-mat-count">{{ matrixRows().length }} row{{ matrixRows().length === 1 ? '' : 's' }}</span>
        </div>

        <!-- Empty state -->
        <div *ngIf="!loading() && matrixRows().length === 0" class="apm-empty" style="padding:32px 0;text-align:center">
          No user-story or feature artifacts yet — the matrix populates automatically as pipeline stages complete.
        </div>

        <!-- Matrix table -->
        <div class="apm-mat-scroll" *ngIf="matrixRows().length > 0">
          <table class="apm-mat-table">
            <thead>
              <tr>
                <th class="apm-mat-th"><span class="apm-mat-pill frd">FRD</span> FRD Ref</th>
                <th class="apm-mat-th"><span class="apm-mat-pill feat">FEAT</span> Feature</th>
                <th class="apm-mat-th"><span class="apm-mat-pill us">US</span> User Story</th>
                <th class="apm-mat-th"><span class="apm-mat-pill lld">LLD</span> LLD / TDD</th>
                <th class="apm-mat-th"><span class="apm-mat-pill ui">UI</span> Angular Code</th>
                <th class="apm-mat-th"><span class="apm-mat-pill api">API</span> Java Code</th>
                <th class="apm-mat-th"><span class="apm-mat-pill test">TEST</span> Tests</th>
                <th class="apm-mat-th apm-mat-stat-col">Status</th>
              </tr>
            </thead>
            <tbody>
              <tr *ngFor="let row of matrixRows(); let i = index" class="apm-mat-row">

                <!-- FRD -->
                <td class="apm-mat-td">
                  <span *ngIf="!row.frd" class="apm-mat-none">—</span>
                  <a *ngIf="row.frd" class="apm-mat-art-link" [href]="downloadUrl(row.frd.id)" target="_blank">
                    {{ row.frd.artifact_type || 'FRD' }}
                  </a>
                </td>

                <!-- Feature -->
                <td class="apm-mat-td">
                  <span *ngIf="!row.feature" class="apm-mat-none">—</span>
                  <a *ngIf="row.feature" class="apm-mat-art-link feat" [href]="downloadUrl(row.feature.id)" target="_blank">
                    {{ row.feature.artifact_type || 'Feature TDD' }}
                  </a>
                </td>

                <!-- User Story -->
                <td class="apm-mat-td">
                  <span *ngIf="!row.userStory" class="apm-mat-none">—</span>
                  <a *ngIf="row.userStory" class="apm-mat-art-link us" [href]="downloadUrl(row.userStory.id)" target="_blank">
                    {{ row.userStory.artifact_type || 'User Story TDD' }}
                  </a>
                </td>

                <!-- LLD / TDD -->
                <td class="apm-mat-td">
                  <span *ngIf="!row.lld" class="apm-mat-none">—</span>
                  <a *ngIf="row.lld" class="apm-mat-art-link lld" [href]="downloadUrl(row.lld.id)" target="_blank">
                    {{ row.lld.artifact_type || 'LLD / TDD' }}
                  </a>
                </td>

                <!-- Angular UI Code -->
                <td class="apm-mat-td">
                  <span *ngIf="!row.uiCodes.length" class="apm-mat-none">—</span>
                  <div *ngFor="let a of row.uiCodes" class="apm-mat-multi">
                    <a class="apm-mat-art-link ui" [href]="downloadUrl(a.id)" target="_blank">
                      {{ a.artifact_type || 'Angular Code' }}
                    </a>
                    <a *ngIf="a.pr_url" class="apm-mat-pr-link" [href]="a.pr_url" target="_blank">↗ PR</a>
                  </div>
                </td>

                <!-- Java API Code -->
                <td class="apm-mat-td">
                  <span *ngIf="!row.apiCodes.length" class="apm-mat-none">—</span>
                  <div *ngFor="let a of row.apiCodes" class="apm-mat-multi">
                    <a class="apm-mat-art-link api" [href]="downloadUrl(a.id)" target="_blank">
                      {{ a.artifact_type || 'Spring Boot API' }}
                    </a>
                    <a *ngIf="a.pr_url" class="apm-mat-pr-link" [href]="a.pr_url" target="_blank">↗ PR</a>
                  </div>
                </td>

                <!-- Tests -->
                <td class="apm-mat-td">
                  <span *ngIf="!row.tests.length" class="apm-mat-none">—</span>
                  <div *ngFor="let a of row.tests" class="apm-mat-multi">
                    <a class="apm-mat-art-link test" [href]="downloadUrl(a.id)" target="_blank">
                      {{ a.artifact_type || stageLabel(a.stage_key || '') }}
                    </a>
                  </div>
                </td>

                <!-- Status -->
                <td class="apm-mat-td apm-mat-stat-col">
                  <span class="apm-mat-status" [class]="'apm-mat-s-' + row.status">
                    {{ matStatusLabel(row.status) }}
                  </span>
                </td>

              </tr>
            </tbody>
          </table>
        </div>
      </div>

    </div>
  `,
  styles: [`
    .apm-trace-page { padding: 16px 24px; }

    /* Tab bar */
    .apm-trace-tabs { display:flex; gap:4px; margin:16px 0 0; border-bottom:1px solid var(--ax-color-border,#dde); padding-bottom:0; }
    .apm-trace-tab  { background:none; border:none; padding:10px 18px; cursor:pointer; font-size:14px;
                      color:var(--ax-color-muted,#666); border-bottom:2px solid transparent; margin-bottom:-1px; }
    .apm-trace-tab.active { color:var(--ax-color-primary,#235); border-bottom-color:var(--ax-color-primary,#235); font-weight:600; }

    /* Tab body */
    .apm-trace-body { padding-top:16px; }

    /* Timeline */
    .apm-timeline { position:relative; }
    .apm-tl-item  { display:flex; align-items:flex-start; gap:14px; margin-bottom:12px; position:relative; }
    .apm-tl-num      { min-width:34px; height:34px; border-radius:50%; background:#adb5bd; color:#fff;
                       display:flex; align-items:center; justify-content:center; font-size:14px; font-weight:700; flex-shrink:0; }
    .apm-tl-num.good { background:#28a745; }
    .apm-tl-num.warn { background:#ffc107; color:#333; }
    .apm-tl-num.bad  { background:#dc3545; }
    .apm-tl-content { flex:1; background:var(--ax-bg-card,#fff); border:1px solid var(--ax-color-border,#dde); border-radius:8px; padding:14px 18px; }
    .apm-tl-head  { display:flex; align-items:center; gap:10px; }
    .apm-tl-stage { font-weight:600; font-size:16px; }
    .apm-tl-meta  { font-size:13px; color:#555; margin-top:4px; }
    .apm-tl-sub   { margin-top:6px; display:flex; gap:6px; flex-wrap:wrap; }
    .apm-tl-connector { position:absolute; left:16px; top:42px; width:2px; height:18px; background:var(--ax-color-border,#dde); }

    /* ── Execution summary bar ─────────────────────────────────────────────── */
    .apm-exec-summary   { display:flex; align-items:center; flex-wrap:wrap; gap:0;
                          background:linear-gradient(135deg,#0d3c8f 0%,#1a5cbe 100%);
                          color:#fff; border-radius:10px; padding:16px 24px; margin-bottom:20px;
                          box-shadow:0 2px 8px rgba(13,60,143,.25); }
    .apm-exec-stat      { display:flex; align-items:center; gap:10px; padding:0 20px; }
    .apm-exec-stat:first-child { padding-left:0; }
    .apm-exec-divider   { width:1px; height:40px; background:rgba(255,255,255,.25); flex-shrink:0; }
    .apm-exec-icon      { font-size:20px; opacity:.85; }
    .apm-exec-label     { font-size:11px; color:rgba(255,255,255,.7); text-transform:uppercase;
                          letter-spacing:.5px; font-weight:600; }
    .apm-exec-value     { font-size:18px; font-weight:700; color:#fff; margin-top:2px; }
    .apm-exec-highlight { font-size:20px; color:#7ef7c4; }

    /* Per-stage duration badge — sits right after the status badge */
    .apm-exec-duration-inline { font-size:12px; font-weight:600; color:#0d3c8f;
                                background:#e8eeff; border:1px solid #c9d8ff; border-radius:5px;
                                padding:2px 9px; white-space:nowrap; }

    /* ── Stage timing row (start / end / duration) ──────────────────────── */
    .apm-tl-timing-row   { display:flex; align-items:center; flex-wrap:wrap; gap:0;
                           background:#f0f7ff; border:1px solid #c5d8f5; border-radius:8px;
                           padding:10px 16px; margin-top:10px; }
    .apm-tl-timing-cell  { display:flex; align-items:center; gap:8px; padding:0 14px; }
    .apm-tl-timing-cell:first-child { padding-left:0; }
    .apm-tl-timing-dur   { display:flex; align-items:center; gap:8px; padding:0 14px;
                           margin-left:auto; border-left:1px solid #c5d8f5; }
    .apm-tl-timing-arrow { font-size:16px; color:#0d3c8f; opacity:.5; padding:0 6px; }
    .apm-tl-timing-icon  { font-size:15px; color:#0d3c8f; flex-shrink:0; }
    .apm-tl-timing-label { font-size:10px; color:#7a9cbf; text-transform:uppercase;
                           letter-spacing:.4px; font-weight:700; }
    .apm-tl-timing-val   { font-size:13px; font-weight:600; color:#0d2b5e; margin-top:1px; }
    .apm-tl-timing-dur-val { font-size:15px; font-weight:800; color:#0d3c8f; }

    /* Timeline detail grid: label | value pairs */
    .apm-tl-detail-grid { display:grid; grid-template-columns:120px 1fr; gap:4px 14px; margin-top:10px; font-size:13px; }
    .apm-tl-det-label   { color:#888; font-weight:600; align-self:start; white-space:nowrap; }
    .apm-tl-det-val     { color:#333; }

    /* Chain breadcrumb */
    .apm-chain-crumb { display:flex; align-items:center; flex-wrap:wrap; gap:4px; background:var(--ax-bg-card,#fff);
                       border:1px solid var(--ax-color-border,#dde); border-radius:8px; padding:12px 16px; }
    .apm-chain-node  { font-size:13px; font-weight:600; color:var(--ax-color-primary,#235); }
    .apm-chain-arrow { font-size:13px; color:#888; }

    /* ── Pipeline Flow — Trace Graph + Relationship chain ─────────────────── */

    /* Banner */
    .apm-flow-banner       { display:flex; align-items:center; gap:14px; background:#0d1b2a; color:#fff;
                             border-radius:10px; padding:14px 20px; margin-bottom:16px; }
    .apm-flow-banner-title { font-size:14px; font-weight:700; letter-spacing:.3px; }
    .apm-flow-banner-sub   { font-size:11px; color:#7fa8cc; margin-top:2px; }

    /* Outer scroll wrapper */
    .apm-flow-wrap  { overflow-x:auto; background:#f6f8ff; border:1px solid #dde8ff;
                      border-radius:10px; padding:22px 20px 18px; }

    /* Horizontal track */
    .apm-flow-track { display:flex; align-items:flex-start; gap:0; min-width:max-content; }

    /* One pipeline step = vertical stack of nodes */
    .apm-flow-step  { display:flex; flex-direction:column; gap:6px; }

    /* Connector arrow between steps */
    .apm-flow-connector { display:flex; align-items:center; align-self:flex-start;
                          margin-top:22px; flex-shrink:0; }
    .apm-flow-line      { width:18px; height:2px; background:#b0bbcc; }
    .apm-flow-arrowhead { font-size:12px; color:#b0bbcc; margin-left:-2px; line-height:1; }

    /* Individual node card */
    .apm-flow-node       { border:2px solid #c8d4e8; border-radius:10px; padding:10px 14px;
                           min-width:100px; max-width:128px; text-align:center; background:#fff;
                           transition:box-shadow .15s, border-color .15s; }
    .apm-flow-node:hover { box-shadow:0 3px 10px rgba(0,0,0,.12); }

    /* Leaf node — dashed border, softer */
    .apm-flow-leaf { border-style:dashed; opacity:.85; }
    .apm-flow-leaf-tag { font-size:9px; color:#e5a000; font-weight:700;
                         margin-top:3px; letter-spacing:.3px; }

    /* Node ID (G1, G2, M1 …) */
    .apm-flow-node-id  { font-size:9px; font-weight:800; font-family:'Courier New',monospace;
                         letter-spacing:.6px; margin-bottom:4px; }

    /* Node label */
    .apm-flow-node-label { font-size:12px; font-weight:700; color:#1a1a2e; line-height:1.25; }

    /* Sub-label (KB root) */
    .apm-flow-node-sub { font-size:10px; color:#7fa8cc; margin-top:2px; }

    /* Fan-out badge */
    .apm-flow-fanout { font-size:9px; font-weight:800; color:#c0392b;
                       background:#fdecea; border-radius:4px; padding:1px 6px;
                       margin-top:4px; display:inline-block; }

    /* Artifact status row */
    .apm-flow-art-row   { display:flex; align-items:center; justify-content:center; gap:4px; margin-top:7px; }
    .apm-flow-art-dot   { width:8px; height:8px; border-radius:50%; background:#ccc; flex-shrink:0; }
    .apm-flow-art-dot.apm-flow-art-live { background:#28a745; }
    .apm-flow-art-label { font-size:10px; color:#555; }
    .apm-flow-art-grey  { color:#bbb; }

    /* Color coding by workspace tier — border + ID colour */
    .apm-flow-root     { border-color:#0d1b2a; background:#0d1b2a; }
    .apm-flow-root .apm-flow-node-id, .apm-flow-root .apm-flow-node-label { color:#7fa8cc; }

    .apm-flow-global    { border-color:#0d3c8f; }
    .apm-flow-global .apm-flow-node-id { color:#0d3c8f; }

    .apm-flow-arch      { border-color:#198754; }
    .apm-flow-arch .apm-flow-node-id { color:#198754; }

    .apm-flow-mini      { border-color:#e5a000; }
    .apm-flow-mini .apm-flow-node-id { color:#e5a000; }

    .apm-flow-developer { border-color:#6f42c1; }
    .apm-flow-developer .apm-flow-node-id { color:#6f42c1; }

    .apm-flow-tester    { border-color:#d4670c; }
    .apm-flow-tester .apm-flow-node-id { color:#d4670c; }

    .apm-flow-deploy    { border-color:#0dcaf0; background:#e3fafd; }
    .apm-flow-deploy .apm-flow-node-id { color:#0a7a96; }

    /* Legend */
    .apm-flow-legend    { display:flex; align-items:center; gap:18px; flex-wrap:wrap;
                          margin-top:14px; padding-top:12px; border-top:1px solid #dde8ff; }
    .apm-flow-leg-item  { display:flex; align-items:center; gap:6px; font-size:12px; color:#555; }
    .apm-flow-leg-swatch { width:14px; height:14px; border-radius:3px; border:2.5px solid; }
    .apm-flow-leg-global   { border-color:#0d3c8f; }
    .apm-flow-leg-arch     { border-color:#198754; }
    .apm-flow-leg-mini     { border-color:#e5a000; }
    .apm-flow-leg-developer{ border-color:#6f42c1; }
    .apm-flow-leg-tester   { border-color:#d4670c; }
    .apm-flow-leg-deploy   { border-color:#0dcaf0; }

    /* Workspace tier pills in Relationships hop header */
    .apm-rel-hop-ws     { font-size:10px; padding:2px 9px; border-radius:10px; font-weight:700; }
    .apm-rel-ws-global  { background:#0d3c8f; color:#fff; }
    .apm-rel-ws-arch    { background:#198754; color:#fff; }
    .apm-rel-ws-mini    { background:#e5a000; color:#333; }
    .apm-rel-ws-developer { background:#6f42c1; color:#fff; }
    .apm-rel-ws-tester  { background:#d4670c; color:#fff; }
    .apm-rel-ws-deploy  { background:#0dcaf0; color:#333; }

    /* Coverage matrix */
    .apm-matrix-wrap { overflow-x:auto; }
    .apm-matrix      { border-collapse:collapse; font-size:13px; width:100%; }
    .apm-matrix th, .apm-matrix td { padding:8px 12px; border:1px solid var(--ax-color-border,#dde); text-align:center; }
    .apm-matrix th   { background:var(--ax-bg-subtle,#f5f5f8); font-weight:600; }
    .apm-matrix-sticky { position:sticky; left:0; background:var(--ax-bg-card,#fff); text-align:left !important; min-width:140px; }
    .apm-matrix-cell.covered { color:#28a745; font-weight:700; }
    .apm-matrix-pct  { font-weight:700; color:var(--ax-color-primary,#235); }

    /* KB grounding grid */
    .apm-kb-grid   { display:grid; grid-template-columns:repeat(auto-fill,minmax(200px,1fr)); gap:14px; }
    .apm-kb-card   { background:var(--ax-bg-card,#fff); border:1px solid var(--ax-color-border,#dde); border-radius:8px; padding:14px; }
    .apm-kb-card-id { font-size:12px; font-weight:600; color:var(--ax-color-primary,#235); margin-bottom:8px; word-break:break-all; }
    .apm-kb-pct-bar { height:7px; background:#eee; border-radius:3px; overflow:hidden; margin-bottom:4px; }
    .apm-kb-pct-fill { height:100%; background:var(--ax-color-primary,#235); border-radius:3px; }
    .apm-kb-pct-label { font-size:13px; font-weight:700; }

    /* ── Download buttons (shared across Artifacts + Relationships tabs) ─── */
    .apm-dl-btn          { display:inline-flex; align-items:center; gap:5px; text-decoration:none;
                           border-radius:6px; font-weight:600; white-space:nowrap; transition:all .15s; }
    .apm-dl-primary      { font-size:12px; padding:5px 14px;
                           background:#0d3c8f; color:#fff; border:1.5px solid #0d3c8f; }
    .apm-dl-primary:hover{ background:#0a2d6b; border-color:#0a2d6b; }
    .apm-dl-secondary    { font-size:12px; padding:5px 12px;
                           background:#fff; color:#495057; border:1.5px solid #adb5bd; }
    .apm-dl-secondary:hover { background:#f1f3f5; border-color:#868e96; }
    /* Smaller variant for Relationships hop body */
    .apm-dl-sm           { font-size:11px; padding:3px 10px; }

    /* Artifacts tab — stage block with inline artifacts */
    .apm-tl-num-neutral  { background:#6c757d !important; }
    .apm-art-stage       { margin-bottom:8px; }
    .apm-art-stage-block { flex:1; background:var(--ax-bg-card,#fff); border:1px solid var(--ax-color-border,#dde);
                           border-radius:8px; padding:10px 16px; }
    .apm-art-stage-name  { font-size:15px; font-weight:600; color:#222; margin-bottom:6px; }
    .apm-art-list        { display:flex; flex-direction:column; gap:6px; }
    .apm-art-row         { display:flex; align-items:center; gap:12px; background:var(--ax-bg-subtle,#f5f5f8);
                           border:1px solid var(--ax-color-border,#eee); border-radius:7px; padding:8px 14px; }
    .apm-art-idx         { min-width:22px; height:22px; background:#dee2e6; border-radius:50%;
                           display:flex; align-items:center; justify-content:center;
                           font-size:11px; font-weight:700; color:#555; flex-shrink:0; }
    .apm-art-info        { flex:1; display:flex; align-items:center; gap:8px; min-width:0; }
    .apm-art-type        { font-size:14px; color:#222; font-weight:600; }
    .apm-art-ver         { font-size:11px; color:#888; background:#e9ecef; border-radius:4px; padding:1px 7px; }
    .apm-art-actions     { display:flex; gap:6px; flex-shrink:0; }
    .apm-art-none        { margin-top:4px; font-size:12px; color:#aaa; font-style:italic; }

    /* Relationships tab — hop-by-hop chain */
    /* Breadcrumb */
    .apm-rel-breadcrumb  { display:flex; align-items:center; flex-wrap:wrap; gap:4px;
                           background:var(--ax-bg-subtle,#f5f5f8); border:1px solid var(--ax-color-border,#dde);
                           border-radius:8px; padding:12px 16px; margin-bottom:16px; }
    .apm-rel-bc-node     { font-size:13px; font-weight:700; color:var(--ax-color-primary,#0d3c8f); }
    .apm-rel-bc-type     { font-size:12px; color:#888; font-style:italic; }

    /* Hop cards */
    .apm-rel-hops        { display:flex; flex-direction:column; gap:10px; }
    .apm-rel-hop-card    { border:1px solid var(--ax-color-border,#dde); border-radius:8px;
                           background:var(--ax-bg-card,#fff); overflow:hidden; }

    /* Hop header */
    .apm-rel-hop-hdr     { display:flex; align-items:center; gap:10px; flex-wrap:wrap;
                           padding:10px 16px; background:var(--ax-bg-subtle,#f8f9fa);
                           border-bottom:1px solid var(--ax-color-border,#eee); }
    .apm-rel-hop-stage   { font-size:14px; font-weight:700; color:#222; }

    /* Plain arrow — colour changes by rel type, no text label */
    .apm-rel-hop-arrow               { font-size:20px; font-weight:300; line-height:1; }
    .apm-rel-type-derives_from       { color:#198754; }
    .apm-rel-type-traces_to          { color:#0d6efd; }
    .apm-rel-type-implements         { color:#6f42c1; }
    .apm-rel-type-tests              { color:#d4670c; }

    /* Center badge inside hop body (keeps rel type text for detail view) */
    .apm-rel-center-badge            { font-size:10px; font-weight:700; padding:3px 8px; border-radius:10px;
                                       background:#e9ecef; color:#444; white-space:nowrap; text-align:center; margin:4px 0; }
    .apm-rel-center-badge.apm-rel-type-derives_from { background:#d1e7dd; color:#0a3622; }
    .apm-rel-center-badge.apm-rel-type-traces_to    { background:#cfe2ff; color:#084298; }
    .apm-rel-center-badge.apm-rel-type-implements   { background:#e2d9f3; color:#432874; }
    .apm-rel-center-badge.apm-rel-type-tests        { background:#fff3cd; color:#664d03; }

    .apm-rel-ws-pill     { font-size:11px; background:#e9ecef; color:#555;
                           border-radius:4px; padding:2px 8px; white-space:nowrap; }
    .apm-rel-ws-arrow    { font-size:12px; color:#aaa; }

    /* Hop body — two columns with center arrow */
    .apm-rel-hop-body    { display:grid; grid-template-columns:1fr 140px 1fr; gap:0;
                           padding:12px 16px; align-items:start; }
    .apm-rel-hop-col     { display:flex; flex-direction:column; gap:6px; }
    .apm-rel-col-hdr     { font-size:12px; font-weight:700; color:#555; text-transform:uppercase;
                           letter-spacing:.4px; margin-bottom:4px; }
    .apm-rel-art-row     { display:flex; align-items:center; gap:8px; flex-wrap:wrap;
                           background:#f0f4ff; border:1px solid #c9d8ff; border-radius:8px; padding:7px 12px; }
    .apm-rel-art-dot     { width:8px; height:8px; border-radius:50%; background:#0d6efd; flex-shrink:0; }
    .apm-rel-art-name    { flex:1; font-size:13px; font-weight:500; color:#1a1a2e; min-width:0;
                           overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
    .apm-rel-art-btns    { display:flex; gap:5px; flex-shrink:0; }
    .apm-rel-pending     { font-size:12px; color:#aaa; font-style:italic; margin-top:4px; }

    /* Center relationship-type column */
    .apm-rel-center-col  { display:flex; flex-direction:column; align-items:center; gap:0;
                           padding:0 12px; margin-top:28px; }
    .apm-rel-center-line { width:2px; height:20px; background:#dee2e6; }
    .apm-rel-center-badge { font-size:10px; font-weight:700; padding:3px 8px; border-radius:10px;
                            background:#e9ecef; color:#444; white-space:nowrap; text-align:center; margin:4px 0; }

    /* Trace graph pills */
    .apm-graph-stage-pill { display:block; padding:6px 10px; border-radius:6px; font-size:12px;
                            font-weight:600; margin-bottom:4px; background:#e9ecef; color:#333; }
    .apm-graph-stage-pill.good { background:#d4edda; color:#155724; }
    .apm-graph-stage-pill.warn { background:#fff3cd; color:#856404; }
    .apm-graph-stage-pill.bad  { background:#f8d7da; color:#721c24; }

    /* Shared helpers */
    .apm-badge.good { background:#d4edda; color:#155724; }
    .apm-badge.bad  { background:#f8d7da; color:#721c24; }
    .apm-badge.warn { background:#fff3cd; color:#856404; }
    .apm-badge.idle { background:#e2e3e5; color:#383d41; }
    .apm-badge      { display:inline-block; padding:3px 10px; border-radius:10px; font-size:12px; font-weight:600; }
    .apm-mono       { font-family:monospace; font-size:13px; }
    .apm-mono.sm    { font-size:12px; }
    .apm-muted      { color:#888; }
    .apm-tl-debug   { font-size:11px; color:#bbb; margin:2px 0 4px; }
    .apm-empty      { color:#888; font-size:14px; padding:28px 0; }
    .apm-table      { border-collapse:collapse; width:100%; font-size:13px; }
    .apm-table th, .apm-table td { padding:8px 12px; border-bottom:1px solid var(--ax-color-border,#dde); text-align:left; }
    .apm-table th   { font-weight:600; background:var(--ax-bg-subtle,#f5f5f8); }

    /* ── Trace Matrix tab ─────────────────────────────────────────────────── */
    .apm-mat-banner   { display:flex; align-items:center; justify-content:space-between;
                        background:linear-gradient(135deg,#0d2b5e 0%,#1a5cbe 100%);
                        color:#fff; border-radius:10px; padding:14px 20px; margin-bottom:16px;
                        box-shadow:0 2px 8px rgba(13,60,143,.25); }
    .apm-mat-count    { font-size:13px; font-weight:700; background:rgba(255,255,255,.15);
                        border-radius:10px; padding:4px 14px; white-space:nowrap; }
    .apm-mat-scroll   { overflow-x:auto; border:1px solid var(--ax-color-border,#dde);
                        border-radius:10px; }
    .apm-mat-table    { border-collapse:collapse; width:100%; font-size:13px; min-width:960px; }
    .apm-mat-th       { padding:10px 14px; background:var(--ax-bg-subtle,#f5f5f8);
                        font-weight:700; font-size:12px; color:#444; text-align:left;
                        border-bottom:2px solid var(--ax-color-border,#dde); white-space:nowrap; }
    .apm-mat-td       { padding:10px 14px; border-bottom:1px solid var(--ax-color-border,#eee);
                        vertical-align:top; }
    .apm-mat-row:last-child .apm-mat-td { border-bottom:none; }
    .apm-mat-row:hover .apm-mat-td      { background:#f8faff; }
    .apm-mat-stat-col { width:110px; text-align:center; }
    .apm-mat-none     { color:#bbb; font-size:13px; }
    .apm-mat-multi    { margin-bottom:5px; display:flex; align-items:center; gap:6px; }
    .apm-mat-multi:last-child { margin-bottom:0; }

    /* Artifact link chips */
    .apm-mat-art-link       { display:inline-block; font-size:12px; font-weight:600;
                              text-decoration:none; border-radius:5px; padding:3px 10px;
                              max-width:190px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
                              color:#0d3c8f; background:#e8f0fe; border:1px solid #c5d3f5; }
    .apm-mat-art-link:hover { background:#d0e0fc; border-color:#a0b8f0; }
    .apm-mat-art-link.feat  { color:#6d3a00; background:#fff3cd; border-color:#f0d07a; }
    .apm-mat-art-link.us    { color:#0a3050; background:#cfe8ff; border-color:#87bfee; }
    .apm-mat-art-link.lld   { color:#3b1f6e; background:#ede9f7; border-color:#c5b6e8; }
    .apm-mat-art-link.ui    { color:#084298; background:#cfe2ff; border-color:#9ec5fe; }
    .apm-mat-art-link.api   { color:#7a2b00; background:#ffe5d0; border-color:#f4b98a; }
    .apm-mat-art-link.test  { color:#721c24; background:#f8d7da; border-color:#f1aeb5; }
    .apm-mat-pr-link  { font-size:11px; color:#0d6efd; text-decoration:none; }
    .apm-mat-pr-link:hover { text-decoration:underline; }

    /* Column header tier pills */
    .apm-mat-pill       { display:inline-block; font-size:9px; font-weight:800;
                          border-radius:4px; padding:1px 6px; margin-right:5px;
                          letter-spacing:.3px; vertical-align:middle; }
    .apm-mat-pill.frd   { background:#198754; color:#fff; }
    .apm-mat-pill.feat  { background:#e5a000; color:#333; }
    .apm-mat-pill.us    { background:#0d3c8f; color:#fff; }
    .apm-mat-pill.lld   { background:#6f42c1; color:#fff; }
    .apm-mat-pill.ui    { background:#0d6efd; color:#fff; }
    .apm-mat-pill.api   { background:#d4670c; color:#fff; }
    .apm-mat-pill.test  { background:#dc3545; color:#fff; }

    /* Status chips */
    .apm-mat-status        { display:inline-block; font-size:11px; font-weight:700;
                             border-radius:10px; padding:4px 10px; white-space:nowrap; }
    .apm-mat-s-done        { background:#d4edda; color:#155724; }
    .apm-mat-s-partial     { background:#fff3cd; color:#664d03; }
    .apm-mat-s-in-progress { background:#cfe2ff; color:#084298; }
    .apm-mat-s-pending     { background:#e2e3e5; color:#383d41; }
  `],
})
export class WorkspaceTraceabilityComponent implements OnInit, OnDestroy {
  readonly CHAIN_STAGES = CHAIN_STAGES;
  readonly REL_CHAIN    = REL_CHAIN;
  readonly FLOW_STEPS   = FLOW_STEPS;

  readonly tabs: { key: TraceTab; label: string; icon: string }[] = [
    { key: "timeline",  label: "Timeline",        icon: "⏱" },
    { key: "artifacts", label: "Artifacts",        icon: "📄" },
    { key: "relations", label: "Relationships",    icon: "🔗" },
    { key: "graph",     label: "Trace Graph",      icon: "🕸" },
    { key: "coverage",  label: "Coverage Matrix",  icon: "☑" },
    { key: "grounding", label: "KB Grounding",     icon: "📚" },
    { key: "matrix",    label: "Trace Matrix",     icon: "📋" },
  ];

  readonly workspaceId  = signal<string>("");
  readonly activeTab    = signal<TraceTab>("timeline");
  readonly loading      = signal(true);
  readonly data         = signal<TraceabilityResponse>({
    workspace_id: "",
    artifacts: [],
    relationships: [],
    kb_grounding: [],
    coverage_matrix: [],
  });

  /** Flat map of stage key → Stage, built from all loaded workspace stage responses.
   *  Powers the Timeline tab so it shows real ✅/⚑/▶/○ status even when
   *  fe_workspace_artifacts is empty (older workspaces). */
  readonly stageMap = signal<Map<string, Stage>>(new Map());

  /** Per-workspace stage list — workspaceId → Stage[].
   *  Powers the Relationships tab workspace-chain visualization. */
  readonly wsStageMap = signal<Map<string, Stage[]>>(new Map());

  /** Real artifacts from fe_workspace_artifacts via the /artifacts API,
   *  keyed by stage_key. Used for download links in the Artifacts tab. */
  readonly artifactsByStage = signal<Map<string, Artifact[]>>(new Map());

  /** Artifacts from THIS workspace only (not other mini workspaces), keyed by stage_key.
   *  Used to resolve per-EPIC stage status without cross-EPIC contamination. */
  readonly epicArtsByStage = signal<Map<string, Artifact[]>>(new Map());

  /** Latest pipeline run per stage key — supplies started_at / finished_at for duration display. */
  readonly stageRunMap = signal<Map<string, Run>>(new Map());

  /** Per-EPIC workspace runs — populated only when viewing the assembler workspace.
   *  Maps mini workspace ID → Run[] so aggregate EPIC rows can show timing. */
  readonly epicWsRunMap = signal<Map<string, Run[]>>(new Map());

  private sub?: Subscription;

  constructor(
    private readonly route: ActivatedRoute,
    private readonly svc: AidlcPlatformMgmtService,
    private readonly store: WorkspaceStore,
  ) {}

  ngOnInit(): void {
    const id = this.route.snapshot.paramMap.get("id") ?? "";
    this.workspaceId.set(id);
    this.loading.set(true);

    const kbAppId   = this.store.selectedProject()?.kb_application_id;
    const globalId  = this.store.globalWorkspace()?.id;
    const archId    = this.store.architectureWorkspace()?.id;

    // All workspace IDs to query for artifact/relationship/KB data.
    // Include raw kb_application_id so older workspaces (stored before the --global
    // suffix convention) are also covered.
    const allIds = [
      id, kbAppId, globalId, archId,
      ...this.store.miniWorkspaces().map((w) => w.id),
    ].filter((v): v is string => !!v);
    const uniqueIds = [...new Set(allIds)];

    // ── Stage status: fetch from stages API for each workspace so the
    //    Timeline tab shows real ✅/⚑/▶ status even when fe_workspace_artifacts
    //    is empty (workspace was created before that table was populated).
    const stageRequests = uniqueIds.map((wid) =>
      this.svc.workspaceStages(wid).pipe(catchError(() => of(null))),
    );

    // Also seed from globalStages already cached in the store (zero extra call).
    const cachedGlobalStages = this.store.globalStages();
    const map = new Map<string, Stage>();
    for (const s of cachedGlobalStages) map.set(s.key, s);
    this.stageMap.set(new Map(map));

    // Fetch artifacts only for the current workspace + global/arch workspaces.
    // Other EPICs' mini workspace IDs are excluded: including them merges stage_key
    // artifacts across all EPICs into artifactsByStage, causing EPIC-1 artifacts to
    // appear in EPIC-2's Artifacts, Relationships, and Trace Graph tabs.
    const otherMiniIds = new Set(
      this.store.miniWorkspaces().map(w => w.id).filter(wid => wid !== id)
    );
    const artFetchIds = uniqueIds.filter(wid => !otherMiniIds.has(wid));
    const artifactRequests = artFetchIds.map((wid) =>
      this.svc.artifacts(wid).pipe(catchError(() => of({ items: [] }))),
    );

    // Fetch pipeline runs only for the current workspace, global, and architecture.
    // Other mini workspaces MUST be excluded — including them causes the "latest start_at wins"
    // merge to pull EPIC-N's run data into EPIC-M's traceability view.
    const runIds = [id, globalId, archId, kbAppId].filter((v): v is string => !!v);
    const uniqueRunIds = [...new Set(runIds)];
    const runsRequests = uniqueRunIds.map((wid) =>
      this.svc.runs(wid).pipe(catchError(() => of({ items: [] }))),
    );

    this.sub = forkJoin([
      this.svc.traceability(id, uniqueIds),
      forkJoin(stageRequests.length ? stageRequests : [of(null)]),
      forkJoin(artifactRequests.length ? artifactRequests : [of({ items: [] })]),
      forkJoin(runsRequests.length ? runsRequests : [of({ items: [] })]),
    ]).subscribe({
      next: ([traceResp, stagesResponses, artifactResponses, runsResponses]) => {
        // Build stage map from all workspace stage responses.
        const merged   = new Map<string, Stage>(map);
        const wsMap    = new Map<string, Stage[]>();
        (stagesResponses as (any | null)[]).forEach((resp, idx) => {
          if (!resp) return;
          const stages: Stage[] = (resp as any)?.stages ?? [];
          merged.set && stages.forEach((s: Stage) => merged.set(s.key, s));
          if (stages.length) wsMap.set(uniqueIds[idx], stages);
        });
        this.stageMap.set(merged);
        this.wsStageMap.set(wsMap);

        // Build artifact-by-stage map from the real /artifacts API responses.
        // epicArtMap tracks ONLY the current workspace's artifacts so per-EPIC stage
        // status resolution is not contaminated by other EPIC mini workspace artifacts.
        const artMap = new Map<string, Artifact[]>();
        const epicArtMap = new Map<string, Artifact[]>();
        (artifactResponses as any[]).forEach((resp, idx) => {
          const isCurrentWs = artFetchIds[idx] === id;
          for (const a of resp?.items ?? []) {
            const key = a.stage_key ?? "";
            if (!artMap.has(key)) artMap.set(key, []);
            artMap.get(key)!.push(a);
            if (isCurrentWs) {
              if (!epicArtMap.has(key)) epicArtMap.set(key, []);
              epicArtMap.get(key)!.push(a);
            }
          }
        });
        this.artifactsByStage.set(artMap);
        this.epicArtsByStage.set(epicArtMap);

        // Build stage-run map: latest run per stage_key (highest started_at wins).
        const runMap = new Map<string, Run>();
        // Build a lookup of run_id → Run for the fallback pass below.
        const runById = new Map<string, Run>();
        for (const resp of (runsResponses as any[])) {
          for (const r of (resp as any)?.items ?? []) {
            const run = r as Run;
            const key = run.stage_key ?? "";
            if (key) {
              const existing = runMap.get(key);
              const rTime = new Date(run.started_at ?? run.queued_at ?? 0).getTime();
              const eTime = existing ? new Date(existing.started_at ?? existing.queued_at ?? 0).getTime() : -1;
              if (rTime >= eTime) runMap.set(key, run);
            }
            if (run.run_id) runById.set(run.run_id, run);
          }
        }
        // Fallback: match by Stage.run_id for stages whose run didn't include stage_key.
        for (const [stageKey, stage] of merged) {
          if (runMap.has(stageKey) || !stage.run_id) continue;
          const matched = runById.get(stage.run_id);
          if (matched) runMap.set(stageKey, matched);
        }

        this.stageRunMap.set(runMap);

        // ── Pipeline-name fallback: for any stage still missing timing data,
        //    query /pipeline-runs?pipeline=<name> WITHOUT workspace_id.
        //    This catches legacy runs stored before workspace_id was persisted.
        //    Pipeline names come from the stageResponses (each has a "pipeline" field).
        const pipelineNames = new Set<string>();
        for (const resp of (stagesResponses as (any | null)[])) {
          const pName = (resp as any)?.pipeline;
          if (pName && typeof pName === "string") pipelineNames.add(pName);
        }
        // Also add from workspace store as an additional source.
        const gwPipeline = this.store.globalWorkspace()?.pipeline;
        const awPipeline = this.store.architectureWorkspace()?.pipeline;
        if (gwPipeline) pipelineNames.add(gwPipeline);
        if (awPipeline) pipelineNames.add(awPipeline);

        const stagesStillMissing = [...merged.keys()].some(k => !runMap.has(k));
        if (stagesStillMissing && pipelineNames.size) {
          const pipelineFetches = [...pipelineNames].map((p) =>
            this.svc.runsByPipeline(p).pipe(catchError(() => of({ items: [] }))),
          );
          forkJoin(pipelineFetches).subscribe((pipelineRunResponses) => {
            const updated = new Map(this.stageRunMap());
            for (const resp of pipelineRunResponses) {
              for (const r of (resp as any)?.items ?? []) {
                const run = r as Run;
                const key = run.stage_key ?? "";
                if (!key || updated.has(key)) continue;
                // Skip runs from other mini workspaces — same pipeline name is shared
                // across all EPICs and without this check EPIC-N runs contaminate EPIC-M.
                if (run.workspace_id && !uniqueRunIds.includes(run.workspace_id)) continue;
                updated.set(key, run);
              }
            }
            this.stageRunMap.set(updated);
          });
        }

        this.data.set(traceResp);
        this.loading.set(false);

        // Assembler workspace: fetch runs for all per-EPIC mini workspaces so
        // aggregate EPIC rows in the Timeline tab can show start/end/duration.
        if (this.store.assemblerWorkspace()?.id === id) {
          const epicWss = this.store.miniWorkspaces().filter(w => w.pipeline !== 'uw-cr-epic-assembler');
          if (epicWss.length) {
            forkJoin(
              epicWss.map(w => this.svc.runs(w.id).pipe(catchError(() => of({ items: [] }))))
            ).subscribe(responses => {
              const wsRunMap = new Map<string, Run[]>();
              (responses as any[]).forEach((resp, idx) => {
                const items: Run[] = (resp as any)?.items ?? [];
                if (items.length) wsRunMap.set(epicWss[idx].id, items);
              });
              this.epicWsRunMap.set(wsRunMap);
            });
          }
        }

        // Per-EPIC runs (feature/user-story/coverage) are stored under the mini
        // workspace_id but ?workspace_id= filter is broken, so svc.runs(id) returns
        // nothing. Use runsByApp + client-side filter to populate stageRunMap for
        // per-EPIC stages so timing shows after completion in the Timeline tab.
        if (kbAppId) {
          this.svc.runsByApp(kbAppId).pipe(catchError(() => of({ items: [] }))).subscribe(r => {
            const perEpicRuns = (r.items ?? []).filter(run => run.workspace_id === id);
            if (!perEpicRuns.length) return;
            const updated = new Map(this.stageRunMap());
            for (const run of perEpicRuns) {
              const key = run.stage_key ?? '';
              if (!key) continue;
              const existing = updated.get(key);
              const rTime = new Date(run.started_at ?? run.queued_at ?? 0).getTime();
              const eTime = existing ? new Date(existing.started_at ?? existing.queued_at ?? 0).getTime() : -1;
              if (rTime >= eTime) updated.set(key, run);
            }
            this.stageRunMap.set(updated);
          });
        }
      },
      error: () => this.loading.set(false),
    });
  }

  ngOnDestroy(): void {
    this.sub?.unsubscribe();
  }

  // ── Computed helpers ──────────────────────────────────────────────────────

  stageLabel(key: string): string {
    return STAGE_LABELS[key] ?? key;
  }

  /** Human-readable workspace tier label for node badges. */
  wsTypeLabel(wsType: string): string {
    switch (wsType) {
      case "global":    return "Global";
      case "arch":      return "Architecture";
      case "mini":      return "Mini/EPIC";
      case "developer": return "Developer";
      case "tester":    return "Tester";
      case "deploy":    return "Deploy";
      default:          return wsType;
    }
  }

  /** Convert a display workspace label (e.g. "Architecture") to the CSS wsType slug
   *  used for colour-coded pills in the Relationships hop header. */
  wsTypeForStage(wsLabel: string): string {
    const l = wsLabel.toLowerCase();
    if (l.includes("architecture")) return "arch";
    if (l.includes("mini") || l.includes("epic")) return "mini";
    if (l.includes("developer")) return "developer";
    if (l.includes("tester")) return "tester";
    if (l.includes("deploy")) return "deploy";
    return "global";
  }

  /** Lookup artifacts for a lineage node by trying each stageKey in order,
   *  then falling back to prefix-matching against the artifact stage key map. */
  lineageArtifacts(stageKeys: string[]): Artifact[] {
    const artMap = this.artifactsByStage();
    for (const key of stageKeys) {
      const arts = artMap.get(key);
      if (arts?.length) return arts;
    }
    // Fuzzy: try prefix matching (handles minor stage key drift between pipeline versions).
    for (const [mapKey, arts] of artMap) {
      if (!arts?.length) continue;
      if (stageKeys.some(k => mapKey.startsWith(k) || k.startsWith(mapKey))) return arts;
    }
    return [];
  }

  statusClass(status: string | undefined | null): string {
    if (!status) return "idle";
    if (["completed", "approved", "done"].includes(status))              return "good";
    if (["failed", "error", "rejected"].includes(status))                return "bad";
    if (["waiting_for_approval", "pending_review"].includes(status))     return "warn";
    if (["running", "in_progress"].includes(status))                     return "warn";
    return "idle";
  }

  /** Human-readable label for each pipeline stage state. */
  statusLabel(status: string | undefined | null): string {
    switch (status) {
      case "completed":            return "✔ Completed";
      case "approved":             return "✔ Approved";
      case "waiting_for_approval": return "⚑ Awaiting Approval";
      case "running":              return "▶ Running";
      case "in_progress":          return "▶ In Progress";
      case "failed":               return "✕ Failed";
      case "rejected":             return "✕ Rejected";
      case "ready":                return "▶ Ready";
      case "in_progress":          return "▶ In Progress";
      case "idle":                 return "○ Idle";
      default:                     return "○ Pending";
    }
  }

  /** Unique KB card IDs, sorted, from kb_grounding (covers all stages). */
  uniqueKbCards(): string[] {
    const ids = new Set(this.data().kb_grounding.map(g => g.to_kb_card_id));
    return Array.from(ids).sort();
  }

  /** Timeline entries — one per chain stage.
   *
   *  Status comes from the stages API (stageMap) so real ✅/⚑/▶/○ states show
   *  immediately regardless of whether fe_workspace_artifacts has rows.
   *  `stage` (full Stage object) and `artifact` metadata are merged in when available.
   *  `isAggregate` is true for EPIC summary rows in the assembler workspace view.
   */
  timelineStages(): { key: string; stageName: string; status: string; isAggregate?: boolean; wsId?: string; stage?: Stage; artifact?: TraceabilityArtifact }[] {
    const arts    = this.data().artifacts;
    const map     = this.stageMap();
    const runMap  = this.stageRunMap();
    const id      = this.workspaceId();

    // ── EPIC Assembler workspace path ────────────────────────────────────────
    // Timeline shows: Global stages → Architecture stages →
    //   one aggregate row per EPIC (status only, no stage breakdown) →
    //   Assembler pipeline stages (epic-assemble, ui-smoke, ui-e2e, pr-merge, deployment).
    if (this.store.assemblerWorkspace()?.id === id) {
      const result: { key: string; stageName: string; status: string; isAggregate?: boolean; wsId?: string; stage?: Stage; artifact?: TraceabilityArtifact }[] = [];

      // Global chain stages: PRD, FRD, EPIC Set
      for (const key of ['prd', 'frd', 'epic-set', 'epic'] as const) {
        const stage = map.get(key);
        if (!stage && !runMap.has(key)) continue;
        const artifact = arts.find(a => a.kind === key);
        const raw = stage?.state ?? (runMap.has(key) ? 'completed' : 'pending');
        result.push({ key, stageName: stage?.name ?? this.stageLabel(key), status: raw, stage, artifact });
      }

      // Architecture chain stages: ADR, NFR, SDD, SRD
      for (const key of ['adr', 'nfr', 'sdd', 'srd'] as const) {
        const stage = map.get(key);
        if (!stage && !runMap.has(key)) continue;
        const artifact = arts.find(a => a.kind === key);
        const raw = stage?.state ?? (runMap.has(key) ? 'completed' : 'pending');
        result.push({ key, stageName: stage?.name ?? this.stageLabel(key), status: raw, stage, artifact });
      }

      // One aggregate row per EPIC — derive status from loaded stage states in wsStageMap
      const perEpicWs = this.store.miniWorkspaces().filter(w => w.pipeline !== 'uw-cr-epic-assembler');
      const wsMap = this.wsStageMap();
      for (const ws of perEpicWs) {
        const wsStages = wsMap.get(ws.id) ?? [];
        let derivedStatus: string;
        if (wsStages.length > 0) {
          const done    = wsStages.filter(s => ['completed','approved'].includes(s.state ?? '')).length;
          const running = wsStages.filter(s => ['running','in_progress','queued'].includes(s.state ?? '')).length;
          const failed  = wsStages.filter(s => s.state === 'failed').length;
          derivedStatus = failed > 0          ? 'failed'
            : done === wsStages.length        ? 'completed'
            : running > 0                     ? 'running'
            : done > 0                        ? 'in_progress'
            : (ws.state ?? 'idle');
        } else {
          derivedStatus = ws.state ?? 'idle';
        }
        result.push({
          key:         `epic-agg-${ws.epic_id ?? ws.id}`,
          stageName:   `${ws.epic_id ?? 'EPIC'}${ws.epic_title ? ' — ' + ws.epic_title : ''}`,
          status:      derivedStatus,
          isAggregate: true,
          wsId:        ws.id,
        });
      }

      // Assembler pipeline's own stages (epic-assemble → db-integration → ui-smoke → ui-e2e → pr-merge → deployment)
      // Inject db-integration when backend hasn't added it yet (pre-existing workspace).
      const ownStages = this.wsStageMap().get(id) ?? [];
      const hasDb = ownStages.some(s => s.key === 'db-integration');
      const finalOwnStages: Stage[] = hasDb ? ownStages : (() => {
        const dbStage: Stage = { key: 'db-integration', name: 'DB Integration', seq: 2, state: 'idle', agent_group: 'Developer Agent' };
        const idx = ownStages.findIndex(s => s.key === 'epic-assemble');
        const r = [...ownStages];
        r.splice(idx >= 0 ? idx + 1 : r.length, 0, dbStage);
        return r;
      })();
      for (const stage of finalOwnStages) {
        const artifact = arts.find(a => a.kind === stage.key);
        result.push({ key: stage.key, stageName: stage.name, status: stage.state ?? 'pending', stage, artifact });
      }
      return result;
    }
    // ────────────────────────────────────────────────────────────────────────

    if (map.size === 0 && runMap.size === 0) return [];

    const result: { key: string; stageName: string; status: string; stage?: Stage; artifact?: TraceabilityArtifact }[] = [];
    const seen = new Set<string>();

    // Per-EPIC global stages: the stages API returns 'not_ready' for these even when
    // artifacts are APPROVED (mini workspace IDs excluded from readable_workspace_ids).
    // Override using artifact status fetched directly from the mini workspace.
    const PER_EPIC = new Set(['feature', 'user-story', 'coverage']);
    const resolveStatus = (key: string, raw: string): string => {
      if (!PER_EPIC.has(key)) return raw;
      // Use epicArtsByStage (current workspace only) — artifactsByStage aggregates
      // all mini workspaces and would incorrectly reflect other EPICs' artifact state.
      const stageArts = this.epicArtsByStage().get(key) ?? [];
      if (stageArts.length > 0 && stageArts.every(a => (a.status ?? '').toUpperCase() === 'APPROVED')) return 'completed';
      if (stageArts.some(a => (a.status ?? '').toUpperCase() === 'DRAFT')) return 'waiting_for_approval';
      return raw;
    };

    // Stages moved to the EPIC Assembler workspace — excluded from per-EPIC timelines.
    const ASSEMBLER_ONLY_STAGES = new Set(['db-integration']);

    // Stages that exist in the stage map — chain order first, then extras.
    for (const key of CHAIN_STAGES) {
      // db-integration is now part of the EPIC Assembler pipeline, not per-EPIC workspaces.
      if (ASSEMBLER_ONLY_STAGES.has(key)) continue;
      const stage = map.get(key);
      // Only include stages that are present in the workspace OR have run data.
      if (!stage && !runMap.has(key)) continue;
      const artifact = arts.find(a => a.kind === key);
      const raw = stage?.state ?? (runMap.has(key) ? "completed" : "pending");
      result.push({
        key,
        stageName: stage?.name ?? this.stageLabel(key),
        status:    resolveStatus(key, raw),
        stage,
        artifact,
      });
      seen.add(key);
    }
    // Extra stages not in CHAIN_STAGES (pipeline may have additional stages).
    for (const [key, stage] of map) {
      if (seen.has(key)) continue;
      const artifact = arts.find(a => a.kind === key);
      result.push({ key, stageName: stage.name, status: resolveStatus(key, stage.state ?? "pending"), stage, artifact });
    }
    // Stages with runs but not in stage map (can happen for cross-workspace runs).
    for (const [key] of runMap) {
      if (seen.has(key) || map.has(key)) continue;
      const artifact = arts.find(a => a.kind === key);
      result.push({ key, stageName: this.stageLabel(key), status: "completed", artifact });
    }
    return result;
  }

  /** Returns the Run object for a stage key — used in the Timeline to show start/end timestamps. */
  stageRun(stageKey: string): Run | null {
    return this.stageRunMap().get(stageKey) ?? null;
  }

  /** ISO string of the earliest pipeline start across all completed runs. */
  pipelineStartTime(): string | null {
    let earliest: string | null = null;
    let earliestMs = Infinity;
    for (const r of this.stageRunMap().values()) {
      const t = r.started_at ?? r.queued_at;
      if (!t) continue;
      const ms = new Date(t).getTime();
      if (ms < earliestMs) { earliestMs = ms; earliest = t; }
    }
    return earliest;
  }

  /** ISO string of the latest finished_at across all completed runs. */
  pipelineEndTime(): string | null {
    let latest: string | null = null;
    let latestMs = -Infinity;
    for (const r of this.stageRunMap().values()) {
      if (!r.finished_at) continue;
      const ms = new Date(r.finished_at).getTime();
      if (ms > latestMs) { latestMs = ms; latest = r.finished_at; }
    }
    return latest;
  }

  /** The latest (highest version) artifact for a stage key from the /artifacts API,
   *  used in the Timeline to show git commit, PR URL, created_by, approval_feedback. */
  stageLatestArtifact(stageKey: string): Artifact | undefined {
    const list = this.artifactsByStage().get(stageKey) ?? [];
    if (!list.length) return undefined;
    return list.reduce((best, a) =>
      (Number(a.version ?? 0) >= Number(best.version ?? 0) ? a : best), list[0]);
  }

  // ── Execution time helpers ────────────────────────────────────────────────

  /** Duration string for a single stage (e.g. "4m 32s", "1h 12m"). Returns null if no run data. */
  stageDuration(stageKey: string): string | null {
    const run = this.stageRunMap().get(stageKey);
    if (!run) return null;
    const start  = run.started_at ?? run.queued_at;
    const finish = run.finished_at;
    if (!start || !finish) return null;
    const ms = new Date(finish).getTime() - new Date(start).getTime();
    if (ms <= 0) return null;
    return this.formatMs(ms);
  }

  /** Earliest started_at across all runs for an aggregate EPIC workspace. */
  epicWsFirstStarted(wsId: string | undefined): string | null {
    if (!wsId) return null;
    const runs = this.epicWsRunMap().get(wsId) ?? [];
    const times = runs.map(r => r.started_at ?? r.queued_at).filter(Boolean) as string[];
    return times.length ? times.sort()[0] : null;
  }

  /** Latest finished_at across all runs for an aggregate EPIC workspace. */
  epicWsLastFinished(wsId: string | undefined): string | null {
    if (!wsId) return null;
    const runs = this.epicWsRunMap().get(wsId) ?? [];
    const times = runs.map(r => r.finished_at).filter(Boolean) as string[];
    return times.length ? [...times].sort().at(-1) ?? null : null;
  }

  /** Total execution time for an aggregate EPIC workspace — sum of individual stage
   *  run durations, identical to what that EPIC's own traceability view shows as
   *  "Total Execution Time". Excludes idle time between stages. */
  epicWsDuration(wsId: string | undefined): string | null {
    if (!wsId) return null;
    const runs = this.epicWsRunMap().get(wsId) ?? [];
    let totalMs = 0;
    for (const run of runs) {
      const start  = run.started_at ?? run.queued_at;
      const finish = run.finished_at;
      if (!start || !finish) continue;
      const ms = new Date(finish).getTime() - new Date(start).getTime();
      if (ms > 0) totalMs += ms;
    }
    return totalMs > 0 ? this.formatMs(totalMs) : null;
  }

  /** Total execution time = SUM of every stage's own duration.
   *  For the EPIC Assembler workspace this also includes all per-EPIC workspace
   *  stage run durations so the banner reflects the full pipeline compute time. */
  totalExecutionTime(): string | null {
    let totalMs = 0;
    for (const r of this.stageRunMap().values()) {
      const start  = r.started_at ?? r.queued_at;
      const finish = r.finished_at;
      if (!start || !finish) continue;
      const ms = new Date(finish).getTime() - new Date(start).getTime();
      if (ms > 0) totalMs += ms;
    }
    // When viewing the assembler workspace, add per-EPIC workspace run durations.
    for (const runs of this.epicWsRunMap().values()) {
      for (const r of runs) {
        const start  = r.started_at ?? r.queued_at;
        const finish = r.finished_at;
        if (!start || !finish) continue;
        const ms = new Date(finish).getTime() - new Date(start).getTime();
        if (ms > 0) totalMs += ms;
      }
    }
    return totalMs > 0 ? this.formatMs(totalMs) : null;
  }

  /** Count of completed stages (have both started_at and finished_at). */
  completedStageCount(): number {
    return [...this.stageRunMap().values()].filter(r => r.started_at && r.finished_at).length;
  }

  private formatMs(ms: number): string {
    const totalSec = Math.floor(ms / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  // ── Relationships tab helpers ─────────────────────────────────────────────

  /** All artifacts for a stage key (from the /artifacts API). Used in the Relationships tab. */
  relArtifactsForStage(stageKey: string): Artifact[] {
    return this.artifactsByStage().get(stageKey) ?? [];
  }

  /** Derives workspace relationship groups from stage responses, ordered by pipeline tier.
   *
   *  Mapping (matches user-defined hierarchy):
   *    Global     → prd, frd, epic-set, feature, user-story
   *    Architecture → adr, nfr, sdd, srd (any workspace containing these keys)
   *    Epic mini  → workspace IDs matching --epic-N
   *    Developer  → workspace IDs containing "developer"
   *    Tester     → workspace IDs containing "tester"
   */
  derivedRelGroups(): { workspaceId: string; tierLabel: string; tier: string; stages: Stage[] }[] {
    const wsMap     = this.wsStageMap();
    const globalId  = this.store.globalWorkspace()?.id ?? "";
    const archId    = this.store.architectureWorkspace()?.id ?? "";

    const TIER_ORDER: Record<string, number> = {
      global: 0, architecture: 1, mini: 2, developer: 3, tester: 4, other: 5,
    };

    const groups: { workspaceId: string; tierLabel: string; tier: string; stages: Stage[] }[] = [];

    for (const [wsId, stages] of wsMap) {
      if (!stages.length) continue;
      let tier     = "other";
      let tierLabel = wsId;

      if (wsId === globalId || /--global$/.test(wsId) || (!wsId.includes("--") && wsId.endsWith("-v1"))) {
        tier      = "global";
        tierLabel = "Global Workspace";
      } else if (wsId === archId || /--architecture/.test(wsId)) {
        tier      = "architecture";
        tierLabel = "Architecture Workspace";
      } else if (/--developer/i.test(wsId)) {
        tier      = "developer";
        tierLabel = "Developer Workspace";
      } else if (/--tester/i.test(wsId)) {
        tier      = "tester";
        tierLabel = "Tester Workspace";
      } else if (/--epic-\d/.test(wsId)) {
        const epicNum = wsId.match(/epic-(\d+)/)?.[1] ?? "";
        tier      = "mini";
        tierLabel = `EPIC ${epicNum} Workspace`;
      }

      groups.push({ workspaceId: wsId, tierLabel, tier, stages });
    }

    return groups.sort((a, b) => {
      const oa = TIER_ORDER[a.tier] ?? 99;
      const ob = TIER_ORDER[b.tier] ?? 99;
      return oa !== ob ? oa - ob : a.workspaceId.localeCompare(b.workspaceId);
    });
  }

  /** Artifacts grouped by their kind (stage key), in chain order. */
  artifactGroups(): { key: string; items: TraceabilityArtifact[] }[] {
    const arts = this.data().artifacts;
    const grouped = new Map<string, TraceabilityArtifact[]>();
    for (const a of arts) {
      if (!grouped.has(a.kind)) grouped.set(a.kind, []);
      grouped.get(a.kind)!.push(a);
    }
    // Return in chain order first, then any extras not in the chain list
    const chainSet = new Set<string>(CHAIN_STAGES);
    const result: { key: string; items: TraceabilityArtifact[] }[] = [];
    for (const key of CHAIN_STAGES) {
      if (grouped.has(key)) result.push({ key, items: grouped.get(key)! });
    }
    for (const [key, items] of grouped) {
      if (!chainSet.has(key)) result.push({ key, items });
    }
    return result;
  }

  /** Filter artifacts by kind — used when fe_workspace_artifacts has rows. */
  artifactsByKind(kind: string): TraceabilityArtifact[] {
    return this.data().artifacts.filter(a => a.kind === kind);
  }

  /** All stages from the map as an ordered array, for the Artifacts tab. */
  stageEntries(): Stage[] {
    const map = this.stageMap();
    const chainSet = new Set<string>(CHAIN_STAGES);
    const result: Stage[] = [];
    const seen  = new Set<string>();
    for (const key of CHAIN_STAGES) {
      const s = map.get(key);
      if (s) { result.push(s); seen.add(key); }
    }
    for (const [key, s] of map) {
      if (!seen.has(key) && !chainSet.has(key)) result.push(s);
    }
    return result;
  }

  /** Stage group buckets (kept for backward compat — Trace Graph tab now uses lineage tree). */
  private readonly GRAPH_GROUPS = {
    requirements: ["prd", "frd", "epic-set"],
    architecture: ["adr", "nfr", "sdd", "srd"],
    stories:      ["feature", "user-story"],
    code:         ["lld-and-tdds", "ui-code-generation", "api-code-generation", "coverage-validation", "ui-smoke-test", "api-testing", "ui-e2e-testing"],
  } as const;

  /** Download URL for an artifact — proxied via the backend (handles S3 presigned URL). */
  downloadUrl(artifactId: string): string {
    return this.svc.downloadArtifactUrl(artifactId);
  }

  /** Export-as-DOCX URL for an artifact. */
  exportDocxUrl(artifactId: string): string {
    return this.svc.exportDocxUrl(artifactId);
  }

  // ── Matrix tab ────────────────────────────────────────────────────────────

  /** Human-readable status label displayed in the matrix Status column. */
  matStatusLabel(status: MatrixRow["status"]): string {
    switch (status) {
      case "done":        return "✅ DONE";
      case "partial":     return "⚠ PARTIAL";
      case "in-progress": return "⏳ IN PROGRESS";
      default:            return "○ PENDING";
    }
  }

  /** Build a flat lookup of artifact.id → Artifact from all stage artifact responses. */
  private artDetailMap(): Map<string, Artifact> {
    const m = new Map<string, Artifact>();
    for (const arts of this.artifactsByStage().values()) {
      for (const a of arts) m.set(a.id, a);
    }
    return m;
  }

  /** Find TraceabilityArtifacts connected to `pivotId` whose kind matches `targetKind`.
   *  Searches both relationship directions to tolerate backend storage variations. */
  private connectedByKind(
    artById: Map<string, TraceabilityArtifact>,
    rels: TraceabilityRelationship[],
    pivotId: string,
    targetKind: string,
  ): TraceabilityArtifact[] {
    const out: TraceabilityArtifact[] = [];
    for (const r of rels) {
      if (r.source_artifact_id === pivotId) {
        const a = artById.get(r.target_artifact_id);
        if (a?.kind === targetKind) out.push(a);
      } else if (r.target_artifact_id === pivotId) {
        const a = artById.get(r.source_artifact_id);
        if (a?.kind === targetKind) out.push(a);
      }
    }
    return out;
  }

  /** Derive a matrix row status from which output stages are populated. */
  private matStatus(
    row: Pick<MatrixRow, "uiCodes" | "apiCodes" | "lld" | "tests">,
  ): MatrixRow["status"] {
    if ((row.uiCodes.length || row.apiCodes.length) && row.tests.length) return "done";
    if (row.uiCodes.length || row.apiCodes.length) return "partial";
    if (row.lld) return "in-progress";
    return "pending";
  }

  /** Build one matrix row per user-story, using the relationship graph when available
   *  and falling back to a stage-level grouping when relationship data is absent. */
  matrixRows(): MatrixRow[] {
    const { artifacts, relationships } = this.data();
    const detailMap = this.artDetailMap();

    // ── Relationship-graph path (preferred when relationship data exists) ──
    const artById = new Map<string, TraceabilityArtifact>();
    for (const a of artifacts) artById.set(a.artifact_id, a);

    const rel = (id: string, kind: string) =>
      this.connectedByKind(artById, relationships, id, kind);

    const toArt  = (a: TraceabilityArtifact | undefined): Artifact | null =>
      a ? (detailMap.get(a.artifact_id) ?? null) : null;
    const toArts = (arr: TraceabilityArtifact[]): Artifact[] =>
      arr.map(a => detailMap.get(a.artifact_id)).filter((a): a is Artifact => !!a);

    const userStories = artifacts.filter(a => a.kind === "user-story");

    if (userStories.length && relationships.length) {
      return userStories.map(us => {
        const features = rel(us.artifact_id, "feature");
        const llds     = rel(us.artifact_id, "lld-and-tdds");
        const frdList  = features.flatMap(f => rel(f.artifact_id, "frd"));
        const lldId    = llds[0]?.artifact_id;
        const uiList   = lldId ? rel(lldId, "ui-code-generation")  : [];
        const apiList  = lldId ? rel(lldId, "api-code-generation") : [];
        const testList = [
          ...uiList.flatMap(c => rel(c.artifact_id, "ui-smoke-test")),
          ...uiList.flatMap(c => rel(c.artifact_id, "ui-e2e-testing")),
          ...apiList.flatMap(c => rel(c.artifact_id, "api-testing")),
        ];
        const row: MatrixRow = {
          userStory: toArt(us),
          feature:   toArt(features[0]),
          frd:       toArt(frdList[0]),
          lld:       toArt(llds[0]),
          uiCodes:   toArts(uiList),
          apiCodes:  toArts(apiList),
          tests:     toArts(testList),
          status:    "pending",
        };
        row.status = this.matStatus(row);
        return row;
      });
    }

    // ── Stage-map fallback (relationship data not yet populated) ──────────
    return this.matrixRowsFromStages(this.artifactsByStage());
  }

  /** Fallback: one row per user-story (or feature) with shared stage artifacts
   *  broadcast to every row when no relationship graph is available. */
  private matrixRowsFromStages(artMap: Map<string, Artifact[]>): MatrixRow[] {
    const get    = (k: string) => artMap.get(k) ?? [];
    const usArts  = get("user-story");
    const pivot   = usArts.length ? usArts : get("feature");
    if (!pivot.length) return [];

    const isUS    = usArts.length > 0;
    const tests   = [...get("ui-smoke-test"), ...get("api-testing"), ...get("ui-e2e-testing")];
    const uiCodes  = get("ui-code-generation");
    const apiCodes = get("api-code-generation");

    return pivot.map(p => {
      const row: MatrixRow = {
        userStory: isUS ? p : null,
        feature:   isUS ? (get("feature")[0] ?? null) : p,
        frd:       get("frd")[0]          ?? null,
        lld:       get("lld-and-tdds")[0] ?? null,
        uiCodes,
        apiCodes,
        tests,
        status:    "pending",
      };
      row.status = this.matStatus(row);
      return row;
    });
  }

  stagesByGroup(group: "requirements" | "architecture" | "stories" | "code"): Stage[] {
    const keys = this.GRAPH_GROUPS[group];
    const map  = this.stageMap();
    return keys.map(k => map.get(k)).filter((s): s is Stage => !!s);
  }

  /** True if there is a GROUNDS link from (card, stage). */
  hasCoverage(cardId: string, stage: string): boolean {
    return this.data().coverage_matrix.some(
      c => c.to_kb_card_id === cardId && c.stage === stage,
    );
  }

  /** Coverage % for one KB card: stages covered ÷ total stages. */
  coveragePct(cardId: string): number {
    const covered = CHAIN_STAGES.filter(s => this.hasCoverage(cardId, s)).length;
    return Math.round((covered / CHAIN_STAGES.length) * 100);
  }

  /** Grounding % — normalised against the max count across all cards. */
  groundingPct(g: TraceabilityKbGrounding): number {
    const maxCount = Math.max(...this.data().kb_grounding.map(x => x.coverage_count), 1);
    return Math.round((g.coverage_count / maxCount) * 100);
  }
}
