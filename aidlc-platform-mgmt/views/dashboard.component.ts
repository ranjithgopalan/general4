import { Component, OnInit, computed, signal } from "@angular/core";
import { Router } from "@angular/router";

import { AidlcPlatformMgmtService, Project } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

const PAGE_SIZE = 8;

/** Dashboard — horizontal table of onboarded projects with KPI tiles. */
@Component({
  selector: "app-apm-dashboard",
  template: `
    <!-- KPI tiles -->
    <div class="apm-kpis">
      <div class="apm-kpi primary">
        <b>{{ store.loading() ? '…' : store.projects().length }}</b>
        <div class="lbl">⊞ Projects</div>
        <div class="foot">{{ store.loading() ? 'loading…' : notOnboarded() + ' not yet onboarded' }}</div>
      </div>
      <div class="apm-kpi ok">
        <b>{{ store.loading() ? '…' : inDelivery() }}</b>
        <div class="lbl">✓ In delivery</div>
        <div class="foot">{{ store.loading() ? '' : totalOpenEpics() + ' EPIC workspaces open' }}</div>
      </div>
      <div class="apm-kpi info">
        <b>{{ store.loading() ? '…' : inDefinition() }}</b>
        <div class="lbl">⊟ In definition</div>
        <div class="foot">working down the document ladder</div>
      </div>
      <div class="apm-kpi warn">
        <b>{{ store.loading() ? '…' : awaiting() }}</b>
        <div class="lbl">△ Awaiting approval</div>
        <div class="foot">{{ store.loading() ? '' : (awaiting() === 0 ? 'nothing waiting on a human' : 'stages need a decision') }}</div>
      </div>
    </div>

    <!-- Table -->
    <div class="apm-card">
      <div class="apm-card-head">
        <div style="display:flex;align-items:center;gap:10px;flex:1;min-width:0;">
          <h4 style="white-space:nowrap;">Onboarded workspaces</h4>
          <input
            style="flex:1;max-width:280px;padding:5px 10px;border:1px solid var(--border);border-radius:8px;font:inherit;font-size:13px;"
            placeholder="Search projects…"
            [ngModel]="searchText()"
            (ngModelChange)="onSearch($event)" />
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="apm-btn sm outline" [disabled]="store.loading()" (click)="store.load()">
            {{ store.loading() ? '…' : 'Refresh' }}
          </button>
          <button class="apm-btn green" (click)="goOnboard()">⊕ Onboard a Workspace</button>
        </div>
      </div>
      <div class="apm-card-body" style="padding:0">

        <!-- skeleton rows while loading -->
        <ng-container *ngIf="store.loading()">
          <div class="apm-dash-skel" *ngFor="let _ of skeletonRows">
            <span class="apm-skel-block" style="width:140px"></span>
            <span class="apm-skel-block" style="width:70px"></span>
            <span class="apm-skel-block" style="width:120px"></span>
            <span class="apm-skel-block" style="width:60px"></span>
            <span class="apm-skel-block" style="width:50px"></span>
          </div>
        </ng-container>

        <table class="apm-table" *ngIf="!store.loading() && filteredProjects().length; else empty">
          <thead>
            <tr>
              <th>PROJECT</th>
              <th>STAGE</th>
              <th>GLOBAL TIER</th>
              <th>EPICS</th>
              <th>APPROVALS</th>
              <th>DOMAIN</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr *ngFor="let p of pagedProjects()" class="pick" (click)="open(p)">
              <td>
                <b>{{ p.name }}</b>
                <span class="apm-chip" style="margin-left:8px;font-size:11px">{{ p.kb_application_id }}</span>
              </td>
              <td>
                <span class="apm-chip" [ngClass]="stageTone(p)">{{ p.stage || '—' }}</span>
              </td>
              <td>
                <div style="display:flex;align-items:center;gap:8px">
                  <span style="font-size:12px;color:var(--text-secondary);white-space:nowrap">
                    {{ p.global_progress?.completed || 0 }}/{{ p.global_progress?.stages || 7 }}
                  </span>
                  <div class="apm-prog-bar">
                    <div class="apm-prog-fill" [style.width]="pct(p) + '%'"></div>
                  </div>
                </div>
              </td>
              <td>{{ p.mini_workspace_count ? (p.open_mini_count || 0) + ' open / ' + p.mini_workspace_count : '—' }}</td>
              <td>{{ p.global_progress?.waiting_for_approval || '—' }}</td>
              <td>{{ p.business_domain || '—' }}</td>
              <td style="white-space:nowrap;text-align:right" (click)="$event.stopPropagation()">
                <button class="apm-btn sm" (click)="open(p)">Open</button>
                <button class="apm-btn danger sm" style="margin-left:6px"
                        [disabled]="deleting.has(p.kb_application_id)"
                        (click)="remove(p)">
                  {{ deleting.has(p.kb_application_id) ? 'Deleting…' : 'Delete' }}
                </button>
              </td>
            </tr>
          </tbody>
        </table>

        <!-- Pagination footer -->
        <div *ngIf="!store.loading() && totalPages() > 1"
             style="display:flex;align-items:center;justify-content:space-between;padding:10px 16px;border-top:1px solid var(--border);font-size:12.5px;color:var(--text-secondary);">
          <span>Showing {{ rangeStart() }}–{{ rangeEnd() }} of {{ filteredProjects().length }}</span>
          <div style="display:flex;gap:4px;align-items:center;">
            <button class="apm-pg-btn" [disabled]="page() === 1" (click)="goPage(page() - 1)">‹</button>
            <ng-container *ngFor="let n of pageNumbers()">
              <button class="apm-pg-btn" [class.on]="n === page()" (click)="goPage(n)">{{ n }}</button>
            </ng-container>
            <button class="apm-pg-btn" [disabled]="page() === totalPages()" (click)="goPage(page() + 1)">›</button>
          </div>
          <span style="color:var(--text-secondary);">{{ pageSize }} per page</span>
        </div>

        <ng-template #empty>
          <div class="apm-gear-empty" *ngIf="!store.loading()">
            {{ searchText() ? 'No projects match "' + searchText() + '".' : 'No workspaces onboarded yet. Click "⊕ Onboard a Workspace" to get started.' }}
          </div>
        </ng-template>
      </div>
    </div>

    <style>
      .apm-dash-skel {
        display: flex; gap: 20px; align-items: center;
        padding: 14px 20px; border-bottom: 1px solid var(--border, #1e3a5f);
      }
      .apm-skel-block {
        height: 14px; border-radius: 6px;
        background: linear-gradient(90deg, #1e3a5f 25%, #2a4a72 50%, #1e3a5f 75%);
        background-size: 200% 100%;
        animation: apm-shimmer 1.4s infinite;
        display: inline-block; flex-shrink: 0;
      }
      @keyframes apm-shimmer {
        0%   { background-position: 200% 0; }
        100% { background-position: -200% 0; }
      }
      .apm-pg-btn {
        min-width: 28px; height: 28px; border-radius: 6px;
        border: 1px solid var(--border); background: var(--card-bg);
        color: var(--text-secondary); font: inherit; font-size: 12px; font-weight: 600;
        cursor: pointer; display: flex; align-items: center; justify-content: center;
      }
      .apm-pg-btn:disabled { opacity: .4; cursor: default; }
      .apm-pg-btn.on { background: var(--aig-cobalt, #0077c8); color: #fff; border-color: transparent; }
      .apm-pg-btn:not(:disabled):not(.on):hover { background: var(--panel-bg); color: var(--text-primary); }
    </style>
  `,
})
export class DashboardComponent implements OnInit {
  readonly deleting = new Set<string>();
  readonly skeletonRows = [1, 2, 3];
  readonly pageSize = PAGE_SIZE;

