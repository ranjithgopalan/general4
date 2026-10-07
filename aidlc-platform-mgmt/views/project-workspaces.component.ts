import { Component, OnInit, signal } from "@angular/core";
import { ActivatedRoute, Router } from "@angular/router";

import { AidlcPlatformMgmtService, Stage, Workspace } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

/** Level-2 view: workspace list for a single project. Reached by clicking a project card on the dashboard. */
@Component({
  selector: "app-apm-project-workspaces",
  template: `
    <div class="apm-proj-back"><a (click)="goBack()">← Dashboard</a></div>
    <div class="apm-proj-hdr">
      <h2>{{ store.selectedProject()?.name || projectId }}</h2>
      <div class="apm-gear-id">{{ projectId }}</div>
    </div>

    <div class="apm-ws-toolbar">
      <h4>Workspaces</h4>
      <input class="apm-search" [(ngModel)]="searchTerm" placeholder="Search by name or ID…" />
      <button class="apm-btn green" (click)="goOnboard()">⊕ New Workspace</button>
    </div>

    <table class="apm-table apm-ws-table" *ngIf="filteredWorkspaces().length; else empty">
      <thead>
        <tr>
          <th>WORKSPACE</th>
          <th>PIPELINE</th>
          <th>EPICS</th>
          <th>ARTIFACTS</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr *ngFor="let ws of filteredWorkspaces()">
          <td>
            <b>{{ ws.label || ws.id }}</b>
            <div class="apm-gear-id">{{ ws.id }}</div>
            <span class="apm-chip" style="margin-top:4px;display:inline-block" *ngIf="ws.tier">{{ ws.tier }}</span>
          </td>
          <td>
            <div class="apm-mini-stepper">
              <ng-container *ngIf="stagesFor(ws) as stages">
                <ng-container *ngIf="stages.length; else loadingStages">
                  <ng-container *ngFor="let s of stages; let i = index">
                    <span *ngIf="i > 0" class="apm-conn-mini"
                          [class.done]="stages[i - 1]?.state === 'completed'"></span>
                    <div class="apm-dot-wrap">
                      <span class="apm-dot-mini" [ngClass]="dotTone(s)" [title]="s.name"></span>
                      <span class="apm-dot-lbl">{{ shortLabel(s) }}</span>
                    </div>
                  </ng-container>
                </ng-container>
              </ng-container>
              <ng-template #loadingStages>
                <span class="apm-gear-id" style="font-size:11px">—</span>
              </ng-template>
            </div>
          </td>
          <td>{{ epicCount(ws) }}</td>
          <td>{{ artifactCounts()[ws.id] ?? '—' }}</td>
          <td style="white-space:nowrap;text-align:right">
            <button class="apm-btn sm" (click)="openPipeline(ws)">Open</button>
            <button class="apm-btn danger sm" style="margin-left:6px"
                    [disabled]="deleting.has(ws.id)"
                    (click)="remove(ws); $event.stopPropagation()">
              {{ deleting.has(ws.id) ? '…' : '🗑' }}
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <ng-template #empty>
      <div class="apm-gear-empty">No workspaces found{{ searchTerm ? ' matching "' + searchTerm + '"' : '' }}.</div>
    </ng-template>
  `,
})
export class ProjectWorkspacesComponent implements OnInit {
  private readonly PER_EPIC_KEYS = new Set(['feature', 'user-story', 'coverage']);

  private readonly STAGE_ABBR: Record<string, string> = {
    // Global stages
    'prd': 'PRD', 'frd': 'FRD', 'epic-set': 'EPIC',
    'feature': 'Feat', 'user-story': 'US', 'coverage': 'Cov',
    // Architecture stages
    'adr': 'ADR', 'nfr': 'NFR', 'sdd': 'SDD', 'srd': 'SRD',
    // Mini stages
    'lld': 'LLD', 'ui-code': 'UI', 'api-code': 'API',
    'db-integration': 'DB', 'security-opa': 'Sec',
    'api-test': 'Test', 'ui-smoke': 'Smoke', 'ui-e2e': 'E2E',
  };

  projectId = "";
  searchTerm = "";
  readonly workspaces = signal<Workspace[]>([]);
  readonly artifactCounts = signal<Record<string, number>>({});
  readonly wsStages = signal<Record<string, Stage[]>>({});
  // Per-EPIC global stages (feature / user-story / coverage) fetched once from the global workspace.
  readonly perEpicStages = signal<Stage[]>([]);
  readonly deleting = new Set<string>();

