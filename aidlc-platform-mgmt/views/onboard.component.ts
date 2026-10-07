import { Component, OnInit } from "@angular/core";
import { Router } from "@angular/router";

import { AidlcPlatformMgmtService, AspSourceResult } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

/** Onboard a workspace — project title, description, and requirements source choice. */
@Component({
  selector: "app-apm-onboard",
  template: `
    <div class="apm-crumb">No project</div>
    <div class="apm-hdr">
      <div class="apm-hdr-icon">✦</div>
      <div>
        <h2>Onboard a Workspace</h2>
        <p class="sub">Opens a Global Workspace for this project. Spends no tokens and is safe to repeat.</p>
      </div>
    </div>

    <div class="apm-banner ok" *ngIf="notice">{{ notice }}</div>

    <div class="apm-card">
      <div class="apm-card-head"><h4>Project details</h4></div>
      <div class="apm-card-body">

        <div class="apm-field">
          <label>Project title</label>
          <input [(ngModel)]="name" placeholder="e.g. UW Credit Risk" />
          <p class="apm-field-hint">Used to identify the project and resolve its KB application.</p>
        </div>

        <div class="apm-field">
          <label>Project description</label>
          <textarea rows="4" [(ngModel)]="description"
                    placeholder="Brief description of the project scope and goals…"></textarea>
        </div>

        <div class="apm-field">
          <label>Gear ID</label>
          <select [(ngModel)]="gearId" style="padding:8px;border:1px solid var(--border);border-radius:8px;font:inherit;width:100%;">
            <option value="">— Default (from config) —</option>
            <option value="japan">japan</option>
            <option value="1429">1429 (UW credit)</option>
          </select>
          <p class="apm-field-hint">The application identifier for RE Graph context. Leave empty to use global config.</p>
        </div>

        <!-- Requirements source toggle -->
        <div class="apm-field" style="margin-bottom:6px;">
          <label>Requirements source</label>
          <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:6px;">
            <label class="apm-radio-card" [class.on]="intakeMode==='asp'" (click)="intakeMode='asp'">
              <input type="radio" name="intakeMode" value="asp" [checked]="intakeMode==='asp'"
                     style="width:16px;height:16px;margin:2px 0 0;accent-color:var(--aig-cobalt);" />
              <div>
                <b style="display:block;font-size:13px;">Upload ASP files (Library)</b>
                <em style="display:block;font-size:11px;color:var(--text-secondary);font-style:normal;margin-top:2px;line-height:1.4;">
                  Cards are selected from the approved Global Library by matching your ASP file names.
                </em>
              </div>
            </label>
            <label class="apm-radio-card" [class.on]="intakeMode==='re-graph'" (click)="intakeMode='re-graph'">
              <input type="radio" name="intakeMode" value="re-graph" [checked]="intakeMode==='re-graph'"
                     style="width:16px;height:16px;margin:2px 0 0;accent-color:var(--aig-cobalt);" />
              <div>
                <b style="display:block;font-size:13px;">Use RE Graph</b>
                <em style="display:block;font-size:11px;color:var(--text-secondary);font-style:normal;margin-top:2px;line-height:1.4;">
                  Rules are fetched live from the RE Graph API when the PRD stage runs. No document upload needed.
                </em>
              </div>
            </label>
          </div>
        </div>

        <!-- ASP file upload (shown only when intakeMode === 'asp') -->
        <div *ngIf="intakeMode==='asp'">
          <div class="apm-field">
            <label>ASP files <span style="font-weight:400;color:var(--text-secondary)">(required, multi-select)</span></label>
            <input #aspInput type="file" accept=".asp,.asa,.inc" multiple (change)="pickAsp($event)" hidden />
            <input #txtInput type="file" accept=".txt" (change)="pickAspFromText($event)" hidden />
            <div class="apm-file-pick">
              <button type="button" class="apm-btn outline" (click)="aspInput.click()">Choose ASP files…</button>
              <span class="sub">{{ aspLabel }}</span>
            </div>
            <!-- Text file fallback -->
            <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
              <span style="font-size:12px;color:var(--text-secondary);">or</span>
              <button type="button" class="apm-btn outline" style="font-size:12px;padding:4px 10px;"
                      (click)="txtInput.click()">Upload names from .txt…</button>
              <span *ngIf="txtLabel" style="font-size:12px;color:var(--text-secondary);">{{ txtLabel }}</span>
            </div>
            <p class="apm-field-hint">
              Upload your project's <code>.asp</code>, <code>.asa</code>, or <code>.inc</code> files.
              Only the file names are used to select matching cards from the approved Global Library.
              Ensure the library is built and approved before onboarding.
              <br/>
              <b>Fallback:</b> upload a <code>.txt</code> file listing one ASP filename per line — names are extracted and matched automatically.
            </p>
          </div>
          <div *ngIf="uploadProgress" class="apm-banner" style="background:#e6f3fb;border-color:#b3d9f0;color:#0369a1;margin-bottom:8px;">
            {{ uploadProgress }}
          </div>
          <!-- ASP upload result -->
          <div *ngIf="aspResult" style="padding:12px;background:var(--surface-2,#f5f5f5);border-radius:8px;font-size:13px;margin-bottom:12px;">
            <b style="display:block;margin-bottom:4px;">✔ {{ aspResult.matched.length }} files matched · {{ aspResult.cards_selected ?? 0 }} cards selected</b>
            <span *ngIf="aspResult.unmatched.length" style="color:#b45309;">
              {{ aspResult.unmatched.length }} unmatched: {{ aspResult.unmatched.join(', ') }}
            </span>
          </div>
        </div>

        <!-- RE Graph info banner (shown only when intakeMode === 're-graph') -->
        <div class="apm-banner" *ngIf="intakeMode==='re-graph'"
             style="background:#e6f3fb;border-color:#b3d9f0;color:#0369a1;margin-bottom:14px;">
          <b>RE Graph will be called at PRD run time.</b><br/>
          When you click <b>▶ Run agent</b> on the PRD stage, a dialog will ask for the
          extraction scope (Whole Application or Specific Module). The gear_id is resolved
          from the workspace config — no document upload needed here.
        </div>

        <div style="display:flex;gap:8px;align-items:center;margin-top:14px">
          <button class="apm-btn"
                  [disabled]="!name.trim() || busy || (intakeMode==='asp' && aspFiles.length === 0)"
                  (click)="submit()">
            {{ busy ? 'Onboarding…' : 'Onboard workspace' }}
          </button>
          <button class="apm-btn outline" (click)="cancel()">Cancel</button>
          <span class="sub" *ngIf="!name.trim()">a project title is required</span>
          <span class="sub" *ngIf="name.trim() && intakeMode==='asp' && aspFiles.length === 0">at least one ASP file is required</span>
        </div>

      </div>
    </div>
  `,
})
export class OnboardComponent implements OnInit {
  name = "";
  description = "";
  gearId = "";
  uploadProgress = "";
  busy = false;
  notice = "";
  intakeMode: 'asp' | 're-graph' = 'asp';