  readonly page = signal(1);
  readonly searchText = signal('');

  readonly notOnboarded = computed(() => this.store.projects().filter(p => !p.onboarded).length);
  readonly inDelivery = computed(() => this.store.projects().filter(p => p.stage === 'delivery').length);
  readonly inDefinition = computed(() => this.store.projects().filter(p => p.onboarded && p.stage !== 'delivery').length);
  readonly awaiting = computed(() => this.store.projects().reduce((s, p) => s + (p.global_progress?.waiting_for_approval ?? 0), 0));
  readonly totalOpenEpics = computed(() => this.store.projects().reduce((s, p) => s + (p.open_mini_count ?? 0), 0));

  readonly filteredProjects = computed(() => {
    const q = this.searchText().trim().toLowerCase();
    const list = q
      ? this.store.projects().filter(p =>
          p.name?.toLowerCase().includes(q) || p.kb_application_id?.toLowerCase().includes(q))
      : [...this.store.projects()];
    return list.sort((a, b) => {
      if (!b.created_at && !a.created_at) return 0;
      if (!b.created_at) return -1;
      if (!a.created_at) return 1;
      return b.created_at.localeCompare(a.created_at);
    });
  });

  readonly totalPages = computed(() => Math.ceil(this.filteredProjects().length / PAGE_SIZE));
  readonly pagedProjects = computed(() => {
    const p = this.page();
    return this.filteredProjects().slice((p - 1) * PAGE_SIZE, p * PAGE_SIZE);
  });
  readonly rangeStart = computed(() => Math.min((this.page() - 1) * PAGE_SIZE + 1, this.filteredProjects().length));
  readonly rangeEnd = computed(() => Math.min(this.page() * PAGE_SIZE, this.filteredProjects().length));
  readonly pageNumbers = computed(() => {
    const total = this.totalPages();
    const cur = this.page();
    if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
    const pages: number[] = [1];
    if (cur > 3) pages.push(-1);
    for (let i = Math.max(2, cur - 1); i <= Math.min(total - 1, cur + 1); i++) pages.push(i);
    if (cur < total - 2) pages.push(-1);
    pages.push(total);
    return pages;
  });

