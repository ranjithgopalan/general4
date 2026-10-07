import { Component, OnInit, effect } from "@angular/core";

import { AidlcPlatformMgmtService } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

/**
 * ASP dependencies of the selected project: the ASP files the project uploaded, placed on its Gear ID's
 * dependency graph (module / sub-module / domain interdependencies) together with the Global Library
 * cards (BR / FR / SCR / CMP / API / ENT / WF) made for each page. The same card component the Global
 * Library page uses, in project mode, so the forward-engineering stages can see the picture before
 * the PRD and FRD are written from those cards.
 *
 * The Gear ID comes from the project when it was onboarded with one; otherwise it is picked here from
 * the gears that have a dependency graph (remembered per project in this browser).
 */
@Component({
  selector: "app-apm-asp-dependencies",
  template: `
    <ng-container *ngIf="store.selectedProject() as project; else pickProject">
      <div class="apm-crumb">{{ project.name }} / ASP dependencies</div>
      <div class="apm-hdr">
        <div class="apm-hdr-icon">🕸</div>
        <div>
          <h2>ASP dependencies</h2>
          <p class="sub">
            The ASP files this project uploaded, on the dependency graph of its Gear ID: which modules, sub-modules
            and domains they connect to, and the Global Library cards each page contributes to the PRD and FRD.
            Pages outside the project appear faded.
          </p>
        </div>
      </div>

      <div class="apm-card" *ngIf="!project.gear_id">
        <div class="apm-card-body" style="display:flex;flex-wrap:wrap;gap:10px 18px;align-items:center;">
          <label style="display:inline-flex;align-items:center;gap:8px;">Gear ID
            <select [(ngModel)]="gearId" (ngModelChange)="rememberGear(project.kb_application_id)"
                    style="padding:6px 8px;border:1px solid var(--border);border-radius:8px;font:inherit;min-width:220px;">
              <option value="">— Select Gear ID —</option>
              <option *ngFor="let g of gears" [value]="g.gear_id">{{ g.gear_id }} ({{ g.versions }} version{{ g.versions === 1 ? '' : 's' }}{{ g.current ? '' : ', none current' }})</option>
            </select>
          </label>
          <span class="sub">This project was onboarded without a Gear ID, so pick the one whose library it belongs to.</span>
        </div>
      </div>

      <app-apm-library-dependency-graph *ngIf="effectiveGear(project)" [gearId]="effectiveGear(project)" [projectId]="project.kb_application_id"></app-apm-library-dependency-graph>
      <div class="apm-card" *ngIf="!effectiveGear(project)"><div class="apm-card-body sub">Select a Gear ID above to place the project's ASP files on its dependency graph.</div></div>
    </ng-container>
    <ng-template #pickProject>
      <div class="apm-card"><div class="apm-card-body sub">Pick a project on the Dashboard to see the dependencies of its ASP files.</div></div>
    </ng-template>
  `,
})
export class AspDependenciesComponent implements OnInit {
  gears: { gear_id: string; versions: number; current: string | null; latest: string | null }[] = [];
  gearId = "";

  constructor(readonly store: WorkspaceStore, private readonly svc: AidlcPlatformMgmtService) {
    effect(() => {
      const p = this.store.selectedProject();
      if (p) this.gearId = p.gear_id || this.recallGear(p.kb_application_id) || this.gearId;
    });
  }

  ngOnInit(): void {
    this.svc.libraryGraphGears().subscribe((r) => {
      this.gears = r?.items ?? [];
      if (!this.gearId && this.gears.length === 1) this.gearId = this.gears[0].gear_id;
    });
  }

  effectiveGear(project: { gear_id?: string | null }): string {
    return project.gear_id || this.gearId || "";
  }

  rememberGear(projectId: string): void {
    try { localStorage.setItem(`apm.asp-dependencies.gear.${projectId}`, this.gearId); } catch { /* ignore */ }
  }

  private recallGear(projectId: string): string {
    try { return localStorage.getItem(`apm.asp-dependencies.gear.${projectId}`) ?? ""; } catch { return ""; }
  }
}
