import { Component, OnInit, computed, signal } from "@angular/core";
import { catchError } from "rxjs/operators";
import { of } from "rxjs";

import { Artifact, AidlcPlatformMgmtService, Stage, Workspace } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

const PER_EPIC_KEYS = new Set(["feature", "user-story", "coverage"]);

const STAGE_ABBR: Record<string, string> = {
  "prd": "PRD", "frd": "FRD", "epic-set": "EPIC",
  "kb": "KB",
  "feature": "Feat", "user-story": "US", "coverage": "Cov",
  "adr": "ADR", "nfr": "NFR", "sdd": "SDD", "srd": "SRD",
  "lld": "LLD", "ui-code": "UI", "api-code": "API",
  "db-integration": "DB", "security-opa": "Sec",
  "api-test": "Test", "ui-smoke": "Smoke", "ui-e2e": "E2E",
  // EPIC Assembler pipeline stages
  "epic-assemble": "Assemble", "pr-merge": "PR", "deployment": "Deploy",
};

const STATE_LABEL: Record<string, string> = {
  "completed": "Completed", "approved": "Approved",
  "running": "Running", "queued": "Queued",
  "waiting_for_approval": "Awaiting Approval",
  "failed": "Failed", "idle": "Idle",
};

/** Pipeline Summary — three-tier status view: Global → Architecture → per-EPIC mini workspaces. */
@Component({
  selector: "app-pipeline-summary",
  template: `
    <div class="apm-crumb">{{ store.selectedProject()?.name }} &rsaquo; Pipeline Summary</div>

    <div class="apm-hdr">
      <div class="apm-hdr-icon">⚙</div>
      <div>
        <h2>Pipeline Summary</h2>
        <p class="sub">Live status for all workspace stages — Global, Architecture, and per-EPIC delivery.</p>
      </div>
    </div>

    <div class="ps-empty" *ngIf="!store.selectedProject()">
      Select a project from the Workspace dashboard to view its pipeline summary.
    </div>

    <!-- ── Global Workspace ── -->
    <div class="ps-section" *ngIf="globalNonEpicStages().length">
      <div class="ps-section-hdr">
        <span class="ps-tier-badge tier-global">global</span>
        <span class="ps-section-title">Global Workspace</span>
        <span class="ps-section-id">{{ store.globalWorkspace()?.id }}</span>
      </div>
      <div class="ps-stepper">
        <ng-container *ngFor="let s of globalNonEpicStages(); let i = index">
          <span *ngIf="i > 0" class="ps-conn" [class.ps-conn-done]="globalNonEpicStages()[i-1]?.state === 'completed' || globalNonEpicStages()[i-1]?.state === 'approved'"></span>
          <div class="ps-step" [title]="s.name + ' · ' + labelFor(s.state)">
            <div class="ps-dot" [ngClass]="dotClass(s.state)"></div>
            <div class="ps-lbl">{{ abbr(s) }}</div>
          </div>
        </ng-container>
      </div>
    </div>

    <!-- ── Architecture Workspace ── -->
    <div class="ps-section" *ngIf="archStages().length">
      <div class="ps-section-hdr">
        <span class="ps-tier-badge tier-arch">arch</span>
        <span class="ps-section-title">Architecture Workspace</span>
        <span class="ps-section-id">{{ store.architectureWorkspace()?.id }}</span>
      </div>
      <div class="ps-stepper">
        <ng-container *ngFor="let s of archStages(); let i = index">
          <span *ngIf="i > 0" class="ps-conn" [class.ps-conn-done]="archStages()[i-1]?.state === 'completed' || archStages()[i-1]?.state === 'approved'"></span>
          <div class="ps-step" [title]="s.name + ' · ' + labelFor(s.state)">
            <div class="ps-dot" [ngClass]="dotClass(s.state)"></div>
            <div class="ps-lbl">{{ abbr(s) }}</div>
          </div>
        </ng-container>
      </div>
    </div>

    <!-- ── Per-EPIC Delivery Workspaces ── -->
    <div class="ps-epics-hdr" *ngIf="perEpicWorkspaces().length">
      <strong>Per-EPIC Delivery Workspaces</strong>
      <span class="sub">&nbsp;· Feat &rarr; US &rarr; Cov &rarr; LLD &rarr; UI &rarr; API &rarr; Sec &rarr; Test</span>
    </div>

    <div class="ps-section ps-section--epic" *ngFor="let ws of perEpicWorkspaces()">
      <div class="ps-section-hdr">
        <span class="ps-tier-badge tier-epic">{{ ws.epic_id }}</span>
        <span class="ps-section-title">{{ ws.epic_title || ws.epic_id }}</span>
        <span class="ps-section-id">{{ ws.id }}</span>
        <span class="ps-state-chip" [ngClass]="chipTone(ws.state || 'idle')">{{ labelFor(ws.state || 'idle') }}</span>
      </div>
      <div class="ps-stepper">
        <ng-container *ngIf="stagesFor(ws) as stages">
          <ng-container *ngFor="let s of stages; let i = index">
            <span *ngIf="i > 0" class="ps-conn" [class.ps-conn-done]="stages[i-1]?.state === 'completed' || stages[i-1]?.state === 'approved'"></span>
            <div class="ps-step" [title]="s.name + ' · ' + labelFor(s.state)">
              <div class="ps-dot" [ngClass]="dotClass(s.state)"></div>
              <div class="ps-lbl">{{ abbr(s) }}</div>
            </div>
          </ng-container>
          <div class="ps-loading" *ngIf="!stages.length">
            <span class="sub">Loading…</span>
          </div>
        </ng-container>
      </div>
    </div>

    <!-- ── EPIC Assembler Workspace ── -->
    <div class="ps-epics-hdr" *ngIf="store.selectedProject()">
      <strong>EPIC Assembler Workspace</strong>
      <span class="sub">&nbsp;· Assemble &rarr; DB &rarr; Smoke &rarr; E2E &rarr; PR &rarr; Deploy</span>
    </div>

    <ng-container *ngIf="store.assemblerWorkspace() as asm; else assemblerPending">
      <div class="ps-section ps-section--assembler">
        <div class="ps-section-hdr">
          <span class="ps-tier-badge tier-epic">Assembler</span>
          <span class="ps-section-title">EPIC Assembler Workspace</span>
          <span class="ps-section-id">{{ asm.id }}</span>
          <span class="ps-state-chip" [ngClass]="chipTone(asm.state || 'idle')">{{ labelFor(asm.state || 'idle') }}</span>
        </div>
        <div class="ps-stepper">
          <ng-container *ngFor="let s of assemblerStagesWithDb(); let i = index">
            <span *ngIf="i > 0" class="ps-conn" [class.ps-conn-done]="assemblerStagesWithDb()[i-1]?.state === 'completed' || assemblerStagesWithDb()[i-1]?.state === 'approved'"></span>
            <div class="ps-step" [title]="s.name + ' · ' + labelFor(s.state)">
              <div class="ps-dot" [ngClass]="dotClass(s.state)"></div>
              <div class="ps-lbl">{{ abbr(s) }}</div>
            </div>
          </ng-container>
          <div class="ps-loading" *ngIf="!assemblerStagesWithDb().length">
            <span class="sub">Loading…</span>
          </div>
        </div>
      </div>
    </ng-container>

    <ng-template #assemblerPending>
      <div class="ps-section ps-section--assembler ps-section--pending" *ngIf="store.selectedProject()">
        <div class="ps-section-hdr">
          <span class="ps-tier-badge tier-epic">Assembler</span>
          <span class="ps-section-title">EPIC Assembler Workspace</span>
          <span class="ps-state-chip ps-chip-idle">Pending</span>
        </div>
        <div class="ps-stepper">
          <ng-container *ngFor="let key of ['epic-assemble','db-wiring','ui-smoke','ui-e2e','pr-merge','deployment']; let i = index">
            <span *ngIf="i > 0" class="ps-conn"></span>
            <div class="ps-step" [title]="key">
              <div class="ps-dot dot-grey"></div>
              <div class="ps-lbl">{{ abbrKey(key) }}</div>
            </div>
          </ng-container>
        </div>
      </div>
    </ng-template>

    <!-- ── Legend ── -->
    <div class="ps-legend" *ngIf="store.selectedProject()">
      <span class="ps-legend-item"><span class="ps-dot dot-green"></span> Completed</span>
      <span class="ps-legend-item"><span class="ps-dot dot-blue"></span> Running / Queued</span>
      <span class="ps-legend-item"><span class="ps-dot dot-orange"></span> Awaiting Approval</span>
      <span class="ps-legend-item"><span class="ps-dot dot-red"></span> Failed</span>
      <span class="ps-legend-item"><span class="ps-dot dot-grey"></span> Idle</span>
    </div>
  `,
})
export class PipelineSummaryComponent implements OnInit {
  readonly archStages = signal<Stage[]>([]);
  readonly assemblerStages = signal<Stage[]>([]);
  readonly miniStageMap = signal<Record<string, Stage[]>>({});
  /** Per-mini-workspace artifacts keyed by stage_key — used to infer per-EPIC stage completion. */
  readonly miniArtifactMap = signal<Record<string, Record<string, Artifact[]>>>({});