  filteredWorkspaces(): Workspace[] {
    const term = this.searchTerm.toLowerCase();
    return this.workspaces().filter(w =>
      !term ||
      w.id.toLowerCase().includes(term) ||
      (w.label ?? "").toLowerCase().includes(term) ||
      (w.tier ?? "").toLowerCase().includes(term)
    );
  }

  constructor(
    readonly store: WorkspaceStore,
    private readonly svc: AidlcPlatformMgmtService,
    private readonly router: Router,
    private readonly route: ActivatedRoute,
  ) {}

  ngOnInit(): void {
    this.projectId = this.route.snapshot.paramMap.get("id") ?? "";
    this.store.selectProject(this.projectId);
    this.loadWorkspaces();
  }

  private loadWorkspaces(): void {
    this.svc.listWorkspaces(this.projectId, true).subscribe(ws => {
      this.workspaces.set(ws);
      for (const w of ws) {
        this.svc.artifacts(w.id).subscribe(r => {
          this.artifactCounts.update(c => ({ ...c, [w.id]: r.items?.length ?? 0 }));
        });
        this.svc.workspaceStages(w.id).subscribe(r => {
          if (!r?.stages?.length) return;
          if (w.tier === 'global') {
            // Extract per-EPIC global stages (Feature / User Story / Coverage).
            // These run in the global thread but are scoped per EPIC — store once
            // so mini rows can prepend them to show the full pipeline sequence.
            const epicStages = r.stages.filter(s => this.PER_EPIC_KEYS.has(s.key));
            if (epicStages.length) this.perEpicStages.set(epicStages);
            // Only store non-per-epic stages under the global workspace key
            const globalOnly = r.stages.filter(s => !this.PER_EPIC_KEYS.has(s.key));
            this.wsStages.update(m => ({ ...m, [w.id]: globalOnly }));
          } else {
            this.wsStages.update(m => ({ ...m, [w.id]: r.stages }));
          }
        });
      }
    });
  }

  private readonly ACTIVE_STATES = new Set(['running', 'queued', 'completed', 'waiting_for_approval', 'failed']);

  /** Full ordered stage list for a workspace row: mini gets per-EPIC stages prepended. */
  stagesFor(ws: Workspace): Stage[] {
    const own = this.wsStages()[ws.id] ?? [];
    if (ws.tier !== 'mini') return own;
    const epicStages = this.perEpicStages();
    if (!epicStages.length) return own;
    // Mini stages only begin after ALL per-EPIC stages (Feature/US/Coverage) pass.
    // If any mini stage has been touched, per-EPIC stages are definitively complete
    // for this EPIC — override the (inaccurate) global aggregate state.
    const miniStarted = own.some(s => this.ACTIVE_STATES.has(s.state));
    const normalizedEpic: Stage[] = miniStarted
      ? epicStages.map(s => ({ ...s, state: 'completed' }))
      : epicStages;
    return [...normalizedEpic, ...own];
  }

  dotTone(s: Stage): string {
    if (s.state === 'completed') return 'good';
    if (['running', 'queued', 'waiting_for_approval', 'failed'].includes(s.state)) return 'run';
    return 'idle';
  }

  shortLabel(s: Stage): string {
    return this.STAGE_ABBR[s.key] ?? s.key.toUpperCase().slice(0, 4);
  }

  openPipeline(ws: Workspace): void {
    this.store.select(ws.id);
    this.router.navigate(["/aidlc-platform-management/pipeline"]);
  }

  goBack(): void {
    this.store.clearProject();
    this.router.navigate(["/aidlc-platform-management/dashboard"]);
  }

  goOnboard(): void {
    this.router.navigate(["/aidlc-platform-management/onboard"]);
  }

  remove(ws: Workspace): void {
    const ok = typeof window === "undefined" || window.confirm(
      `Delete workspace "${ws.label || ws.id}"?\n\nThis removes all its runs, artifacts and approvals.`);
    if (!ok) return;
    this.deleting.add(ws.id);
    this.svc.deleteProject(ws.id).subscribe(() => {
      this.deleting.delete(ws.id);
      this.loadWorkspaces();
    });
  }

  epicCount(ws: Workspace): string | number {
    if (ws.tier === "global") {
      const project = this.store.selectedProject();
      if (project?.mini_workspace_count) {
        return `${project.open_mini_count ?? 0} / ${project.mini_workspace_count}`;
      }
    }
    return "—";
  }
}
