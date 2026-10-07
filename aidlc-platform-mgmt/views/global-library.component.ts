import { Component, OnInit } from "@angular/core";

import {
  AidlcPlatformMgmtService,
  LibraryDocumentResult,
  LibraryGraphUploadResult,
  LibraryStatus,
  LibraryVersions,
} from "../services/aidlc-platform-mgmt.service";

/** Global Card Library — scoped per Gear ID. Upload RED JSON + screens once; projects upload ASP files. */
@Component({
  selector: "app-apm-global-library",
  template: `
    <div class="apm-crumb">Global Library</div>
    <div class="apm-hdr">
      <div class="apm-hdr-icon">📚</div>
      <div>
        <h2>Global Card Library</h2>
        <p class="sub">Upload the RED JSON and screens document once per Gear ID. Every project selects its cards by uploading ASP files.</p>
      </div>
    </div>

    <div class="apm-banner ok" *ngIf="notice">{{ notice }}</div>
    <div class="apm-banner err" *ngIf="error">{{ error }}</div>

    <!-- Step 1: Gear ID selection — gates everything below -->
    <div class="apm-card">
      <div class="apm-card-head"><h4>Application (Gear ID)</h4></div>
      <div class="apm-card-body">
        <div class="apm-field">
          <label>Gear ID</label>
          <select [(ngModel)]="gearId" (ngModelChange)="onGearChange()"
                  style="padding:8px;border:1px solid var(--border);border-radius:8px;font:inherit;width:100%;">
            <option value="">— Select Gear ID —</option>
            <option value="japan">japan</option>
            <option value="1429">1429 (UW credit)</option>
          </select>
          <p class="apm-field-hint">
            The library is stored and approved per Gear ID. Downstream agents resolve the approved library
            for a project by matching the project's Gear ID. Each Gear ID has its own S3 path, DB version,
            and approval state.
          </p>
        </div>
      </div>
    </div>

    <!-- Only show the rest once a Gear ID is selected -->
    <ng-container *ngIf="gearId">

      <!-- Library status -->
      <div class="apm-card" *ngIf="status">
        <div class="apm-card-head">
          <h4>Library status</h4>
          <span style="font-size:12px;color:var(--text-secondary);margin-left:8px;">Gear: <b>{{ gearId }}</b></span>
        </div>
        <div class="apm-card-body" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;">
          <div class="apm-stat-tile">
            <div class="apm-stat-val">{{ status.corpus.modules.length }}</div>
            <div class="apm-stat-lbl">Modules (JSON)</div>
          </div>
          <div class="apm-stat-tile">
            <div class="apm-stat-val">{{ status.corpus.screens.length }}</div>
            <div class="apm-stat-lbl">Screen catalogues</div>
          </div>
          <div class="apm-stat-tile"
               [style.background]="status.approved ? '#edfdf5' : (status.latest_build ? '#fff8e1' : '#fafafa')"
               [style.border-color]="status.approved ? '#86efac' : (status.latest_build ? '#fcd34d' : '#e5e7eb')">
            <div class="apm-stat-val"
                 [style.color]="status.approved ? '#15803d' : (status.latest_build ? '#b45309' : '#6b7280')">
              {{ status.approved ? 'Approved' : (status.latest_build ? 'Pending approval' : 'Not built') }}
            </div>
            <div class="apm-stat-lbl">Build state</div>
          </div>
        </div>

        <!-- S3 / DB path info -->
        <div class="apm-card-body" style="padding-top:0;border-top:1px solid var(--border,#e5e7eb);margin-top:4px;">
          <p class="sub" style="margin-bottom:6px;font-size:11px;">Storage paths for Gear ID <b>{{ gearId }}</b>:</p>
          <div style="font-family:monospace;font-size:11px;color:var(--text-secondary);line-height:1.8;">
            <div>S3: <b>workspaces/_library/{{ gearId }}/corpus/</b></div>
            <div>S3: <b>workspaces/_library/{{ gearId }}/kb/</b></div>
            <div>DB: <b>kb_version = _library-{{ gearId }}-re-v&lt;N&gt;</b> (card builds, one per build; approval makes one ACTIVE)</div>
            <div>DB: <b>kb_version = _library-{{ gearId }}-deps-v&lt;N&gt;</b> (dependency graph, one per upload; current = ACTIVE)</div>
            <div>DB: <b>gear_id = _library-{{ gearId }}</b> / <b>_library-{{ gearId }}-deps</b></div>
          </div>
        </div>

        <!-- KB versions behind this Gear ID -->
        <div class="apm-card-body" style="padding-top:0;" *ngIf="versions && (versions.cards.items.length || versions.dependency_graph.items.length)">
          <p class="sub" style="margin-bottom:6px;">KB versions in the database:</p>
          <table class="apm-table" style="font-size:12px;">
            <thead><tr><th>Series</th><th>Version</th><th>State</th><th>Built</th><th>Nodes</th><th>Edges</th></tr></thead>
            <tbody>
              <tr *ngFor="let v of newestFirst(versions.cards.items)">
                <td>Card library</td><td style="font-family:monospace">{{ v.kb_version }}</td>
                <td><span class="apm-chip" [class.ok]="v.status === 'ACTIVE'" [class.amber]="v.status === 'STAGING'">{{ v.status === 'ACTIVE' ? 'active' : (v.status || '').toLowerCase() }}</span></td>
                <td>{{ v.built_at | date:'medium' }}</td><td>{{ v.nodes_total }}</td><td>{{ v.edges_total }}</td>
              </tr>
              <tr *ngFor="let v of newestFirst(versions.dependency_graph.items)">
                <td>Dependency graph</td><td style="font-family:monospace">{{ v.kb_version }}</td>
                <td><span class="apm-chip" [class.ok]="v.is_current" [class.amber]="!v.is_current && v.status === 'STAGING'">{{ v.is_current ? 'current' : (v.status || '').toLowerCase() }}</span></td>
                <td>{{ v.built_at | date:'medium' }}</td><td>{{ v.nodes_total }}</td><td>{{ v.edges_total }}</td>
              </tr>
            </tbody>
          </table>
          <p class="apm-field-hint" *ngIf="!versions.cards.items.length">No card-library build has been published to the database yet.</p>
        </div>

        <!-- Loaded modules list -->
        <div class="apm-card-body" style="padding-top:0;" *ngIf="status.corpus.modules.length">
          <p class="sub" style="margin-bottom:6px;">Modules loaded:</p>
          <div *ngFor="let m of status.corpus.modules"
               style="font-size:12px;padding:4px 8px;background:var(--surface-2,#f5f5f5);border-radius:6px;margin-bottom:4px;">
            <b>{{ m.module_key }}</b>
            <span *ngIf="m.module" style="color:var(--text-secondary);margin-left:6px;">{{ m.module }}</span>
            <span *ngIf="m.app" style="color:var(--text-secondary);margin-left:6px;">· {{ m.app }}</span>
          </div>
        </div>
      </div>

      <!-- Dependency graph: module / domain interdependencies of every ASP page, read from the KB graph tables -->
      <app-apm-library-dependency-graph [gearId]="gearId" [reloadKey]="graphReload"></app-apm-library-dependency-graph>

      <!-- Upload section -->
      <div class="apm-card">
        <div class="apm-card-head">
          <h4>Upload library documents</h4>
          <span style="font-size:12px;color:var(--text-secondary);margin-left:8px;">Gear: <b>{{ gearId }}</b></span>
        </div>
        <div class="apm-card-body">

          <!-- RED JSON -->
          <div class="apm-field">
            <label>RED JSON <span style="font-weight:400;color:var(--text-secondary)">(required, one per module)</span></label>
            <input #jsonFile type="file" accept=".json" (change)="pickJson($event)" hidden />
            <div class="apm-file-pick">
              <button type="button" class="apm-btn outline" (click)="jsonFile.click()">Upload RED JSON…</button>
              <span class="sub">{{ jsonFileName || 'No file chosen' }}</span>
            </div>
            <p class="apm-field-hint">
              Cards JSON in re-cards format (BR/FR/SCR/CMP/API/ENT/WF per ASP page).
              Re-uploading replaces the existing module. Stored under Gear ID <b>{{ gearId }}</b>.
            </p>
          </div>

          <button class="apm-btn" style="margin-bottom:18px;"
                  [disabled]="!jsonFile$ || busy"
                  (click)="uploadJson()">
            {{ busyJson ? 'Uploading…' : 'Upload JSON' }}
          </button>

          <!-- JSON result -->
          <div *ngIf="jsonResult"
               style="margin-bottom:16px;padding:12px;background:var(--surface-2,#f5f5f5);border-radius:8px;font-size:13px;">
            <b style="display:block;margin-bottom:4px;">
              ✔ {{ jsonResult.module_key }} — {{ jsonResult.pages }} pages, {{ jsonResult.cards_total }} cards
              <span *ngIf="jsonResult.parsed_by === 'llm-fallback'"
                    style="color:#b45309;margin-left:6px;">(LLM fallback used)</span>
            </b>
            <span *ngIf="jsonResult.edges">{{ jsonResult.edges }} edges · </span>
            <ng-container *ngIf="jsonResult.cards">
              <span *ngFor="let k of cardKinds(jsonResult.cards)" style="margin-right:8px;">
                {{ k }}: {{ jsonResult.cards[k] }}
              </span>
            </ng-container>
            <div *ngFor="let w of jsonResult.warnings || []" style="color:#b45309;margin-top:4px;">⚠ {{ w }}</div>
          </div>

          <hr style="border:none;border-top:1px solid var(--border,#e5e7eb);margin:4px 0 16px;" />

          <!-- RED file-analysis JSON: page dependencies -->
          <div class="apm-field">
            <label>RED file-analysis JSON <span style="font-weight:400;color:var(--text-secondary)">(optional, page dependencies)</span></label>
            <input #analysisFile type="file" accept=".json" (change)="pickAnalysis($event)" hidden />
            <div class="apm-file-pick">
              <button type="button" class="apm-btn outline" (click)="analysisFile.click()">Upload RED analysis…</button>
              <span class="sub">{{ analysisFileName || 'No file chosen' }}</span>
            </div>
            <p class="apm-field-hint">
              The RED per-file analysis export (result.files[] with internal_dependencies and entry points).
              Page-to-page references are derived and written to the KB graph tables for Gear ID <b>{{ gearId }}</b>;
              the dependency graph above reads them. Re-uploading replaces the graph.
            </p>
          </div>

          <label style="display:inline-flex;align-items:center;gap:6px;font-size:13px;margin:0 16px 12px 0;">
            <input type="checkbox" [(ngModel)]="makeCurrent" /> Make this version current after upload
            <span class="sub" style="font-size:11px;">(unchecked: keep the current version; switch later from the graph's Versions tab)</span>
          </label>
          <br />
          <button class="apm-btn" style="margin-bottom:18px;"
                  [disabled]="!analysis$ || busy"
                  (click)="uploadAnalysis()">
            {{ busyAnalysis ? 'Deriving…' : 'Upload analysis' }}
          </button>

          <div *ngIf="analysisResult"
               style="margin-bottom:16px;padding:12px;background:var(--surface-2,#f5f5f5);border-radius:8px;font-size:13px;">
            <b style="display:block;margin-bottom:4px;">
              ✔ Dependency graph v{{ analysisResult.version }} built — {{ analysisResult.pages }} pages, {{ analysisResult.edges }} edges, {{ analysisResult.modules }} modules
              <span [style.color]="analysisResult.is_current ? '#15803d' : '#b45309'" style="margin-left:6px;">
                {{ analysisResult.is_current ? '· now the current version' : '· not current (the previous version stays in use)' }}
              </span>
            </b>
            <span>KB version <span style="font-family:monospace">{{ analysisResult.kb_version }}</span></span>
            <ng-container *ngIf="analysisResult.by_label">
              <span *ngFor="let k of cardKinds(analysisResult.by_label)" style="margin-left:8px;">{{ k }}: {{ analysisResult.by_label[k] }}</span>
            </ng-container>
            <div *ngIf="analysisResult.ambiguous_edges" style="color:#b45309;margin-top:4px;">
              ⚠ {{ analysisResult.ambiguous_edges }} references matched a page name that exists in several folders (linked to each, flagged inferred).
            </div>
          </div>

          <hr style="border:none;border-top:1px solid var(--border,#e5e7eb);margin:4px 0 16px;" />

          <!-- Screens doc -->
          <div class="apm-field">
            <label>Screens Word document <span style="font-weight:400;color:var(--text-secondary)">(optional)</span></label>
            <input #screensFile type="file" accept=".docx" (change)="pickScreens($event)" hidden />
            <div class="apm-file-pick">
              <button type="button" class="apm-btn outline" (click)="screensFile.click()">Upload screens .docx…</button>
              <span class="sub">{{ screensFileName || 'No file chosen' }}</span>
            </div>
            <p class="apm-field-hint">
              Word document with ASP file names (e.g. "File name – CompanySelect.asp") and screenshots.
              Screenshots bind to SCR cards. Re-uploading merges; newest wins per page.
            </p>
          </div>

          <button class="apm-btn" style="margin-bottom:18px;"
                  [disabled]="!screens$ || busy"
                  (click)="uploadScreens()">
            {{ busyScreens ? 'Uploading…' : 'Upload screens' }}
          </button>

          <!-- Screens result -->
          <div *ngIf="screensResult"
               style="padding:12px;background:var(--surface-2,#f5f5f5);border-radius:8px;font-size:13px;">
            <b>✔ Screens uploaded — {{ screensResult.pages }} pages, {{ screensResult.images_bound }} bound</b>
            <span *ngIf="screensResult.images_unbound" style="color:#b45309;margin-left:8px;">
              {{ screensResult.images_unbound }} unbound
            </span>
            <div *ngFor="let r of screensResult.report || []"
                 style="color:var(--text-secondary);margin-top:2px;font-size:11px;">{{ r }}</div>
          </div>

        </div>
      </div>

      <!-- Build -->
      <div class="apm-card">
        <div class="apm-card-head">
          <h4>Build library</h4>
          <span style="font-size:12px;color:var(--text-secondary);margin-left:8px;">Gear: <b>{{ gearId }}</b></span>
        </div>
        <div class="apm-card-body">
          <p class="sub" style="margin-bottom:12px;">
            Merges all modules for Gear ID <b>{{ gearId }}</b>, binds screenshots, writes the page index to S3
            and KB version to the database. A build must be approved before any project under this Gear ID can use it.
            <span *ngIf="status?.dirty" style="color:#b45309;font-weight:500;margin-left:4px;">Unbuilt changes pending.</span>
          </p>
          <button class="apm-btn"
                  [disabled]="busyBuild || !hasCorpus()"
                  (click)="build()">
            {{ busyBuild ? 'Building…' : 'Build library' }}
          </button>
          <span class="sub" style="margin-left:10px;" *ngIf="!hasCorpus()">Upload at least one JSON module first</span>
          <div *ngIf="buildNotice"
               class="apm-banner"
               style="margin-top:12px;background:#e6f3fb;border-color:#b3d9f0;color:#0369a1;">
            {{ buildNotice }}
          </div>
        </div>
      </div>

    </ng-container>

    <!-- Placeholder when no Gear ID selected -->
    <div *ngIf="!gearId" class="apm-card">
      <div class="apm-card-body" style="text-align:center;padding:32px;color:var(--text-secondary);">
        Select a Gear ID above to view or upload the library for that application.
      </div>
    </div>
  `,
})
export class GlobalLibraryComponent implements OnInit {
  gearId = "";
  status: LibraryStatus | null = null;
  jsonResult: LibraryDocumentResult | null = null;
  screensResult: LibraryDocumentResult | null = null;