  aspFiles: File[] = [];
  aspLabel = "No files chosen";
  aspResult: AspSourceResult | null = null;
  txtLabel = "";

  constructor(
    private readonly svc: AidlcPlatformMgmtService,
    private readonly store: WorkspaceStore,
    private readonly router: Router,
  ) {}

  ngOnInit(): void {
    this.store.clearProject();
  }

  pickAsp(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.aspFiles = input.files ? Array.from(input.files) : [];
    const count = this.aspFiles.length;
    const plural = count > 1 ? 's' : '';
    this.aspLabel = count ? `${count} file${plural} selected` : "No files chosen";
    this.aspResult = null;
    this.txtLabel = "";
  }

  pickAspFromText(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    const ASP_EXTS = ['.asp', '.asa', '.inc'];
    file.text().then(text => {
      const names = text
        .split(/\r?\n/)
        .map(l => l.trim())
        .filter(l => l && ASP_EXTS.some(ext => l.toLowerCase().endsWith(ext)));
      this.aspFiles = names.map(name => new File([" "], name));
      this.aspLabel = this.aspFiles.length
        ? `${this.aspFiles.length} ASP name(s) extracted from text file`
        : "No valid ASP names found in file";
      this.txtLabel = file.name;
      this.aspResult = null;
    });
  }

  submit(): void {
    this.busy = true;
    this.aspResult = null;
    const app = this.name.trim();
    const intakeSrc = this.intakeMode === 're-graph' ? 're-graph' : 'reverse-engineering';

    this.svc.onboard({
      kb_application: app,
      category: 'brownfield',
      intake_source: intakeSrc,
      source_language: null,
      target_framework: 'angular-springboot',
      gear_id: this.gearId || undefined,
    }).subscribe(() => {
      if (this.intakeMode === 'asp' && this.aspFiles.length > 0) {
        this.uploadProgress = `Uploading ${this.aspFiles.length} ASP file(s)…`;
        this.svc.uploadAspSource(app, this.aspFiles, true).subscribe(result => {
          this.uploadProgress = '';
          this.aspResult = result;
          this.done();
        });
      } else {
        this.done();
      }
    });
  }

  private done(): void {
    this.busy = false;
    this.svc.listProjects().subscribe(projects => {
      this.store.projects.set(projects);
      this.router.navigate(['/aidlc-platform-management/dashboard']);
    });
  }

  cancel(): void {
    this.router.navigate(['/aidlc-platform-management/dashboard']);
  }
}