  constructor(
    readonly store: WorkspaceStore,
    private readonly svc: AidlcPlatformMgmtService,
    private readonly router: Router,
  ) {}

  ngOnInit(): void {
    // Always refresh — store shows cached data instantly if already loaded, fetches silently in background.
    this.store.load();
  }

  onSearch(value: string): void {
    this.searchText.set(value);
    this.page.set(1);
  }

  goPage(n: number): void {
    if (n < 1 || n > this.totalPages()) return;
    this.page.set(n);
  }

  open(p: Project): void {
    this.store.selectProject(p.kb_application_id);
    this.router.navigate(['/aidlc-platform-management/pipeline']);
  }

  goOnboard(): void {
    this.router.navigate(['/aidlc-platform-management/onboard']);
  }

  remove(p: Project): void {
    const ok = typeof window === 'undefined' || window.confirm(
      `Delete project "${p.name}"?\n\nThis permanently removes its workspaces, runs, artifacts and approvals. This cannot be undone.`);
    if (!ok) return;
    this.deleting.add(p.kb_application_id);
    this.svc.deleteProject(p.kb_application_id).subscribe(() => {
      this.deleting.delete(p.kb_application_id);
      if (this.store.selectedProjectId() === p.kb_application_id) this.store.clearProject();
      this.store.load();
    });
  }

  pct(p: Project): number {
    const total = p.global_progress?.stages || 7;
    const done = p.global_progress?.completed || 0;
    return total ? Math.round((done / total) * 100) : 0;
  }

  stageTone(p: Project): string {
    if (p.stage === 'delivery') return 'ok';
    if (p.onboarded) return 'amber';
    return '';
  }
}