  jsonFile$: File | null = null;
  jsonFileName = "";
  screens$: File | null = null;
  screensFileName = "";
  analysis$: File | null = null;
  analysisFileName = "";
  analysisResult: LibraryGraphUploadResult | null = null;
  /** Whether a red-analysis upload becomes the Gear ID's current dependency-graph version. */
  makeCurrent = true;
  /** Bumped after a red-analysis upload so the dependency-graph card reloads. */
  graphReload = 0;
  /** KB versions behind this Gear ID (card builds + dependency-graph uploads), from GET /library/versions. */
  versions: LibraryVersions | null = null;

  busy = false;
  busyJson = false;
  busyScreens = false;
  busyAnalysis = false;
  busyBuild = false;

  notice = "";
  error = "";
  buildNotice = "";

  constructor(private readonly svc: AidlcPlatformMgmtService) {}

  ngOnInit(): void {}

  onGearChange(): void {
    this.status = null;
    this.jsonResult = null;
    this.screensResult = null;
    this.analysisResult = null;
    this.notice = "";
    this.error = "";
    this.buildNotice = "";
    if (this.gearId) this.loadStatus();
  }

  private loadStatus(): void {
    this.svc.getLibrary(this.gearId).subscribe(s => { this.status = s; });
    this.svc.libraryVersions(this.gearId).subscribe(v => { this.versions = v; });
  }