  readonly globalNonEpicStages = computed(() =>
    this.store.globalStages().filter(s => !PER_EPIC_KEYS.has(s.key))
  );
  private readonly perEpicGlobalStages = computed(() =>
    this.store.globalStages().filter(s => PER_EPIC_KEYS.has(s.key))
  );
  readonly perEpicWorkspaces = computed(() =>
    this.store.miniWorkspaces().filter(w => w.pipeline !== 'uw-cr-epic-assembler')
  );
  /** Assembler stages with db-integration injected after epic-assemble when the
   *  backend hasn't added it yet (pre-existing workspace created before YAML update). */
  readonly assemblerStagesWithDb = computed(() => {
    const stages = this.assemblerStages();
    if (!stages.length || stages.some(s => s.key === 'db-wiring')) return stages;
    const dbStage: Stage = { key: 'db-wiring', name: 'DB Wiring', seq: 2, state: 'idle', agent_group: 'Developer Agent' };
    const idx = stages.findIndex(s => s.key === 'epic-assemble');
    const result = [...stages];
    result.splice(idx >= 0 ? idx + 1 : result.length, 0, dbStage);
    return result;
  });

  constructor(
    readonly store: WorkspaceStore,
    private readonly svc: AidlcPlatformMgmtService,
  ) {}