  /** Newest first, for the version lists in the status card. */
  newestFirst<T>(items: T[] | undefined): T[] { return (items ?? []).slice().reverse(); }

  pickJson(event: Event): void {
    this.jsonFile$ = (event.target as HTMLInputElement).files?.[0] ?? null;
    this.jsonFileName = this.jsonFile$?.name ?? "";
    this.jsonResult = null;
  }

  pickScreens(event: Event): void {
    this.screens$ = (event.target as HTMLInputElement).files?.[0] ?? null;
    this.screensFileName = this.screens$?.name ?? "";
    this.screensResult = null;
  }

  uploadJson(): void {
    if (!this.jsonFile$) return;
    this.busyJson = true;
    this.busy = true;
    this.error = "";
    this.svc.uploadLibraryDocument(this.jsonFile$, "re-cards", this.gearId).subscribe({
      next: (r) => {
        this.busyJson = false;
        this.busy = false;
        if (r) {
          this.jsonResult = r;
          this.notice = `Module "${r.module_key}" uploaded for Gear ID "${this.gearId}".`;
          this.loadStatus();
        } else {
          this.error = "Upload failed. Check that the JSON matches the RED format and try again.";
        }
      },
      error: () => {
        this.busyJson = false;
        this.busy = false;
        this.error = "Upload failed. Check the JSON format and try again.";
      },
    });
  }

  pickAnalysis(event: Event): void {
    this.analysis$ = (event.target as HTMLInputElement).files?.[0] ?? null;
    this.analysisFileName = this.analysis$?.name ?? "";
    this.analysisResult = null;
  }

  uploadAnalysis(): void {
    if (!this.analysis$) return;
    this.busyAnalysis = true;
    this.busy = true;
    this.error = "";
    this.svc.uploadLibraryDocument(this.analysis$, "red-analysis", this.gearId, { activate: this.makeCurrent }).subscribe({
      next: (r) => {
        this.busyAnalysis = false;
        this.busy = false;
        if (r) {
          this.analysisResult = r as LibraryGraphUploadResult;
          const v = this.analysisResult.version;
          this.notice = `Dependency graph v${v} built for Gear ID "${this.gearId}" from ${this.analysisFileName}` +
            (this.analysisResult.is_current ? " and made current." : "; the current version is unchanged.");
          this.graphReload++;
          this.loadStatus();
        } else {
          this.error = "Analysis upload failed. Check that the JSON is a RED per-file analysis export (result.files[]) and try again.";
        }
      },
      error: () => {
        this.busyAnalysis = false;
        this.busy = false;
        this.error = "Analysis upload failed.";
      },
    });
  }

  uploadScreens(): void {
    if (!this.screens$) return;
    this.busyScreens = true;
    this.busy = true;
    this.error = "";
    this.svc.uploadLibraryDocument(this.screens$, "screens", this.gearId).subscribe({
      next: (r) => {
        this.busyScreens = false;
        this.busy = false;
        if (r) {
          this.screensResult = r;
          this.notice = `Screens uploaded for Gear ID "${this.gearId}". ${r.images_bound ?? 0} screenshots bound.`;
          this.loadStatus();
        } else {
          this.error = "Screens upload failed.";
        }
      },
      error: () => {
        this.busyScreens = false;
        this.busy = false;
        this.error = "Screens upload failed.";
      },
    });
  }

  build(): void {
    this.busyBuild = true;
    this.buildNotice = "";
    this.error = "";
    this.svc.buildLibrary(this.gearId).subscribe({
      next: (r) => {
        this.busyBuild = false;
        if (r) {
          this.buildNotice = `Build started for Gear ID "${this.gearId}". Approve it in the pipeline view once it reaches 'waiting for approval'.`;
          this.loadStatus();
        } else {
          this.error = "Build failed to start. Ensure at least one module is uploaded and no build is already running.";
        }
      },
      error: () => {
        this.busyBuild = false;
        this.error = "Build failed to start.";
      },
    });
  }

  hasCorpus(): boolean {
    return (this.status?.corpus.modules.length ?? 0) > 0;
  }

  cardKinds(cards: Record<string, number>): string[] {
    return Object.keys(cards);
  }
}