  ngOnInit(): void {
    const arch = this.store.architectureWorkspace();
    if (arch) {
      this.svc.workspaceStages(arch.id).subscribe(r => {
        if (r?.stages) this.archStages.set(r.stages);
      });
    }
    const asm = this.store.assemblerWorkspace();
    if (asm) {
      this.svc.workspaceStages(asm.id).subscribe(r => {
        if (r?.stages) this.assemblerStages.set(r.stages);
      });
    }
    for (const mini of this.perEpicWorkspaces()) {
      this.svc.workspaceStages(mini.id).subscribe(r => {
        if (r?.stages) {
          this.miniStageMap.update(m => ({ ...m, [mini.id]: r.stages }));
        }
      });
      // Fetch artifacts to detect per-EPIC completion — global stages API never
      // propagates mini workspace artifact status into feature/user-story/coverage state.
      this.svc.artifacts(mini.id).pipe(catchError(() => of({ items: [] }))).subscribe(r => {
        const byKey: Record<string, Artifact[]> = {};
        for (const a of (r?.items ?? [])) {
          const k = a.stage_key ?? '';
          if (!byKey[k]) byKey[k] = [];
          byKey[k].push(a);
        }
        this.miniArtifactMap.update(m => ({ ...m, [mini.id]: byKey }));
      });
    }
  }

  /** Full stage list for a mini workspace: per-EPIC global stages prepended.
   *  State for feature/user-story/coverage is derived from artifacts because the global
   *  stages API cannot see mini workspace artifacts (mini IDs excluded from readable_workspace_ids).
   *  db-integration is excluded here — it now runs in the EPIC Assembler workspace. */
  stagesFor(ws: Workspace): Stage[] {
    const ASSEMBLER_STAGE_KEYS = new Set(['db-integration']);
    const own = (this.miniStageMap()[ws.id] ?? []).filter(s => !ASSEMBLER_STAGE_KEYS.has(s.key));
    const epicStages = this.perEpicGlobalStages();
    if (!epicStages.length) return own;

    const artsByKey = this.miniArtifactMap()[ws.id] ?? {};
    const perEpicResolved = epicStages.map(s => {
      const arts = artsByKey[s.key] ?? [];
      if (arts.length > 0 && arts.every(a => (a.status ?? '').toUpperCase() === 'APPROVED')) {
        return { ...s, state: 'completed' };
      }
      if (arts.some(a => (a.status ?? '').toUpperCase() === 'DRAFT')) {
        return { ...s, state: 'waiting_for_approval' };
      }
      // No artifacts → stage has not run → keep raw state (not_ready → grey dot).
      // LLD becomes 'ready' after Feature + User Story complete even when Coverage
      // has not run, so mini stage readiness cannot be used to infer completion.
      return s;
    });
    return [...perEpicResolved, ...own];
  }

  abbr(s: Stage): string {
    return STAGE_ABBR[s.key] ?? s.key;
  }

  dotClass(state: string): string {
    if (state === "completed" || state === "approved") return "dot-green";
    if (state === "running" || state === "queued") return "dot-blue";
    if (state === "waiting_for_approval") return "dot-orange";
    if (state === "failed") return "dot-red";
    return "dot-grey";
  }

  labelFor(state: string): string {
    return STATE_LABEL[state] ?? state;
  }

  chipTone(state: string): string {
    if (state === "completed" || state === "approved") return "ps-chip-ok";
    if (state === "running" || state === "queued") return "ps-chip-run";
    if (state === "waiting_for_approval") return "ps-chip-wait";
    if (state === "failed") return "ps-chip-fail";
    return "ps-chip-idle";
  }

  abbrKey(key: string): string {
    return STAGE_ABBR[key] ?? key;
  }

}
