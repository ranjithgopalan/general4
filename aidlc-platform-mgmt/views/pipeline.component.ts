import { Component, OnDestroy, effect, signal } from "@angular/core";
import { catchError, forkJoin, of } from "rxjs";

import { AidlcPlatformMgmtService, Artifact, ArtifactContent, Run, Stage, StagesResponse } from "../services/aidlc-platform-mgmt.service";
import { openChatWindow } from "../services/chat-window";
import { WorkspaceStore } from "../services/workspace.store";

type MappedItem = { id: string; title: string; desc: string; parentId?: string; points?: string; body?: string };

/** Pipeline board — used for BOTH the global and mini (EPIC) workspaces (same as forward-engineering). */
@Component({
  selector: "app-apm-pipeline",
  template: `
    <ng-container *ngIf="store.selectedId(); else pickWs">
      <div class="apm-crumb">{{ store.selectedProject()?.kb_application_id }} / {{ data()?.workspace?.label || 'Workspace' }}</div>
      <div class="apm-hdr">
        <div class="apm-hdr-icon">{{ tierIcon() }}</div>
        <div>
          <h2>{{ data()?.workspace?.label || 'Workspace' }}</h2>
          <p class="sub"><span class="apm-chip">{{ data()?.tier || 'global' }}</span>
            {{ sum('total') }} stages · {{ sum('ready') }} ready · {{ sum('gaps') }} gaps · {{ sum('waiting_for_approval') }} awaiting approval</p>
        </div>
      </div>

      <!-- action hint: stages awaiting the reviewer -->
      <div class="apm-banner warn" *ngIf="awaitingStages().length as n">
        <span>⚑ <b>{{ n }}</b> stage{{ n === 1 ? '' : 's' }} awaiting your approval — review the documents, then approve, request changes, or edit them.</span>
        <button class="apm-btn warn sm" (click)="reviewFirst()">Review {{ awaitingStages()[0].name }} →</button>
      </div>

      <!-- run error -->
      <div class="apm-banner bad" *ngIf="runError() as err" style="display:flex;justify-content:space-between;align-items:center">
        <span>✕ {{ err }}</span>
        <button class="apm-btn outline sm" (click)="runError.set(null)">Dismiss</button>
      </div>

      <!-- stepper -->
      <div class="apm-card"><div class="apm-card-body" style="overflow-x:auto"><div class="apm-stepper">
        <ng-container *ngFor="let s of stages(); let i = index">
          <span class="apm-conn" *ngIf="i > 0" [class.done]="isDone(stages()[i-1]) && isDone(s)"></span>
          <button class="apm-node" [class.active]="s.key === store.selectedStageKey()" (click)="scrollToStage(s.key)"
                  style="border:none;background:none;cursor:pointer">
            <span class="apm-ring" [ngClass]="tone(s)">{{ dot(s) }}</span>
            <span class="nm">{{ s.name }}</span>
            <span class="st">{{ label(s) }}</span>
            <span class="apm-node-agent" *ngIf="s.agent_group">{{ s.agent_group }}</span>
          </button>
        </ng-container>
      </div></div></div>

      <!-- per-stage cards -->
      <div class="apm-card" *ngFor="let s of stages()" [id]="'stage-' + s.key">
        <div class="apm-card-head">
          <h4>{{ dot(s) }} {{ s.name }}</h4>
          <span style="display:flex;gap:8px;align-items:center">
            <button class="apm-btn sm" [class.warn]="canRevise(s)" [class.outline]="!canRevise(s)" (click)="openSummary(s)">{{ canRevise(s) ? '⚑ Review & approve' : 'Summary' }}</button>
            <button class="apm-btn sm" *ngIf="showStart(s)" [class.green]="canStart(s)" [class.outline]="!canStart(s)"
                    [disabled]="!canStart(s)"
                    [title]="canStart(s) ? '' : startBlockedReason(s)"
                    (click)="start(s)">{{ hasRun(s) ? '↺ Re-run agent' : '▶ Run agent' }}</button>
          </span>
        </div>
        <div class="apm-card-body" [ngSwitch]="s.key">

          <!-- local-deployment stage: deployment panel -->
          <ng-container *ngSwitchCase="'local-deployment'">
            <div class="apm-guards"><span class="lbl">GUARDRAILS</span>
              <span class="apm-guard">✔ {{ s.approval_persona || 'owner' }} sign-off</span>
              <span class="apm-guard" *ngIf="s.artifact_tier">✔ {{ s.artifact_tier }} tier</span>
            </div>
            <div class="apm-timing" *ngIf="stageTiming(s) as t">
              <div class="apm-timing-left">
                <span class="lbl">TIMING</span>
                <span class="apm-timing-row" *ngIf="t.queuedAt !== '—'">
                  <span class="apm-timing-lbl">Queued</span><span>{{ t.queuedAt }}</span>
                </span>
                <span class="apm-timing-row" *ngIf="t.start !== '—'">
                  <span class="apm-timing-lbl">Start</span><span>{{ t.start }}</span>
                </span>
                <span class="apm-timing-row" *ngIf="t.end !== '—'">
                  <span class="apm-timing-lbl">Stop</span><span>{{ t.end }}</span>
                </span>
                <span class="apm-timing-row" *ngIf="t.elapsed">
                  <span class="apm-timing-lbl">Elapsed</span><b style="color:#f57c00">{{ t.elapsed }}</b>
                </span>
                <span class="apm-timing-row" *ngIf="t.duration">
                  <span class="apm-timing-lbl">Execution</span><b>{{ t.duration }}</b>
                </span>
              </div>
              <div class="apm-timing-right">
                <span class="apm-status-badge apm-status-queued"    *ngIf="t.status === 'queued'">◷ Queued</span>
                <span class="apm-status-badge apm-status-running"   *ngIf="t.status === 'running'"><span class="apm-running-dot"></span> Running</span>
                <span class="apm-status-badge apm-status-completed" *ngIf="t.status === 'completed'">✔ Completed</span>
                <span class="apm-status-badge apm-status-waiting"   *ngIf="t.status === 'waiting_for_approval'">⚑ Awaiting</span>
                <span class="apm-status-badge apm-status-failed"    *ngIf="t.status === 'failed'">✕ Failed</span>
              </div>
            </div>
            <div style="margin-top:12px">
              <span class="lbl" style="display:block;margin-bottom:8px">LOCAL DEPLOYMENT</span>
              <div style="display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap">
                <button class="apm-btn sm"
                        [class.outline]="deployEnv() !== 'local'"
                        [class.on]="deployEnv() === 'local'"
                        (click)="deployEnv.set('local')">
                  Local
                  <span class="apm-chip"
                        [ngClass]="localEnvStatus() === 'succeeded' ? 'ok' :
                                   localEnvStatus() === 'running'   ? 'run' :
                                   localEnvStatus() === 'failed'    ? 'err' : ''">
                    {{ localEnvStatus() | titlecase }}
                  </span>
                </button>
                <button class="apm-btn sm"
                        [class.outline]="deployEnv() !== 'qa'"
                        [class.on]="deployEnv() === 'qa'"
                        (click)="deployEnv.set('qa')">
                  QA (AWS)
                  <span class="apm-chip">{{ qaEnvStatus() | titlecase }}</span>
                </button>
                <button class="apm-btn sm outline" style="opacity:.5;cursor:not-allowed" disabled>
                  Production
                  <span class="apm-chip">Pending</span>
                </button>
              </div>
              <ng-container *ngIf="deployEnv() === 'local'">
                <div *ngFor="let step of deployStepsFor('local')"
                     style="display:flex;align-items:flex-start;gap:8px;padding:5px 0;
                            border-left:3px solid #e2e8f0;margin-left:6px;padding-left:12px">
                  <span style="font-size:14px;flex-shrink:0"
                        [style.color]="step.status==='done'    ? '#22c55e' :
                                       step.status==='failed'  ? '#ef4444' :
                                       step.status==='running' ? '#3b82f6' : '#c8d0dc'">
                    {{ step.status==='done' ? '✔' : step.status==='failed' ? '✕' :
                       step.status==='running' ? '◔' : '○' }}
                  </span>
                  <div style="display:flex;flex-direction:column;gap:2px">
                    <span style="font-size:13px;font-weight:600;color:#1e293b">{{ step.label }}</span>
                    <span *ngIf="step.detail" style="font-size:11px;color:#64748b">{{ step.detail }}</span>
                  </div>
                </div>
                <div *ngIf="!deployStepsFor('local').length && hasRun(s)"
                     style="padding:8px 4px;color:#64748b;font-size:13px">
                  Stage completed — see the deployment report artifact below for full details.
                </div>
                <ng-container *ngIf="localEnvStatus() === 'succeeded' && deployUrls().length">
                  <div class="apm-guards" style="margin-top:14px"><span class="lbl">VALIDATE</span></div>
                  <div class="apm-files">
                    <div class="apm-file" *ngFor="let u of deployUrls()">
                      <div class="apm-file-top">
                        <span class="apm-fi ok">{{ u.kind==='web' ? '🖥' : u.kind==='api' ? '📄' : '❤' }}</span>
                        <div>
                          <div class="apm-fn">{{ u.label }}</div>
                          <div class="apm-fm">
                            <a [href]="u.url" target="_blank" rel="noopener"
                               style="color:#576cbc;font-family:monospace;font-size:11.5px">{{ u.url }}</a>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </ng-container>
                <div class="apm-banner warn" *ngIf="!isDbWiringDone()">
                  Locked — awaiting DB Integration approval.
                </div>
              </ng-container>
              <ng-container *ngIf="deployEnv() === 'qa'">
                <div *ngFor="let step of deployStepsFor('qa')"
                     style="display:flex;align-items:flex-start;gap:8px;padding:5px 0;
                            border-left:3px solid #e2e8f0;margin-left:6px;padding-left:12px">
                  <span style="font-size:14px;flex-shrink:0"
                        [style.color]="step.status==='done'    ? '#22c55e' :
                                       step.status==='failed'  ? '#ef4444' :
                                       step.status==='running' ? '#3b82f6' : '#c8d0dc'">
                    {{ step.status==='done'?'✔':step.status==='failed'?'✕':step.status==='running'?'◔':'○' }}
                  </span>
                  <div style="display:flex;flex-direction:column;gap:2px">
                    <span style="font-size:13px;font-weight:600;color:#1e293b">{{ step.label }}</span>
                    <span *ngIf="step.detail" style="font-size:11px;color:#64748b">{{ step.detail }}</span>
                  </div>
                </div>
                <div class="apm-banner warn" *ngIf="localEnvStatus() !== 'succeeded'">
                  Requires local deployment to complete first.
                </div>
                <div class="apm-banner ok" *ngIf="qaEnvStatus() === 'succeeded'">
                  Deployed to QA (AWS) successfully.
                </div>
              </ng-container>
            </div>
            <div class="apm-files" *ngIf="artifactsFor(s).length" style="margin-top:4px">
              <div class="apm-file" *ngFor="let a of artifactsFor(s)">
                <div class="apm-file-top">
                  <span class="apm-fi" [class.ok]="a.status === 'APPROVED' || a.status === 'approved'">▤</span>
                  <div>
                    <div class="apm-fn">{{ a.artifact_type }}</div>
                    <div class="apm-fm">
                      <span class="apm-chip" [ngClass]="a.status === 'APPROVED' ? 'ok' : ''">{{ a.status }}</span>
                      v{{ a.version }} <span *ngIf="a.produced_by_persona">· {{ a.produced_by_persona }}</span>
                    </div>
                  </div>
                </div>
                <div class="apm-dls">
                  <ng-container *ngIf="a.storage_kind === 'git'; else deployDocDls">
                    <a class="apm-dl pri" [href]="svc.downloadArtifactUrl(a.id) + '?all=true'">⬇ code</a>
                  </ng-container>
                  <ng-template #deployDocDls>
                    <a class="apm-dl pri" [href]="svc.downloadArtifactUrl(a.id)">⬇ .md</a>
                    <button class="apm-dl" type="button" (click)="viewBeautifulHtml(a, a.version)">HTML</button>
                    <a class="apm-dl" [href]="svc.exportDocxUrl(a.id)">DOCX</a>
                    <a class="apm-dl" [href]="svc.downloadArtifactUrl(a.id) + '?all=true'">zip</a>
                  </ng-template>
                </div>
              </div>
            </div>
          </ng-container><!-- /local-deployment -->

          <!-- all other stages: standard body -->
          <ng-container *ngSwitchDefault>
            <div class="apm-guards"><span class="lbl">GUARDRAILS</span>
            <span class="apm-guard">✔ {{ s.approval_persona || 'owner' }} sign-off</span>
            <span class="apm-guard" *ngIf="s.artifact_tier">✔ {{ s.artifact_tier }} tier</span>
          </div>

          <!-- Library mode context — shown when the project uploaded ASP files -->
          <div *ngIf="s.key === 'kb' && aspSource() as asp"
               style="margin-bottom:14px;padding:12px 14px;background:#edf7f1;border:1px solid #52b778;border-radius:8px;font-size:13px;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
              <b style="color:#1a6b3a;">📚 KB Agent — Library Mode</b>
              <span style="font-size:11px;background:#1a6b3a;color:#fff;border-radius:10px;padding:1px 8px;font-weight:700;">
                Library v{{ asp.pinned_library_version || '—' }}
              </span>
              <span *ngIf="asp.library_stale"
                    style="font-size:11px;background:#b45309;color:#fff;border-radius:10px;padding:1px 8px;font-weight:700;">
                ⚠ Library updated — re-run G1 to pick up changes
              </span>
              <span *ngIf="asp.ready"
                    style="font-size:11px;background:#1a4a7a;color:#fff;border-radius:10px;padding:1px 8px;font-weight:700;">
                ✔ Ready
              </span>
            </div>
            <p class="sub" style="margin-bottom:8px;font-size:12px;">
              Selects cards and screenshots from the approved Global Library by matching ASP file names.
              Gear ID resolves the correct library. Downstream stages (PRD, FRD, Architecture…) run unchanged on the resulting project KB.
            </p>
            <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:8px;">
              <div style="background:#fff;border:1px solid #9ad3ab;border-radius:6px;padding:6px 10px;text-align:center;">
                <b style="display:block;font-size:16px;color:#1a6b3a;">{{ asp.matched?.length ?? 0 }}</b>
                <span style="font-size:11px;color:#5c6480;">matched</span>
              </div>
              <div style="background:#fff;border:1px solid #fcd34d;border-radius:6px;padding:6px 10px;text-align:center;">
                <b style="display:block;font-size:16px;color:#b45309;">{{ asp.unmatched?.length ?? 0 }}</b>
                <span style="font-size:11px;color:#5c6480;">unmatched</span>
              </div>
              <div style="background:#fff;border:1px solid #9aa3bd;border-radius:6px;padding:6px 10px;text-align:center;">
                <b style="display:block;font-size:16px;color:#5c6480;">{{ asp.files?.length ?? 0 }}</b>
                <span style="font-size:11px;color:#5c6480;">total files</span>
              </div>
            </div>
            <div *ngIf="asp.matched?.length" style="margin-bottom:4px;">
              <span class="lbl" style="font-size:10px;font-weight:700;letter-spacing:.06em;color:#1a6b3a;">MATCHED PAGES</span>
              <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">
                <span *ngFor="let f of asp.matched"
                      style="font-size:11px;background:#edf7f1;border:1px solid #52b778;border-radius:4px;padding:1px 7px;font-family:monospace;">
                  {{ f }}
                </span>
              </div>
            </div>
            <div *ngIf="asp.unmatched?.length" style="margin-top:6px;">
              <span class="lbl" style="font-size:10px;font-weight:700;letter-spacing:.06em;color:#b45309;">UNMATCHED (no cards in library)</span>
              <div style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px;">
                <span *ngFor="let f of asp.unmatched"
                      style="font-size:11px;background:#fff8e1;border:1px solid #fcd34d;border-radius:4px;padding:1px 7px;font-family:monospace;">
                  {{ f }}
                </span>
              </div>
            </div>
          </div>

          <!-- KB card extraction summary — visible once Claude writes kb.md with counts -->
          <div class="apm-kb-cards" *ngIf="s.key === 'kb' && kbCards().length">
            <span class="lbl">KNOWLEDGE CARDS EXTRACTED</span>
            <table class="apm-kb-table">
              <thead><tr><th>Type</th><th>Directory</th><th>Count</th><th></th></tr></thead>
              <tbody>
                <ng-container *ngFor="let row of kbCards()">
                  <tr class="apm-kb-row" (click)="toggleKbType(row.type)"
                      [class.apm-kb-row--open]="expandedKbType() === row.type">
                    <td>{{ row.type }}</td>
                    <td class="apm-kb-dir">{{ row.directory }}</td>
                    <td class="apm-kb-count">{{ row.count }}</td>
                    <td class="apm-kb-chevron">{{ expandedKbType() === row.type ? '▼' : '▶' }}</td>
                  </tr>
                  <tr *ngIf="expandedKbType() === row.type" class="apm-kb-expand-row">
                    <td colspan="4" class="apm-kb-expand-cell">
                      <div class="apm-kb-card-list">
                        <ng-container *ngFor="let card of (kbCardsByType()[row.type] || [])">
                          <div class="apm-kb-card-item"
                               [class.apm-kb-card-item--open]="expandedKbCardId() === card.id"
                               (click)="toggleKbCard(card.id)">
                            <span class="apm-fid">{{ card.id }}</span>
                            <span class="apm-ftitle">{{ card.label }}</span>
                            <span class="apm-kb-card-chevron" *ngIf="typedCardById(card.id)">
                              {{ expandedKbCardId() === card.id ? '▼' : '▶' }}
                            </span>
                          </div>
                          <ng-container *ngIf="expandedKbCardId() === card.id">
                            <ng-container *ngIf="typedCardById(card.id); let tc">
                              <div class="apm-kb-card-fields">
                                <div class="apm-kb-field-group" *ngIf="tc.key_fields.length > 0">
                                  <strong>Fields</strong>
                                  <span class="apm-kb-field-chip"
                                        *ngFor="let f of tc.key_fields">{{ f }}</span>
                                </div>
                                <div class="apm-kb-field-group" *ngIf="tc.buttons.length > 0">
                                  <strong>Buttons</strong>
                                  <span class="apm-kb-field-chip apm-kb-field-chip--btn"
                                        *ngFor="let b of tc.buttons">{{ b }}</span>
                                </div>
                                <div class="apm-kb-field-group" *ngIf="tc.roles_visible.length > 0">
                                  <strong>Roles</strong>
                                  <span class="apm-kb-field-chip apm-kb-field-chip--role"
                                        *ngFor="let r of tc.roles_visible">{{ r }}</span>
                                </div>
                              </div>
                            </ng-container>
                          </ng-container>
                        </ng-container>
                      </div>
                    </td>
                  </tr>
                </ng-container>
              </tbody>
            </table>
          </div>

          <!-- KB live graph stats (node / edge / chunk counts from DB) -->
          <div class="apm-kb-stats" *ngIf="s.key === 'kb' && store.kbNodeCount() > 0">
            <span class="apm-kb-stat"><strong>{{ store.kbNodeCount() }}</strong> nodes</span>
            <span class="apm-kb-stat"><strong>{{ store.kbEdgeCount() }}</strong> edges</span>
            <span class="apm-kb-stat"><strong>{{ store.kbChunkCount() }}</strong> chunks</span>
          </div>

          <!-- KB promote button — visible on kb stage once a version is known and not yet ACTIVE -->
          <div class="apm-kb-promote" *ngIf="s.key === 'kb'">
            <ng-container *ngIf="store.kbStatus() === 'ACTIVE'; else kbNotActive">
              <span class="apm-chip apm-chip--ok">KB Active</span>
              <span class="apm-kb-ver sub" *ngIf="store.kbVersion()">{{ store.kbVersion() }}</span>
            </ng-container>
            <ng-template #kbNotActive>
              <button class="apm-btn apm-btn-sm"
                      *ngIf="store.kbVersion()"
                      [disabled]="kbPromoting()"
                      (click)="promoteKb()"
                      title="Promote this KB version to ACTIVE so PRD can run">
                {{ kbPromoting() ? 'Promoting…' : 'Promote KB to Active' }}
              </button>
              <span class="sub" *ngIf="store.kbStatus() && store.kbStatus() !== 'ACTIVE'">
                Status: {{ store.kbStatus() }}
              </span>
              <span class="apm-kb-promote-err sub" *ngIf="kbPromoteError()">{{ kbPromoteError() }}</span>
            </ng-template>
          </div>

          <!-- Stage timing: queued-at / start / end / duration + status badge -->
          <div class="apm-timing" *ngIf="stageTiming(s) as t">
            <!-- left: timestamps + duration -->
            <div class="apm-timing-left">
              <span class="lbl">TIMING</span>
              <span class="apm-timing-row" *ngIf="t.queuedAt !== '—'">
                <span class="apm-timing-lbl">Queued</span>
                <span>{{ t.queuedAt }}</span>
              </span>
              <span class="apm-timing-row" *ngIf="t.start !== '—'">
                <span class="apm-timing-lbl">Start</span>
                <span>{{ t.start }}</span>
              </span>
              <span class="apm-timing-row" *ngIf="t.end !== '—'">
                <span class="apm-timing-lbl">Stop</span>
                <span>{{ t.end }}</span>
              </span>
              <span class="apm-timing-row" *ngIf="t.elapsed">
                <span class="apm-timing-lbl">Elapsed</span>
                <b style="color:#f57c00">{{ t.elapsed }}</b>
              </span>
              <span class="apm-timing-row" *ngIf="t.duration">
                <span class="apm-timing-lbl">Execution</span>
                <b>{{ t.duration }}</b>
              </span>
            </div>
            <!-- right: status badge -->
            <div class="apm-timing-right">
              <span class="apm-status-badge apm-status-queued"    *ngIf="t.status === 'queued'">
                ◷ Queued
              </span>
              <span class="apm-status-badge apm-status-running"   *ngIf="t.status === 'running'">
                <span class="apm-running-dot"></span> Running
              </span>
              <span class="apm-status-badge apm-status-completed" *ngIf="t.status === 'completed'">
                ✔ Completed
              </span>
              <span class="apm-status-badge apm-status-waiting"   *ngIf="t.status === 'waiting_for_approval'">
                ⚑ Awaiting
              </span>
              <span class="apm-status-badge apm-status-failed"    *ngIf="t.status === 'failed'">
                ✕ Failed
              </span>
            </div>
          </div>

          <div class="apm-files" *ngIf="artifactsFor(s).length">
            <div class="apm-file" *ngFor="let a of artifactsFor(s)">
              <div class="apm-file-top">
                <span class="apm-fi" [class.ok]="a.status === 'APPROVED' || a.status === 'approved'">▤</span>
                <div>
                  <div class="apm-fn">{{ a.artifact_type }}{{ a.is_dry_run ? ' (dry run)' : '' }}</div>
                  <div class="apm-fm">
                    <span class="apm-chip" [ngClass]="a.status === 'APPROVED' || a.status === 'approved' ? 'ok' : ''">{{ a.status }}</span>

                    <!-- Version selector dropdown - blue when locked, gray when unlocked -->
                    <select class="apm-version-select"
                            [(ngModel)]="selectedVersions()[a.id]"
                            (change)="selectVersion(a, selectedVersions()[a.id])"
                            (click)="loadVersionHistory(a)"
                            [style.backgroundColor]="a.id && this.selectedForForward()[a.id] ? '#e6f2ff' : '#f5f5f5'"
                            [style.borderColor]="a.id && this.selectedForForward()[a.id] ? '#0066cc' : '#999'"
                            style="padding:4px 6px;border:2px solid;border-radius:4px;font-size:12px;font-weight:bold;cursor:pointer;width:100px">
                      <option [value]="toNumber(a.current_version ?? a.version)" style="background:white">
                        v{{ a.current_version ?? a.version ?? 1 }} (current)
                      </option>
                      <option *ngFor="let v of (versionHistory()[a.id] || [])"
                              [value]="toNumber(v.version)"
                              style="background:white">
                        v{{ v.version }} {{ v.created_at ? ('- ' + (v.created_at | date:'short')) : '' }}
                      </option>
                    </select>

                    <!-- For Forward button - blue when locked, gray when unlocked -->
                    <button (click)="selectVersionForForward(a.id, toNumber(selectedVersions()[a.id] ?? a.current_version ?? a.version))"
                            [style.marginLeft]="'4px'"
                            [style.padding]="'4px 10px'"
                            [style.fontSize]="'12px'"
                            [style.fontWeight]="'600'"
                            [style.backgroundColor]="a.id && this.selectedForForward()[a.id] ? '#0066cc' : '#f5f5f5'"
                            [style.color]="a.id && this.selectedForForward()[a.id] ? 'white' : '#666'"
                            [style.border]="a.id && this.selectedForForward()[a.id] ? 'none' : '1px solid #999'"
                            [style.borderRadius]="'4px'"
                            [style.cursor]="'pointer'"
                            title="Lock/unlock this version for forward">
                      {{ a.id && this.selectedForForward()[a.id] ? '✓ For Forward' : 'For Forward' }}
                    </button>

                    <!-- Clear button (only show when frozen) -->
                    <button *ngIf="a.id && this.selectedForForward()[a.id]"
                            (click)="clearVersionSelection(a.id)"
                            [style.marginLeft]="'4px'"
                            [style.padding]="'4px 8px'"
                            [style.fontSize]="'11px'"
                            [style.backgroundColor]="'#f5f5f5'"
                            [style.color]="'#666'"
                            [style.border]="'1px solid #ccc'"
                            [style.borderRadius]="'4px'"
                            [style.cursor]="'pointer'"
                            title="Remove forward lock">
                      ✕ Clear
                    </button>

                    <span *ngIf="a.produced_by_persona">· {{ a.produced_by_persona }}</span>
                    <span *ngIf="a.storage_kind === 'git'">· <code>{{ a.git_repo }}&#64;{{ (a.git_commit || '').slice(0,8) }}</code>
                      <a *ngIf="a.pr_url" [href]="a.pr_url" target="_blank" rel="noopener">PR ↗</a></span>
                  </div>
                </div>
              </div>
              <div class="apm-dls">
                <ng-container *ngIf="a.storage_kind === 'git'; else docDls">
                  <a class="apm-dl pri" [href]="svc.downloadArtifactUrl(a.id) + '?version=' + (selectedVersions()[a.id] ?? a.version) + '&all=true'">⬇ code</a>
                  <a class="apm-dl" [href]="svc.devExportMdUrl(store.selectedId() || '')">⬇ .md</a>
                </ng-container>
                <ng-template #docDls>
                  <a class="apm-dl pri" [href]="svc.downloadArtifactUrl(a.id) + '?version=' + (selectedVersions()[a.id] ?? a.version)">⬇ .md</a>
                  <button class="apm-dl" type="button" (click)="viewBeautifulHtml(a, selectedVersions()[a.id] ?? a.version)">HTML</button>
                  <a class="apm-dl" [href]="svc.exportDocxUrl(a.id) + '?version=' + (selectedVersions()[a.id] ?? a.version)">DOCX</a>
                  <a class="apm-dl" [href]="svc.downloadArtifactUrl(a.id) + '?version=' + (selectedVersions()[a.id] ?? a.version) + '&all=true'">zip</a>
                </ng-template>
              </div>
              <!-- Feature stage: expandable cards (F-xxx only) -->
              <div class="apm-map" *ngIf="s.key === 'feature' && featureItemsFor(a).length">
                <div class="apm-map-head">{{ featureItemsFor(a).length }} Feature{{ featureItemsFor(a).length === 1 ? '' : 's' }}</div>
                <div class="apm-feat-card" *ngFor="let item of featureItemsFor(a)"
                     [class.apm-feat-card--open]="isItemExpanded(item.id)"
                     (click)="toggleExpandItem(item.id)">
                  <div class="apm-feat-card-row">
                    <span class="apm-fid">{{ item.id }}</span>
                    <span class="apm-ftitle apm-feat-card-title">{{ item.title }}</span>
                    <span class="apm-expand-icon">{{ isItemExpanded(item.id) ? '▲' : '▼' }}</span>
                  </div>
                  <div class="apm-feat-card-detail" *ngIf="isItemExpanded(item.id)">
                    <span class="apm-ws-id-row">{{ store.selectedId() }}-{{ item.id }}</span>
                    <pre class="apm-feat-body-text" *ngIf="item.body">{{ item.body }}</pre>
                  </div>
                </div>
              </div>
              <!-- User Story stage: expandable cards (US-xxx only) grouped by parent Feature -->
              <div class="apm-map" *ngIf="s.key === 'user-story' && storyItemsFor(a).length">
                <div class="apm-map-head">{{ storyItemsFor(a).length }} User Stor{{ storyItemsFor(a).length === 1 ? 'y' : 'ies' }}</div>
                <ng-container *ngFor="let group of userStoryGroupsFor(a)">
                  <div class="apm-map-flabel">
                    <span class="apm-fid sm">{{ group.featureId }}</span>
                    <span class="apm-map-flabel-id">{{ store.selectedId() }}-{{ group.featureId }}</span>
                  </div>
                  <div class="apm-story-card" *ngFor="let story of group.items"
                       [class.apm-story-card--open]="isItemExpanded(story.id)"
                       (click)="toggleExpandItem(story.id)">
                    <div class="apm-story-card-row">
                      <span class="apm-fid us">{{ story.id }}</span>
                      <span class="apm-ftitle apm-story-card-title">{{ story.title }}</span>
                      <span class="apm-pts" *ngIf="story.points">{{ story.points }} pts</span>
                      <span class="apm-expand-icon">{{ isItemExpanded(story.id) ? '▲' : '▼' }}</span>
                    </div>
                    <div class="apm-story-card-detail" *ngIf="isItemExpanded(story.id)">
                      <span class="apm-ws-id-row">{{ store.selectedId() }}-{{ group.featureId }}-{{ story.id }}</span>
                      <pre class="apm-feat-body-text" *ngIf="story.body">{{ story.body }}</pre>
                    </div>
                  </div>
                </ng-container>
              </div>
              <!-- EPIC Set artifact: one expandable card per EPIC-N (parsed from summary table) -->
              <div class="apm-map" *ngIf="a.artifact_type === 'epic' && epicItemsFor(a).length">
                <div class="apm-map-head">{{ epicItemsFor(a).length }} EPIC{{ epicItemsFor(a).length === 1 ? '' : 's' }}</div>
                <div class="apm-feat-card" *ngFor="let epic of epicItemsFor(a)"
                     [class.apm-feat-card--open]="isItemExpanded(epic.id)"
                     (click)="toggleExpandItem(epic.id)">
                  <div class="apm-feat-card-row">
                    <span class="apm-fid">{{ epic.id }}</span>
                    <span class="apm-ftitle apm-feat-card-title">{{ epic.title }}</span>
                    <span class="apm-expand-icon">{{ isItemExpanded(epic.id) ? '▲' : '▼' }}</span>
                  </div>
                  <div class="apm-feat-card-detail" *ngIf="isItemExpanded(epic.id)">
                    <p class="apm-fdesc" *ngIf="epic.desc">{{ epic.desc }}</p>
                  </div>
                </div>
              </div>
              <!-- PRD / FRD / other doc stages (not feature/user-story/epic): summary (first 3 sections) + expand -->
              <div class="apm-items" *ngIf="a.artifact_type !== 'epic' && itemsFor(a).length && (s.key !== 'feature' || !featureItemsFor(a).length) && (s.key !== 'user-story' || !storyItemsFor(a).length)">
                <div class="apm-items-head apm-items-head--flex">
                  <span>{{ itemsFor(a).length }} section{{ itemsFor(a).length === 1 ? '' : 's' }}</span>
                  <button class="apm-btn outline sm" *ngIf="itemsFor(a).length > 3"
                          (click)="toggleExpandItem('doc-' + a.id, $event)">
                    {{ isItemExpanded('doc-' + a.id) ? '▲ Show less' : '▼ View all' }}
                  </button>
                </div>
                <ng-container *ngFor="let item of itemsFor(a); let i = index">
                  <div class="apm-item" *ngIf="isItemExpanded('doc-' + a.id) || i < 3">
                    <span class="apm-item-title">{{ item.title }}</span>
                    <span class="apm-item-desc" *ngIf="item.desc">{{ item.desc }}</span>
                  </div>
                </ng-container>
                <div class="apm-item-more" *ngIf="!isItemExpanded('doc-' + a.id) && itemsFor(a).length > 3">
                  + {{ itemsFor(a).length - 3 }} more section{{ itemsFor(a).length - 3 === 1 ? '' : 's' }}…
                </div>
              </div>
            </div>
          </div>
          </ng-container><!-- /ngSwitchDefault -->
        </div><!-- /apm-card-body [ngSwitch] -->
      </div>
    </ng-container>
    <ng-template #pickWs><div class="apm-card"><div class="apm-card-body sub">Pick a project on the Dashboard to open its pipeline.</div></div></ng-template>

    <!-- PRD run dialog — shown before the agent starts; user picks scope + module -->
    <ng-container *ngIf="prdDialog() as ps">
      <div class="apm-modal-bg" (click)="closePrdDialog()"></div>
      <aside class="apm-drawer">
        <div class="apm-drawer-head">
          <div>
            <h3>Start PRD Agent<span *ngIf="data()?.workspace?.gear_id" style="font-weight:400;color:var(--text-secondary);"> — UW Credit Risk {{ data()?.workspace?.gear_id }}</span></h3>
          </div>
          <button class="apm-x" (click)="closePrdDialog()">×</button>
        </div>
        <div class="apm-drawer-body">
          <!-- SCOPE -->
          <div class="apm-field" style="margin-bottom:14px;">
            <label style="font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--text-secondary);">Scope</label>
            <div style="display:flex;gap:18px;margin-top:6px;">
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;">
                <input type="radio" name="prdScope" value="whole" [checked]="prdScopeType==='whole'" (change)="prdScopeType='whole'" style="accent-color:var(--aig-cobalt);" />
                All modules
              </label>
              <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;">
                <input type="radio" name="prdScope" value="specific" [checked]="prdScopeType==='specific'" (change)="prdScopeType='specific'" style="accent-color:var(--aig-cobalt);" />
                Specific module
              </label>
            </div>
          </div>
          <!-- MODULE dropdown -->
          <div class="apm-field" *ngIf="prdScopeType==='specific'" style="margin-bottom:14px;">
            <label style="font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--text-secondary);">
              Module
              <span style="font-size:9px;font-weight:600;background:#e3f2fd;color:#1565c0;border:1px solid #90caf9;border-radius:10px;padding:1px 7px;margin-left:6px;letter-spacing:.04em;text-transform:none;">populated from /re/graph/modules</span>
            </label>
            <span *ngIf="modulesLoading" style="font-size:11.5px;color:var(--text-secondary);display:block;margin-top:6px;">Loading modules…</span>
            <select *ngIf="!modulesLoading && moduleList.length > 0" [(ngModel)]="prdModuleName"
                    style="margin-top:6px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font:inherit;width:100%;font-size:13px;">
              <option value="">— Select a module —</option>
              <option *ngFor="let m of moduleList" [value]="m.name">{{ m.name }}</option>
            </select>
            <input *ngIf="!modulesLoading && moduleList.length === 0" type="text" [(ngModel)]="prdModuleName"
                   placeholder="e.g. Company Search, admin…"
                   style="margin-top:6px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font:inherit;width:100%;font-size:13px;" />
          </div>
          <!-- BUSINESS AREA (new) -->
          <div class="apm-field" style="margin-bottom:14px;border:1.5px dashed var(--aig-cobalt);border-radius:8px;padding:10px 12px;">
            <label style="font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--aig-cobalt);">
              Business Area
              <span style="font-size:9px;font-weight:600;background:#e3f2fd;color:#1565c0;border:1px solid #90caf9;border-radius:10px;padding:1px 7px;margin-left:6px;letter-spacing:.04em;text-transform:none;">NEW FIELD</span>
            </label>
            <input type="text" [(ngModel)]="prdBusinessArea"
                   placeholder="e.g. UW Credit Risk System Maintenance"
                   style="margin-top:6px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font:inherit;width:100%;font-size:13px;" />
          </div>
          <!-- ROLE (new) -->
          <div class="apm-field" style="margin-bottom:14px;border:1.5px dashed var(--aig-cobalt);border-radius:8px;padding:10px 12px;">
            <label style="font-size:10px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--aig-cobalt);">
              Role
              <span style="font-size:9px;font-weight:600;background:#e3f2fd;color:#1565c0;border:1px solid #90caf9;border-radius:10px;padding:1px 7px;margin-left:6px;letter-spacing:.04em;text-transform:none;">NEW FIELD</span>
            </label>
            <input type="text" [(ngModel)]="prdRole"
                   placeholder="e.g. admin"
                   style="margin-top:6px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;font:inherit;width:100%;font-size:13px;" />
          </div>
          <!-- Summary -->
          <div *ngIf="prdBusinessArea.trim() || prdModuleName.trim()"
               style="background:#f0f7ff;border:1px solid #90caf9;border-radius:8px;padding:10px 14px;font-size:12px;color:#1a237e;line-height:1.6;">
            The PRD agent will extract Business Rules, Services, Entities and Screens for<br/>
            <b>"{{ prdBusinessArea.trim() || '(business area)' }} — {{ prdScopeType==='specific' ? (prdModuleName.trim() || '(module)') : 'All Modules' }} — {{ prdRole.trim() || '(role)' }}"</b><br/>
            from gear {{ data()?.workspace?.gear_id || '—' }} using semantic graph search + Chat API grounding.
          </div>
        </div>
        <div class="apm-drawer-foot" style="padding:14px 20px;border-top:1px solid var(--border);display:flex;gap:10px;background:var(--panel-bg);">
          <button class="apm-btn outline" (click)="closePrdDialog()">Cancel</button>
          <button class="apm-btn green" style="flex:1;"
                  [disabled]="prdScopeType==='specific' && !prdModuleName.trim()"
                  (click)="startPrd(ps)">Start PRD Agent</button>
        </div>
      </aside>
    </ng-container>

    <!-- summary drawer -->
    <ng-container *ngIf="drawer() as s">
      <div class="apm-modal-bg" (click)="closeDrawer()"></div>
      <aside class="apm-drawer">
        <div class="apm-drawer-head"><h3>{{ s.name }}</h3><button class="apm-x" (click)="closeDrawer()">×</button></div>
        <div class="apm-drawer-body">
          <span class="apm-chip" [ngClass]="tone(s)">{{ label(s) }}</span>
          <h5>What this stage produced</h5>
          <p class="sub">{{ s.deliverable || 'Deliverable for the ' + s.name + ' stage.' }}</p>
          <h5>Run metrics</h5>
          <div class="apm-metrics">
            <div class="apm-metric"><b>{{ runFor(s)?.output_tokens || '–' }}</b><span>output tokens</span></div>
            <div class="apm-metric"><b>{{ runFor(s)?.num_turns ?? '–' }}/{{ runFor(s)?.max_turns ?? '–' }}</b><span>turns / cap</span></div>
            <div class="apm-metric"><b>{{ runFor(s)?.cost_usd != null ? ('$' + runFor(s)?.cost_usd) : 'n/r' }}</b><span>cost</span></div>
          </div>
          <h5>Artifacts</h5>
          <p class="sub" style="margin:-2px 0 8px" *ngIf="canRevise(s)">Edit a document inline or upload a corrected file (Word/.docx supported); saving creates a new version, then Approve carries it to the next stage.</p>
          <div class="apm-files">
            <div class="apm-file" *ngFor="let a of artifactsFor(s)">
              <div class="apm-file-top"><span class="apm-fi" [class.ok]="a.status === 'APPROVED'">▤</span>
                <div><div class="apm-fn">{{ a.artifact_type }}</div><div class="apm-fm">{{ a.status }} · v{{ a.version }}
                  <span *ngIf="a.created_by">· edited by {{ a.created_by }}</span></div></div></div>
              <div class="apm-dls">
                <a class="apm-dl pri" [href]="svc.downloadArtifactUrl(a.id)">⬇ .md</a>
                <a class="apm-dl" [href]="svc.exportHtmlUrl(a.id)" target="_blank" rel="noopener">HTML</a>
                <a class="apm-dl" [href]="svc.exportDocxUrl(a.id)">DOCX</a>
                <button class="apm-dl" type="button" *ngIf="canRevise(s) && editingId !== a.id" (click)="startEdit(a)">✎ Edit</button>
                <button class="apm-dl" type="button" *ngIf="canRevise(s)" (click)="rev.click()">⬆ Upload</button>
                <input #rev type="file" accept=".md,.txt,.html,.docx,.pdf" hidden (change)="pickRevision(a, $event)" />
              </div>
              <p class="apm-note sub" *ngIf="a.approval_feedback">📝 {{ a.approval_feedback }}</p>
              <div class="apm-editor" *ngIf="editingId === a.id">
                <textarea class="apm-ta" [(ngModel)]="editContent" placeholder="Edit the document (Markdown)…"></textarea>
                <input class="apm-ta" style="min-height:auto;margin-top:8px" [(ngModel)]="editComment" placeholder="Note for this document (optional)…" />
                <div style="display:flex;gap:8px;margin-top:8px">
                  <button class="apm-btn green sm" [disabled]="savingEdit || !editContent.trim()" (click)="saveEdit(a)">{{ savingEdit ? 'Saving…' : '💾 Save revision' }}</button>
                  <button class="apm-btn outline sm" (click)="cancelEdit()">Cancel</button>
                </div>
              </div>
            </div>
            <p class="sub" *ngIf="!artifactsFor(s).length">Nothing produced yet.</p>
          </div>
          <h5>{{ canDecide(s) ? 'Decision' : 'Actions' }}</h5>
          <!-- Draft-only wait: run belongs to a different workspace but artifacts are
               DRAFT here — the approve button will auto-fall-back to per-artifact approval. -->
          <div *ngIf="draftOnlyWait(s)" class="apm-banner warn" style="margin:0 0 10px;font-size:13px">
            ⚠ Documents are drafted but the pipeline run was registered under a different workspace (backend gap).
            Click <b>Approve</b> below — it will approve each document directly without needing a re-run.
          </div>
          <ng-container *ngIf="canDecide(s)">
            <p class="sub" *ngIf="isFailed(s) && s.run_id" style="margin:0 0 8px;color:#e65100">
              ⚠ This stage failed. You can still approve or reject the run if the output is acceptable.
            </p>
            <input class="apm-ta" style="min-height:auto;margin-bottom:8px" [(ngModel)]="approver" placeholder="you@aig.com" />
            <textarea class="apm-ta" [(ngModel)]="comment" placeholder="Add a comment (required to request changes)…"></textarea>
          </ng-container>
          <p class="sub" *ngIf="isFailed(s) && s.reason" style="margin:0 0 8px;color:#c62828">✕ {{ s.reason }}</p>
          <p class="sub" *ngIf="!canDecide(s) && !isFailed(s) && !draftOnlyWait(s)" style="margin:0 0 8px">
            {{ hasRun(s) ? 'This stage has run. Re-run to produce a new version.' : (canStart(s) ? 'Ready to run.' : startBlockedReason(s)) }}
          </p>
          <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
            <button class="apm-btn green" *ngIf="canDecide(s)" (click)="decide(s, 'approve')">✔ Approve</button>
            <button class="apm-btn danger" *ngIf="canDecide(s)" (click)="decide(s, 'reject')">✎ Request changes</button>
            <button class="apm-btn" *ngIf="showStart(s)" [class.green]="canStart(s) || draftOnlyWait(s)" [class.outline]="!canStart(s) && !draftOnlyWait(s)"
                    [disabled]="!canStart(s) && !draftOnlyWait(s)"
                    [title]="draftOnlyWait(s) ? 'Re-run to register a pipeline record, then approve' : (canStart(s) ? '' : startBlockedReason(s))"
                    (click)="start(s)">{{ hasRun(s) ? '↺ Re-run agent' : '▶ Run agent' }}</button>
            <button class="apm-btn outline" *ngIf="canDecide(s) && !isFailed(s)" (click)="start(s)">{{ hasRun(s) ? '↺ Re-run agent' : '▶ Run agent' }}</button>
          </div>
        </div>
      </aside>
    </ng-container>
  `,
})
export class PipelineComponent implements OnDestroy {
  readonly data = signal<StagesResponse | null>(null);
  readonly runs = signal<Run[]>([]);
  readonly artifacts = signal<Artifact[]>([]);
  /** Runs/artifacts from the global workspace — needed for per-EPIC stages
   *  (feature/user-story/coverage) whose runs are stored in the global workspace,
   *  not in the mini workspace that is currently selected. */
  readonly globalRuns = signal<Run[]>([]);
  readonly globalArtifacts = signal<Artifact[]>([]);
  /** Per-EPIC runs fetched via kb_application_id filter (workspace_id query is broken
   *  in the backend, so we fetch all-app runs and filter client-side).
   *  Populated only when viewing a mini workspace that has PER_EPIC stages. */
  readonly perEpicRuns = signal<Run[]>([]);
  readonly drawer = signal<Stage | null>(null);
  readonly prdDialog = signal<Stage | null>(null);
  prdScopeType: 'specific' | 'whole' = 'specific';
  prdModuleName = '';
  prdBusinessArea = '';
  prdRole = '';
  moduleList: { name: string; nodeCount?: number }[] = [];
  modulesLoading = false;
  readonly runError = signal<string | null>(null);
  /** artifact.id → parsed heading items; undefined = not yet fetched */
  private readonly itemCache = signal<Record<string, MappedItem[]>>({});
  approver = "";
  comment = "";
  /** Interval handle for the 10-second auto-poll. */
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  /** Set of run_ids already fetched individually — prevents duplicate requests. */
  private readonly fetchedRunIds = new Set<string>();
  editingId: string | null = null;
  editContent = "";
  editComment = "";
  savingEdit = false;
  /** Tracks expanded state for Feature/US/document section cards. */
  // Use a signal so Angular re-renders whenever an item is expanded/collapsed.
  private readonly expandedItemIds = signal(new Set<string>());

  readonly kbCards = signal<{type: string; directory: string; count: number}[]>([]);
  readonly kbCardsByType = signal<Record<string, {id: string; label: string}[]>>({});
  readonly expandedKbType    = signal<string | null>(null);
  readonly kbTypedCards      = signal<{id: string; kind: string; label: string; key_fields: string[]; buttons: string[]; roles_visible: string[]}[]>([]);
  readonly expandedKbCardId  = signal<string | null>(null);
  readonly kbPromoting   = signal(false);
  readonly kbPromoteError = signal<string | null>(null);

  /** ASP source status for library-mode projects (loaded when kb stage is visible). */
  readonly aspSource = signal<import('../services/aidlc-platform-mgmt.service').AspSourceStatus | null>(null);

  promoteKb(): void {
    const ver = this.store.kbVersion();
    if (!ver || this.kbPromoting()) return;
    this.kbPromoting.set(true);
    this.kbPromoteError.set(null);
    this.svc.promoteKb(ver).subscribe({
      next: () => {
        this.store.kbStatus.set('ACTIVE');
        this.kbPromoting.set(false);
      },
      error: () => {
        this.kbPromoteError.set('Promote failed — check the RE console for blocking gaps.');
        this.kbPromoting.set(false);
      },
    });
  }

  parseKbCards(md: string): {type: string; directory: string; count: number}[] {
    const rows: {type: string; directory: string; count: number}[] = [];
    for (const line of md.split('\n')) {
      // Match any pipe table row with 3+ cells where last numeric cell is the count.
      // Directory column may be kb/knowledge/br/, rules/, screens/, etc.
      const m = line.match(/\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|/);
      if (!m) continue;
      const type = m[1].trim();
      if (!type || type === 'Type' || /^-+$/.test(type)) continue;
      rows.push({ type, directory: m[2].trim(), count: parseInt(m[3], 10) });
    }
    return rows;
  }

  parseKbCardIndex(md: string): Record<string, {id: string; label: string}[]> {
    const m = md.match(/<!--\s*KB_CARDS_JSON:(\{.*?\})\s*-->/s);
    if (!m) return {};
    try { return JSON.parse(m[1]); } catch { return {}; }
  }

  parseKbTypedCards(md: string): {id: string; kind: string; label: string; key_fields: string[]; buttons: string[]; roles_visible: string[]}[] {
    const m = md.match(/<!--\s*KB_CARDS_JSON:(\{.*?\})\s*-->/s);
    if (!m) return [];
    try {
      const raw: Record<string, any[]> = JSON.parse(m[1]);
      return Object.entries(raw).flatMap(([kind, cards]) =>
        (cards || []).map(c => ({
          id:            c.id ?? '',
          kind,
          label:         c.label ?? '',
          key_fields:    Array.isArray(c.key_fields) ? c.key_fields : [],
          buttons:       Array.isArray(c.buttons) ? c.buttons : [],
          roles_visible: Array.isArray(c.roles_visible) ? c.roles_visible : [],
        }))
      );
    } catch { return []; }
  }

  toggleKbType(type: string): void {
    this.expandedKbCardId.set(null);
    this.expandedKbType.update(cur => cur === type ? null : type);
  }

  toggleKbCard(id: string): void {
    this.expandedKbCardId.update(cur => cur === id ? null : id);
  }

  typedCardById(id: string) {
    return this.kbTypedCards().find(c => c.id === id) ?? null;
  }

  readonly deployEnv = signal<'local' | 'qa' | 'prod'>('local');
  readonly deploySteps = signal<{env: string; key: string; label: string; status: string; detail: string}[]>([]);
  readonly deployUrls = signal<{label: string; url: string; kind: string}[]>([]);

  /** Selected version per artifact ID for viewing version history. */
  readonly selectedVersions = signal<Record<string, number>>({});
  /** Version history per artifact ID. */
  readonly versionHistory = signal<Record<string, {version: number; created_at?: string; status?: string}[]>>({});
  /** User-selected versions for merge/next flow - persisted preference. */
  readonly selectedForForward = signal<Record<string, number>>({});
  /** Merge readiness: which artifact versions will be used for merge. */
  readonly mergeReadiness = signal<{
    ready: boolean;
    message: string;
    selectedVersions?: Record<string, number>;
  }>({ ready: false, message: 'Checking merge readiness...' });

  private parseDeployLog(run: Run | undefined): void {
    if (!run?.log?.length) { this.deploySteps.set([]); this.deployUrls.set([]); return; }
    const STEP_LABELS: Record<string, string> = {
      validate: 'Validate build artifacts',
      security: 'Security validation',
      backend:  'Launch application backend (Spring Boot)',
      health:   'Health-check the backend',
      frontend: 'Build & serve frontend (esbuild)',
      ready:    'Publish the running application URL',
    };
    const steps: {env: string; key: string; label: string; status: string; detail: string}[] = [];
    const urls:  {label: string; url: string; kind: string}[] = [];
    for (const line of run.log) {
      const stepM = line.match(/^\[(LOCAL|QA|PROD)\]\s+(\w+)\s+(done|running|failed|queued)\s*(.*)/i);
      if (stepM) {
        const [, envRaw, key, status, detail] = stepM;
        const env = envRaw.toLowerCase() === 'local' ? 'local' : envRaw.toLowerCase() === 'qa' ? 'qa' : 'prod';
        steps.push({ env, key, label: STEP_LABELS[key] ?? key, status, detail: detail.trim() });
        continue;
      }
      const urlM = line.match(/^\[LOCAL_URL\]\s+(.+?)\s+(https?:\/\/\S+)\s+(\w+)/i);
      if (urlM) urls.push({ label: urlM[1].trim(), url: urlM[2], kind: urlM[3] });
    }
    this.deploySteps.set(steps);
    this.deployUrls.set(urls);
  }

  deployStepsFor(env: 'local' | 'qa' | 'prod') {
    return this.deploySteps().filter(s => s.env === env);
  }

  localEnvStatus(): 'pending' | 'running' | 'succeeded' | 'failed' {
    const s = this.deployStepsFor('local');
    if (!s.length) return 'pending';
    if (s.some(x => x.status === 'failed'))  return 'failed';
    if (s.some(x => x.status === 'running')) return 'running';
    if (s.every(x => x.status === 'done'))   return 'succeeded';
    return 'running';
  }

  qaEnvStatus(): 'pending' | 'running' | 'succeeded' | 'failed' {
    const s = this.deployStepsFor('qa');
    if (!s.length) return 'pending';
    if (s.some(x => x.status === 'failed'))  return 'failed';
    if (s.some(x => x.status === 'running')) return 'running';
    if (s.every(x => x.status === 'done'))   return 'succeeded';
    return 'running';
  }

  isDbWiringDone(): boolean {
    return this.stages().some(s => s.key === 'db-wiring' && this.isDone(s));
  }

  toggleExpandItem(id: string, event?: Event): void {
    event?.stopPropagation();
    this.expandedItemIds.update(prev => {
      const next = new Set(prev);
      if (next.has(id)) { next.delete(id); } else { next.add(id); }
      return next;
    });
  }

  isItemExpanded(id: string): boolean {
    return this.expandedItemIds().has(id);
  }

  constructor(
    readonly store: WorkspaceStore,
    readonly svc: AidlcPlatformMgmtService,
  ) {
    // Reload whenever the selected workspace changes; polling only starts when a
    // stage is actually in-flight — either detected after load or triggered by a
    // user action (orchestrate / start / rerun).
    effect(() => {
      const id = this.store.selectedId();
      if (id) {
        this.load(id);
      }
    });

    // Scroll to the selected stage card whenever selectedStageKey changes —
    // covers both stepper clicks (scrollToStage) and left-panel sidebar clicks
    // (selectGlobalStage / selectStage / selectMiniEpicStage) which only call
    // store.selectStage() without doing any scrolling themselves.
    // The 200 ms delay lets Angular finish rendering after a route transition.
    effect(() => {
      const key = this.store.selectedStageKey();
      if (key) {
        setTimeout(() => {
          const el = document.getElementById('stage-' + key);
          if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 200);
      }
    });

    // Auto-load version history for all artifacts when they are loaded
    effect(() => {
      const arts = this.artifacts();
      if (arts && arts.length > 0) {
        arts.forEach(a => this.loadVersionHistory(a));
      }
    });
  }

  /** Stage keys that run at global scope but are displayed per-EPIC. */
  private readonly PER_EPIC_KEYS = new Set(['feature', 'user-story', 'coverage']);

  stages(): Stage[] {
    const base = this.store.stages();
    const tier = this.data()?.tier;
    if (tier === 'global') {
      // Hide per-EPIC stages from the global workspace board —
      // they live under each EPIC workspace, not at programme level.
      return base.filter(s => !this.PER_EPIC_KEYS.has(s.key));
    }
    if (tier === 'mini') {
      // The assembler workspace is also tier=mini but must NOT show per-EPIC
      // global stages (Feature/User Story/Coverage) — those belong to per-EPIC
      // mini workspaces only.
      if (this.data()?.pipeline === 'uw-cr-epic-assembler') {
        const stages = [...base];
        // Inject db-wiring after epic-assemble when missing (pre-existing workspace).
        if (!stages.some(s => s.key === 'db-wiring')) {
          const dbStage: Stage = { key: 'db-wiring', name: 'DB Integration', seq: 2, state: 'idle', agent_group: 'Developer Agent' };
          const idx = stages.findIndex(s => s.key === 'epic-assemble');
          stages.splice(idx >= 0 ? idx + 1 : stages.length, 0, dbStage);
        }
        // Inject local-deployment after db-wiring when missing (pre-existing workspace).
        if (!stages.some(s => s.key === 'local-deployment')) {
          const deployStage: Stage = { key: 'local-deployment', name: 'Local Deployment', seq: 3, state: 'idle', agent_group: 'Developer Agent' };
          const idx = stages.findIndex(s => s.key === 'db-wiring');
          stages.splice(idx >= 0 ? idx + 1 : stages.length, 0, deployStage);
        }
        return stages;
      }
      // Prepend the per-EPIC stages from the global pipeline so they appear at
      // the top of this EPIC's board. Runs/artifacts are fetched from the global
      // workspace and surfaced via globalRuns/globalArtifacts signals.
      const globalPerEpic = this.store.globalStages()
        .filter(s => this.PER_EPIC_KEYS.has(s.key));
      // Filter db-integration from per-EPIC boards — it now runs in the EPIC Assembler.
      const filteredBase = base.filter(s => s.key !== 'db-integration');
      return [...globalPerEpic, ...filteredBase];
    }
    return base;
  }

  isMini(): boolean {
    return this.data()?.tier === "mini";
  }
  isArchitecture(): boolean {
    return this.data()?.tier === "architecture";
  }
  tierIcon(): string {
    if (this.isMini()) return "◱";
    if (this.isArchitecture()) return "△";
    return "◧";
  }
  /** How this tier executes: Global on LangGraph/Bedrock; Architecture & Mini on Claude Code CLI. */
  runMode(): string {
    return this.data()?.tier === "global" ? "LangGraph · Bedrock" : "Claude Code CLI";
  }
  sum(k: string): number {
    return this.data()?.summary?.[k] ?? 0;
  }

  private load(id: string): void {
    this.svc.workspaceStages(id).subscribe((d) => {
      this.data.set(d);
      // Keep store.stages() in sync with the pipeline board's own fetch.
      // This guards against the race condition where select(global) response
      // arrives after select(epic2) and overwrites the mini workspace stages.
      if (d?.stages && id === this.store.selectedId()) {
        this.store.stages.set(d.stages);
      }
      if (d?.tier === 'mini') {
        this.loadGlobalForMini();
        // Per-EPIC runs (feature/user-story/coverage) are stored under the mini
        // workspace_id but the backend's workspace_id filter is broken so they
        // won't appear in runs(id).  Use kb_application_id + client-side filter.
        this.loadPerEpicRunsForMini(id);
      }
      // After stages are known, backfill any runs referenced by run_id that
      // the workspace-scoped query misses (cross-workspace run registration).
      this.fetchMissingStageRuns();
    });
    this.svc.runs(id).subscribe((r) => {
      this.runs.set(r.items ?? []);
      // Re-check for missing runs now that the run list is fresh.
      this.fetchMissingStageRuns();
    });
    this.svc.artifacts(id).subscribe((r) => {
      this.artifacts.set(r.items ?? []);
      // auto-load parsed items for any already-completed stages
      for (const s of this.stages()) { this.loadItemsFor(s); }
    });
    // Load ASP source status for library-mode projects (intake_source=reverse-engineering).
    const appId = this.store.selectedProject()?.kb_application_id;
    if (appId) {
      this.svc.getAspSource(appId).subscribe(a => this.aspSource.set(a));
    }
    // Continuous 10-second auto-poll so the board always reflects live state.
    this.startAutoPoll();
  }

  /** Start (or restart) the 10-second continuous auto-poll. */
  private startAutoPoll(): void {
    if (this.pollTimer) clearInterval(this.pollTimer);
    this.pollTimer = setInterval(() => this.reloadCurrent(), 60_000);
  }

  /** Fetch individual runs for stages whose run_id is not in the workspace run
   *  list. This fixes the case where a run was written under a different
   *  workspace_id (cross-workspace registration bug) so the timing and status
   *  always appear on every stage card.
   *
   *  Sources checked — whichever is available first wins:
   *    • this.stages()        — what the template actually iterates (store + global)
   *    • this.data()?.stages  — component's own workspaceStages fetch
   *    • this.store.globalStages() — global per-EPIC stages for mini boards */
  private fetchMissingStageRuns(): void {
    const existingIds = new Set(this.runs().map(r => r.run_id));
    const allStages = [
      ...this.stages(),                  // what the template sees (includes globalPerEpic for mini)
      ...(this.data()?.stages ?? []),    // component's own fetch (may differ timing-wise)
      ...this.store.globalStages(),      // always include global stages
    ];
    const missingIds = [...new Set(
      allStages
        .map(s => s.run_id)
        .filter((rid): rid is string => !!rid && !existingIds.has(rid) && !this.fetchedRunIds.has(rid))
    )];
    for (const runId of missingIds) {
      this.fetchedRunIds.add(runId); // mark before request to prevent duplicate calls
      this.svc.getRun(runId).subscribe(resp => {
        const run = (resp as any)?.run as Run | undefined;
        if (run) {
          this.runs.update(list =>
            list.some(r => r.run_id === run.run_id) ? list : [...list, run]
          );
        }
      });
    }
  }

  /** Fetch runs + artifacts from the global workspace so per-EPIC stage cards
   *  (feature/user-story/coverage) can display their results inside the mini board. */
  private loadGlobalForMini(): void {
    const gId = this.store.globalWorkspace()?.id;
    if (!gId) return;
    this.svc.runs(gId).subscribe((r) => this.globalRuns.set(r.items ?? []));
    this.svc.artifacts(gId).subscribe((r) => {
      this.globalArtifacts.set(r.items ?? []);
      // Global artifacts arrive after mini artifacts, so loadItemsFor was called
      // before they were available.  Re-trigger it now for per-EPIC stages so the
      // Feature / User Story expandable cards are populated.
      for (const s of this.stages()) {
        if (this.PER_EPIC_KEYS.has(s.key)) { this.loadItemsFor(s); }
      }
    });
  }

  /** Fetch per-EPIC stage runs that are stored under the mini workspace ID.
   *
   *  The backend's `?workspace_id=` query filter is broken — it returns 0 items even
   *  when runs with that workspace_id exist.  `?kb_application_id=` works, so we
   *  fetch all-app runs and filter client-side.  Results go into `perEpicRuns` (a
   *  separate signal) so a subsequent `runs(id)` response can't overwrite them.
   */
  private loadPerEpicRunsForMini(miniWsId: string): void {
    const kbAppId = this.store.selectedProject()?.kb_application_id;
    if (!kbAppId) return;
    this.svc.runsByApp(kbAppId).subscribe(r => {
      const scoped = (r.items ?? []).filter(run => run.workspace_id === miniWsId);
      this.perEpicRuns.set(scoped);
    });
  }

  /** Stages waiting on the reviewer — drives the "needs approval" hint banner. */
  awaitingStages(): Stage[] {
    return this.stages().filter((s) => this.stageState(s) === "waiting_for_approval");
  }
  reviewFirst(): void {
    const first = this.awaitingStages()[0];
    if (first) this.openSummary(first);
  }

  artifactsFor(s: Stage): Artifact[] {
    const local = this.artifacts().filter((a) => a.stage_key === s.key);
    const candidates = local.length || !this.PER_EPIC_KEYS.has(s.key)
      ? local
      : this.globalArtifacts().filter((a) => a.stage_key === s.key);

    // Deduplicate: keep only LATEST version per artifact_type (one card per type, not per version)
    const byType = new Map<string, Artifact>();
    for (const a of candidates) {
      const key = a.artifact_type || 'unknown';
      const existing = byType.get(key);
      // Keep the one with highest version number
      if (!existing || this.toNumber(a.version ?? 1) > this.toNumber(existing.version ?? 1)) {
        byType.set(key, a);
      }
    }
    return Array.from(byType.values());
  }

  /** Convert version to number (for templates and operations). */
  toNumber(v: number | string | undefined): number {
    return Number(v ?? 0);
  }

  /** Load version history for an artifact (cached after first load). */
  loadVersionHistory(a: Artifact): void {
    if (!a.id) return;
    if (this.versionHistory()[a.id]) return; // Already cached - don't reload

    // Load only once and cache
    this.svc.listArtifactVersions(a.id).subscribe(resp => {
      // Deduplicate versions: keep only one of each version number
      const versionMap = new Map<string, any>();
      for (const v of (resp.versions ?? [])) {
        const vNum = String(this.toNumber(v.version));
        // Keep the first occurrence of each version number
        if (!versionMap.has(vNum)) {
          versionMap.set(vNum, v);
        }
      }
      const dedupedVersions = Array.from(versionMap.values())
        .sort((x, y) => this.toNumber(y.version) - this.toNumber(x.version));

      this.versionHistory.update(h => ({
        ...h,
        [a.id]: dedupedVersions
      }));
      // Select latest version by default
      const latestVer = dedupedVersions.length > 0 ? this.toNumber(dedupedVersions[0].version) : 0;
      if (latestVer > 0) {
        this.selectedVersions.update(s => ({ ...s, [a.id]: latestVer }));
      }
    });
  }

  /** User selects a specific version to view its content. */
  selectVersion(a: Artifact, version: number | string): void {
    this.selectedVersions.update(s => ({ ...s, [a.id]: this.toNumber(version) }));
  }

  /** User selects a specific version for merge/next flow. */
  selectVersionForForward(artifactId: string, version: number): void {
    this.selectedForForward.update(s => ({ ...s, [artifactId]: version }));
  }

  /** Clear version selection (use latest approved instead). */
  clearVersionSelection(artifactId: string): void {
    this.selectedForForward.update(s => {
      const updated = { ...s };
      delete updated[artifactId];
      return updated;
    });
  }

  /** Get the version that will be used for merge (user-selected or latest approved). */
  getForwardVersion(a: Artifact): number {
    // If user explicitly selected a version, use that
    if (a.id && this.selectedForForward()[a.id]) {
      return this.selectedForForward()[a.id];
    }
    // Otherwise use latest approved
    const approved = this.artifacts()
      .filter(x => x.artifact_type === a.artifact_type && (x.status ?? '').toUpperCase() === 'APPROVED')
      .sort((x, y) => {
        const xVer = Number(x.current_version ?? x.version ?? 0);
        const yVer = Number(y.current_version ?? y.version ?? 0);
        return yVer - xVer;
      })[0];
    return Number(approved?.current_version ?? approved?.version ?? 1);
  }

  /** Check which artifact versions will be used for merge operations. */
  checkMergeReadiness(workspaceId: string): void {
    const artifacts = this.artifacts();
    const artifactTypes = [...new Set(artifacts.map(a => a.artifact_type))];
    const approved: Record<string, {artifact: Artifact; version: number}> = {};

    let approvedCount = 0;
    for (const aType of artifactTypes) {
      const forType = artifacts.filter(a => a.artifact_type === aType);
      const latestApproved = forType
        .filter(a => (a.status ?? '').toUpperCase() === 'APPROVED')
        .sort((x, y) => {
          const xVer = Number(x.current_version ?? x.version ?? 0);
          const yVer = Number(y.current_version ?? y.version ?? 0);
          return yVer - xVer;
        })[0];

      if (latestApproved) {
        const ver = Number(latestApproved.current_version ?? latestApproved.version ?? 1);
        approved[aType] = { artifact: latestApproved, version: ver };
        approvedCount++;
      }
    }

    const ready = approvedCount === artifactTypes.length && approvedCount > 0;
    this.mergeReadiness.set({
      ready,
      message: ready
        ? `✓ Ready to merge: ${approvedCount} approved artifact${approvedCount === 1 ? '' : 's'}`
        : `✕ ${artifactTypes.length - approvedCount} artifact${approvedCount === 1 ? '' : 's'} awaiting approval`,
      selectedVersions: Object.fromEntries(
        Object.entries(approved).map(([type, {artifact, version}]) => [type, version])
      )
    });
  }
  runFor(s: Stage): Run | undefined {
    // Prefer most-recent run by created_at (or started_at as fallback).
    const latest = (arr: Run[]): Run | undefined =>
      arr.reduce<Run | undefined>((best, cur) => {
        const ta = cur.started_at ?? cur.created_at ?? '';
        const tb = best ? (best.started_at ?? best.created_at ?? '') : '';
        return !best || ta > tb ? cur : best;
      }, undefined);

    // For PER_EPIC stages: merge workspace-scoped runs (perEpicRuns) with the
    // global-workspace runs so the most recent one across both pools is used.
    // This handles the backend bug where per-EPIC runs are stored under the mini
    // workspace_id but the workspace query filter doesn't return them.
    const pool = this.PER_EPIC_KEYS.has(s.key)
      ? [...this.runs(), ...this.perEpicRuns(), ...this.globalRuns()]
      : this.runs();

    const byKey = latest(pool.filter(r => r.stage_key === s.key));
    if (byKey) return byKey;

    // Fallback: look up the stage's own run_id directly in any pool.
    if (s.run_id) {
      return [...this.runs(), ...this.perEpicRuns(), ...this.globalRuns()]
        .find(r => r.run_id === s.run_id);
    }
    return undefined;
  }

  /** Compute the effective state for a stage.
   *
   *  Per-EPIC stages (feature/user-story/coverage): their state in the global
   *  workspace object often lags behind the actual run state because the backend
   *  stores per-EPIC runs under the mini workspace_id.  Override with the live
   *  run state whenever it is more specific than what the stage record shows.
   *
   *  Any stage: if the backend hasn't updated the stage state (e.g. the lld stage
   *  still shows "ready" even though a run completed waiting_for_approval), and a
   *  run is available that contradicts "ready", prefer the run's state.
   */
  private stageState(s: Stage): string {
    const ACTIVE = new Set(['queued', 'running', 'waiting_for_approval']);
    // PER_EPIC stages: the global stages API returns s.state='not_ready' for user-story/coverage
    // even when the stage has completed, because mini workspace IDs are excluded from
    // readable_workspace_ids. Use artifact status as the authoritative source.
    if (this.PER_EPIC_KEYS.has(s.key)) {
      const r = this.runFor(s);
      if (r?.state && ACTIVE.has(r.state)) return r.state;
      const arts = this.artifactsFor(s);
      if (arts.some(a => (a.status ?? '').toUpperCase() === 'DRAFT')) return 'waiting_for_approval';
      if (arts.length > 0 && arts.every(a => (a.status ?? '').toUpperCase() === 'APPROVED')) return 'completed';
      // no artifacts yet — fall through to s.state (not_ready / ready)
    }
    // Any stage showing "ready":
    //   1. If the most recent run is actively in-flight or awaiting, use that state.
    //   2. If DRAFT artifacts already exist for this stage, the pipeline agent ran
    //      and wrote output but the backend failed to update the stage record — treat
    //      it as waiting_for_approval so the review/approve flow is accessible.
    if (s.state === 'ready') {
      const r = this.runFor(s);
      if (r?.state && ACTIVE.has(r.state)) return r.state;
      const arts = this.artifactsFor(s);
      if (arts.some(a => (a.status ?? '').toUpperCase() === 'DRAFT')) {
        return 'waiting_for_approval';
      }
      // If every artifact is APPROVED but the stage record was never updated
      // (cross-workspace run registration gap) — show as completed.
      if (arts.length > 0 && arts.every(a => (a.status ?? '').toUpperCase() === 'APPROVED')) {
        return 'completed';
      }
    }
    // A stage whose run failed but ALL its artifacts are APPROVED should show
    // as completed — this happens when artifacts are approved via direct SQL fix
    // while the run record remains in failed state (backend registration gap).
    if (s.state === 'failed') {
      const arts = this.artifactsFor(s);
      if (arts.length > 0 && arts.every(a => (a.status ?? '').toUpperCase() === 'APPROVED')) {
        return 'completed';
      }
    }
    return s.state;
  }

  isDone(s: Stage): boolean {
    const st = this.stageState(s);
    return st === "completed" || st === "approved";
  }
  isFailed(s: Stage): boolean {
    const st = this.stageState(s);
    return st === "failed" || st === "cancelled";
  }
  dot(s: Stage): string {
    const st = this.stageState(s);
    if (this.isDone(s)) return "✔";
    if (st === "waiting_for_approval") return "⚑";
    if (this.isRunning(s)) return "◔";
    if (this.isFailed(s)) return "✕";
    if (s.ready) return "▶";
    return "○";
  }
  tone(s: Stage): string {
    const st = this.stageState(s);
    if (this.isDone(s)) return "good";
    if (st === "waiting_for_approval") return "warn";
    if (this.isRunning(s)) return "run";
    if (this.isFailed(s)) return "err";
    return "idle";
  }
  label(s: Stage): string {
    const st = this.stageState(s);
    if (this.isDone(s)) return "Completed";
    if (st === "waiting_for_approval") return "Awaiting";
    if (this.isRunning(s)) return st === "queued" ? "Queued" : "Running";
    if (this.isFailed(s)) return "Failed";
    if (s.ready) return "Ready";
    return "—";
  }

  openChat(): void {
    const pid = this.store.selectedProject()?.kb_application_id ?? "";
    const token = (typeof localStorage !== "undefined" && localStorage.getItem("access_token")) || "";
    openChatWindow(this.svc.base, pid, token);
  }

  /** Select a stage in the stepper — the selectedStageKey effect handles the scroll. */
  scrollToStage(key: string): void {
    this.store.selectStage(key);
  }

  openSummary(s: Stage): void {
    this.drawer.set(s);
  }
  closeDrawer(): void {
    this.cancelEdit();
    this.drawer.set(null);
  }

  /** A document may be hand-edited/uploaded only while its stage awaits approval. */
  canRevise(s: Stage): boolean {
    return this.stageState(s) === "waiting_for_approval";
  }

  startEdit(a: Artifact): void {
    this.editingId = a.id;
    this.editContent = "";
    this.editComment = a.approval_feedback ?? "";
    this.svc.artifactContent(a.id).subscribe((c) => {
      this.editContent = this.primaryContent(a, c);
    });
  }

  private primaryContent(a: Artifact, c: ArtifactContent | null): string {
    const files = c?.files ?? [];
    if (!files.length) return "";
    const primary =
      files.find((f) => f.filename === `${a.artifact_type}.md`) ??
      files.find((f) => f.filename.toLowerCase().endsWith(".md")) ??
      files[0];
    return primary.content;
  }

  cancelEdit(): void {
    this.editingId = null;
    this.editContent = "";
    this.editComment = "";
    this.savingEdit = false;
  }

  /** Parse one `##` or `###` section into a MappedItem, extracting ID, title, parent and points. */
  private parseSection(section: string): MappedItem {
    const lines = section.split('\n').map(l => l.trim()).filter(l => l);
    const heading = (lines[0] ?? '').replace(/^#{2,3}\s+/, '').trim();

    const idMatch = heading.match(
      /^((?:FEATURE|F|US)-[\d-]+[a-z]?)\s*(?::\s*|\s{2,}|[\s—–]+)(.+)/i
    );
    const id = idMatch?.[1] ?? '';
    const shortTitle = idMatch?.[2]?.trim() ?? heading;

    const desc = lines.slice(1).find(
      l => !l.startsWith('#') && !l.startsWith('**') && !l.startsWith('|') && !l.startsWith('`') && l.trim()
    ) ?? '';

    const featureLine = lines.find(l => /\*\*Feature:\*\*/i.test(l));
    const parentId = featureLine?.match(/F-[\d-]+/)?.[0];

    const pertLine = lines.find(l => /\*\*PERT\*\*/i.test(l));
    const points = pertLine?.match(/\|\s*\*\*(\d+)\*\*/)?.[1];

    // Full body text (raw markdown) for the expanded detail view.
    const body = lines.slice(1).join('\n').trim();

    return { id, title: shortTitle, desc: desc.trim(), parentId, points, body };
  }

  /** Parse markdown: try ### headings first (F-xxx/US-xxx), fall back to ## headings. */
  private parseItems(md: string): MappedItem[] {
    const h3 = md.split(/\n(?=###\s)/).filter(s => s.trimStart().startsWith('###'));
    if (h3.length) return h3.map(s => this.parseSection(s)).filter(i => i.title);
    return md.split(/\n(?=##\s)/)
      .filter(s => s.trimStart().startsWith('##'))
      .map(s => this.parseSection(s))
      .filter(i => i.title);
  }

  /** Parse EPIC-N rows from the EPIC summary table in the epic artifact.
   *  The epic document stores EPICs as table rows, not as ## headings. */
  private parseEpicRows(md: string): MappedItem[] {
    const rows: MappedItem[] = [];
    for (const line of md.split('\n')) {
      if (!line.startsWith('|')) continue;
      if (/^\|[\s\-:|]+\|$/.test(line)) continue;  // separator row
      const cells = line.split('|').slice(1, -1)
        .map(c => c.trim().replace(/\*\*/g, '').trim());
      // Only accept rows whose first cell is exactly "EPIC-N" (e.g. "EPIC-1").
      // Rows from dependency/guardrail tables have extra text like "EPIC-1 api-code opens"
      // — those must be excluded.
      const rawId = (cells[0] ?? '').replace(/\*\*/g, '').trim();
      const epicMatch = rawId.match(/^(EPIC-\d+)$/i);
      if (!epicMatch) continue;
      const id = epicMatch[1].toUpperCase();
      const title = cells[1] ?? '';
      const desc = cells.slice(2).filter(Boolean).join(' · ');
      rows.push({ id, title, desc });
    }
    return rows;
  }

  /** Return EPIC-N items for an artifact (parsed from the EPIC summary table). */
  epicItemsFor(a: Artifact): MappedItem[] {
    // Only exact "EPIC-N" ids — guards against dependency rows like "EPIC-1 api-code opens"
    return this.itemsFor(a).filter(i => /^EPIC-\d+$/i.test(i.id));
  }

  /** Fetch and cache parsed items for all done-stage artifacts. No-ops if already loading. */
  private loadItemsFor(s: Stage): void {
    if (!this.isDone(s) && !this.canRevise(s)) return;
    for (const a of this.artifactsFor(s)) {
      if (this.itemCache()[a.id] !== undefined) continue;  // already loading/loaded
      this.itemCache.update(c => ({ ...c, [a.id]: [] }));  // mark as loading
      this.svc.artifactContent(a.id).subscribe(ac => {
        const md = this.primaryContent(a, ac);
        // kb artifact: parse card extraction summary table + card index for inline expander
        if (a.artifact_type === 'kb') {
          this.kbCards.set(this.parseKbCards(md));
          this.kbCardsByType.set(this.parseKbCardIndex(md));
          this.kbTypedCards.set(this.parseKbTypedCards(md));
          // Discover the STAGING KB version and status so the Promote button has data
          this.svc.kbReview('staging').pipe(catchError(() => of(null))).subscribe(r => {
            if (r?.kb_version) {
              this.store.kbVersion.set(r.kb_version);
              this.store.kbStatus.set(r.status as 'STAGING' | 'ACTIVE' | 'SUPERSEDED' | null);
            }
          });
          // Fetch live KB graph stats (node / edge / chunk counts) from the DB
          const proj = this.store.selectedProject();
          if (proj) {
            this.svc.kbStats(proj.kb_application_id).pipe(catchError(() => of(null))).subscribe(s => {
              if (s) {
                this.store.kbNodeCount.set(s.node_count);
                this.store.kbEdgeCount.set(s.edge_count);
                this.store.kbChunkCount.set(s.chunk_count);
              }
            });
          }
        }
        // epic artifact: parse table rows; all others: parse ## headings
        const items = a.artifact_type === 'epic'
          ? this.parseEpicRows(md)
          : this.parseItems(md);
        this.itemCache.update(c => ({ ...c, [a.id]: items }));
      });
    }
  }

  /** Return parsed heading items for an artifact (empty while loading or unavailable). */
  itemsFor(a: Artifact): MappedItem[] {
    return this.itemCache()[a.id] ?? [];
  }

  /** Return only F-xxx / FEATURE-xxx Feature items — filters out non-feature headings. */
  featureItemsFor(a: Artifact): MappedItem[] {
    return this.itemsFor(a).filter(i =>
      i.id.startsWith('F-') || /^FEATURE-\d/i.test(i.id)
    );
  }

  /** Return only US-xxx User Story items — filters out feature/intro headings. */
  storyItemsFor(a: Artifact): MappedItem[] {
    return this.itemsFor(a).filter(i => i.id.startsWith('US-'));
  }

  /** Group user story items by their parent feature ID for the mapping view.
   *  Uses filtered US-xxx items only — prevents feature headings from appearing as groups. */
  userStoryGroupsFor(a: Artifact): Array<{ featureId: string; items: MappedItem[] }> {
    const map = new Map<string, MappedItem[]>();
    for (const item of this.storyItemsFor(a)) {
      const key = item.parentId ?? '—';
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(item);
    }
    return Array.from(map.entries())
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([featureId, items]) => ({ featureId, items }));
  }

  /** Open a new browser tab with a beautiful AIG-branded HTML view of the artifact.
   *  Falls back to the backend HTML export URL if content cannot be fetched or
   *  the browser blocks the popup. */
  viewBeautifulHtml(a: Artifact, version?: number | string): void {
    const ver = version ?? a.version;
    this.svc.artifactContent(a.id, ver).subscribe({
      next: content => {
        const md = content ? this.primaryContent(a, content) : '';
        if (!md) {
          // Content unavailable — fall back to the backend-rendered HTML
          window.open(this.svc.exportHtmlUrl(a.id) + '?version=' + ver, '_blank', 'noopener,noreferrer');
          return;
        }
        const bodyHtml = this.markdownToHtml(md);
        const page = this.buildHtmlPage(a.artifact_type, bodyHtml);
        const w = window.open('', '_blank');
        if (!w) {
          // Popup blocked — fall back to backend URL
          window.open(this.svc.exportHtmlUrl(a.id), '_blank', 'noopener,noreferrer');
          return;
        }
        w.document.open();
        w.document.write(page);
        w.document.close();
      },
      error: () => {
        // Network error — fall back to backend URL
        window.open(this.svc.exportHtmlUrl(a.id), '_blank', 'noopener,noreferrer');
      },
    });
  }

  private markdownToHtml(md: string): string {
    const inline = (s: string): string =>
      s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
        .replace(/\*(.+?)\*/g, '<em>$1</em>')
        .replace(/`(.+?)`/g, '<code>$1</code>')
        .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');

    // Extract fenced code blocks BEFORE paragraph splitting so blank lines inside
    // code blocks are not mistakenly treated as paragraph separators.
    const codeBlocks: string[] = [];
    const withPlaceholders = md.replace(/```(\w*)\r?\n?([\s\S]*?)```/g, (_, lang, code) => {
      const idx = codeBlocks.length;
      const l = (lang ?? '').toLowerCase().trim();
      const trimmed = code.trim();
      if (l === 'mermaid') {
        // Mermaid.js will initialise these divs after page load
        codeBlocks.push(`<div class="aig-diagram"><div class="mermaid">${trimmed}</div></div>`);
      } else {
        const esc = trimmed.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const label = l ? `<span class="code-lang">${l}</span>` : '';
        codeBlocks.push(`<div class="aig-code-block">${label}<pre><code>${esc}</code></pre></div>`);
      }
      return `\n\nAIG_CODE_${idx}\n\n`;
    });

    return withPlaceholders.split(/\n\n+/).map(block => {
      const t = block.trim();
      if (!t) return '';

      // Code-block placeholder — replace with pre-rendered HTML
      const phm = t.match(/^AIG_CODE_(\d+)$/);
      if (phm) return codeBlocks[+phm[1]] ?? '';

      // Table: any block whose lines mostly start with |
      const tRows = t.split('\n').filter(l => l.startsWith('|'));
      if (tRows.length > 1) {
        const dataRows = tRows.filter(r => !/^\|[\s\-:|]+\|$/.test(r));
        const [head, ...body] = dataRows;
        const th = (head ?? '').split('|').slice(1, -1)
          .map(c => `<th>${inline(c.trim())}</th>`).join('');
        const tbody = body.map(r =>
          '<tr>' + r.split('|').slice(1, -1)
            .map(c => `<td>${inline(c.trim())}</td>`).join('') + '</tr>'
        ).join('');
        return `<table><thead><tr>${th}</tr></thead><tbody>${tbody}</tbody></table>`;
      }

      // Multi-line block — render line by line
      const lineResults: string[] = [];
      let ulOpen = false; let olOpen = false; let bqOpen = false;
      const closeLists = () => {
        if (ulOpen) { lineResults.push('</ul>'); ulOpen = false; }
        if (olOpen) { lineResults.push('</ol>'); olOpen = false; }
      };
      for (const raw of t.split('\n')) {
        const l = raw.trimEnd();

        // Blockquote
        const bqm = l.match(/^>\s*(.*)/);
        if (bqm) {
          closeLists();
          if (!bqOpen) { lineResults.push('<blockquote>'); bqOpen = true; }
          const bqContent = bqm[1].trim();
          if (bqContent) lineResults.push(`<p>${inline(bqContent)}</p>`);
          continue;
        }
        if (bqOpen) { lineResults.push('</blockquote>'); bqOpen = false; }

        const hm = l.match(/^(#{1,3})\s+(.+)/);
        if (hm) {
          closeLists();
          lineResults.push(`<h${hm[1].length}>${inline(hm[2])}</h${hm[1].length}>`);
          continue;
        }
        const ulm = l.match(/^[-*]\s+(.+)/);
        if (ulm) { if (!ulOpen) { lineResults.push('<ul>'); ulOpen = true; } lineResults.push(`<li>${inline(ulm[1])}</li>`); continue; }
        const olm = l.match(/^\d+\.\s+(.+)/);
        if (olm) { if (!olOpen) { lineResults.push('<ol>'); olOpen = true; } lineResults.push(`<li>${inline(olm[1])}</li>`); continue; }
        if (!l.trim()) continue;
        closeLists();
        lineResults.push(`<p>${inline(l)}</p>`);
      }
      closeLists();
      if (bqOpen) lineResults.push('</blockquote>');
      return lineResults.join('\n');
    }).filter(b => b).join('\n');
  }

  private buildHtmlPage(artifactType: string, bodyHtml: string): string {
    const title = artifactType.replace(/-/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
    const ts = new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>${title} — AIG GenAI Build</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Segoe UI',Arial,sans-serif;font-size:14px;line-height:1.7;color:#1a2233;background:#edf2f7}
.page{max-width:980px;margin:0 auto;padding:32px 20px}
.aig-hdr{background:linear-gradient(135deg,#003865 0%,#0077c8 100%);color:#fff;border-radius:14px;padding:30px 40px;margin-bottom:28px;box-shadow:0 6px 24px rgba(0,56,101,.28)}
.aig-logo{display:inline-flex;align-items:center;gap:6px;background:rgba(255,255,255,.18);border-radius:6px;padding:3px 10px;font-size:11px;font-weight:800;letter-spacing:.06em;margin-bottom:14px}
.aig-hdr h1{font-size:26px;font-weight:800;letter-spacing:.01em;margin-bottom:6px;color:#fff !important;border:none !important;padding:0 !important;background:none !important}
.aig-hdr p{font-size:12.5px;color:rgba(255,255,255,.72)}
.content{background:#fff;border-radius:14px;padding:40px 44px;box-shadow:0 2px 14px rgba(0,56,101,.1);border:1px solid #d1dce8}
h1{font-size:21px;font-weight:800;color:#003865;border-bottom:2.5px solid #0077c8;padding-bottom:10px;margin:28px 0 16px}
h1:first-child{margin-top:0}
h2{font-size:16.5px;font-weight:700;color:#003865;border-left:4px solid #0077c8;padding-left:12px;margin:28px 0 12px;background:linear-gradient(90deg,rgba(0,119,200,.06) 0%,transparent 100%);border-radius:0 6px 6px 0;padding:8px 12px}
h3{font-size:14px;font-weight:700;color:#0056a0;margin:20px 0 8px;padding-left:4px}
p{margin:0 0 12px;color:#2d3f56}
strong{color:#003865;font-weight:700}
em{font-style:italic;color:#2d3f56}
code{background:#e8f0fb;color:#0056a0;border-radius:4px;padding:2px 7px;font-size:12.5px;font-family:'Fira Code',Consolas,monospace}
ul,ol{padding-left:22px;margin:0 0 12px}
li{margin:5px 0;color:#2d3f56}
table{width:100%;border-collapse:collapse;margin:18px 0;font-size:13px;border-radius:8px;overflow:hidden;box-shadow:0 1px 6px rgba(0,56,101,.08)}
th{background:#003865;color:#fff;padding:11px 14px;text-align:left;font-weight:700;font-size:12.5px}
td{border-bottom:1px solid #dce6f0;padding:9px 14px;vertical-align:top}
tr:nth-child(even) td{background:#f4f7fb}
tr:hover td{background:#e8f0fb}
blockquote{border-left:4px solid #0077c8;margin:12px 0 16px;padding:10px 16px;background:#f0f6ff;border-radius:0 8px 8px 0}
blockquote p{margin:4px 0;color:#2d3f56;font-size:13.5px}
blockquote p:first-child{margin-top:0}
blockquote p:last-child{margin-bottom:0}
hr{border:none;border-top:1px solid #d1dce8;margin:24px 0}
a{color:#0077c8;text-decoration:none}
a:hover{text-decoration:underline}
.aig-diagram{margin:20px 0;padding:18px;background:#f8fbff;border:1.5px solid #c5d9f0;border-radius:10px;overflow-x:auto;text-align:center}
.aig-diagram svg{max-width:100%;height:auto}
.aig-code-block{margin:16px 0;border-radius:8px;overflow:hidden;border:1px solid #d1dce8;background:#1e2535}
.code-lang{background:#003865;color:#a0c8f0;font-size:11px;font-weight:700;letter-spacing:.05em;padding:4px 14px;text-transform:uppercase}
.aig-code-block pre{margin:0;padding:16px;overflow-x:auto}
.aig-code-block code{background:none;color:#e2ecff;font-family:'Fira Code',Consolas,'Courier New',monospace;font-size:13px;padding:0;border-radius:0}
@media print{body{background:#fff}.page{padding:0}.aig-hdr{-webkit-print-color-adjust:exact;print-color-adjust:exact;border-radius:0}.content{box-shadow:none;border:none;padding:24px 0}}
</style>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<script>
  document.addEventListener('DOMContentLoaded', function() {
    if (typeof mermaid !== 'undefined') {
      mermaid.initialize({ startOnLoad: true, theme: 'default',
        themeVariables: { primaryColor: '#0077c8', primaryTextColor: '#fff', primaryBorderColor: '#003865',
                          lineColor: '#0056a0', secondaryColor: '#e8f0fb', tertiaryColor: '#f0f6ff' } });
    }
  });
</script>
</head>
<body>
<div class="page">
  <div class="aig-hdr">
    <div class="aig-logo">⬡ AIG GenAI Build</div>
    <h1>${title}</h1>
    <p>Generated ${ts} · AIG Core AIDLC Platform</p>
  </div>
  <div class="content">
    ${bodyHtml}
  </div>
</div>
</body>
</html>`;
  }

  saveEdit(a: Artifact): void {
    if (!this.editContent.trim()) return;
    this.savingEdit = true;
    this.svc.reviseArtifact(a.id, this.editContent, this.editComment || undefined).subscribe(() => {
      this.cancelEdit();
      this.reloadCurrent();
    });
  }

  pickRevision(a: Artifact, event: Event): void {
    const file = (event.target as HTMLInputElement).files?.[0];
    if (!file) return;
    this.svc.uploadRevision(a.id, file, this.editComment || undefined).subscribe(() => {
      this.cancelEdit();
      this.reloadCurrent();
    });
  }

  orchestrate(): void {
    const id = this.store.selectedId();
    if (id) this.svc.orchestrate(id, this.approver || "ui").subscribe(() => { this.reloadCurrent(); this.schedulePoll(); });
  }

  rerun(s: Stage): void {
    const id = this.store.selectedId();
    const pipeline = this.data()?.pipeline ?? "";
    if (id) this.svc.startStage(s.key, pipeline, id, true).subscribe(() => { this.reloadCurrent(); this.schedulePoll(); });
  }

  /** A stage the worker is actively processing. */
  isRunning(s: Stage): boolean {
    const st = this.stageState(s);
    return st === "running" || st === "queued";
  }

  /** True once the stage has produced visible output — so the action reads "Re-run".
   *  Uses computed stage state rather than run_id, which can be stale after a failed reset. */
  hasRun(s: Stage): boolean {
    const st = this.stageState(s);
    return st !== 'ready' && st !== 'not_ready';
  }

  /** A stage can be (re)started when it is ready, or already completed (new version). */
  canStart(s: Stage): boolean {
    // Block PRD if KB exists but is not yet promoted to ACTIVE
    if (s.key === 'prd' && this.store.kbStatus() !== null && this.store.kbStatus() !== 'ACTIVE') {
      return false;
    }
    if (!!s.ready || this.isDone(s)) return true;
    // Backend never sets ready=true for per-EPIC stages via the global /stages API because
    // mini workspace IDs are excluded from readable_workspace_ids. Derive from run state.
    if (this.PER_EPIC_KEYS.has(s.key)) {
      const ORDER = ['feature', 'user-story', 'coverage'];
      const idx = ORDER.indexOf(s.key);
      if (idx > 0) {
        const prev = this.stages().find(st => st.key === ORDER[idx - 1]);
        return prev != null && this.isDone(prev);
      }
    }
    return false;
  }

  /** True when the stage is in waiting_for_approval only because DRAFT artifacts
   *  exist, but has NO approvable run registered for this workspace.
   *  This is a backend bug: the plugin wrote output without registering a run.
   *  The user must re-run the agent to get a proper pipeline record, then approve. */
  draftOnlyWait(s: Stage): boolean {
    if (this.stageState(s) !== 'waiting_for_approval') return false;
    const r = this.runFor(s);
    const wsId = this.store.selectedId();
    // If a run exists AND it belongs to the current workspace, it is approvable.
    if (r?.run_id && r?.workspace_id === wsId) return false;
    // Otherwise the "waiting" state comes from DRAFT artifacts with no valid run.
    return this.artifactsFor(s).some(a => (a.status ?? '').toUpperCase() === 'DRAFT');
  }

  /** Tooltip for a disabled Run-agent button.
   *  Uses unmet_prerequisites from the API to name the exact blocker rather than
   *  the generic fallback, which misleads after FRD approval when the real blocker
   *  is Architecture SRD, not the immediately prior stage. */
  startBlockedReason(s: Stage): string {
    // KB not promoted — explain before PRD can run
    if (s.key === 'prd' && this.store.kbStatus() !== null && this.store.kbStatus() !== 'ACTIVE') {
      return `KB version ${this.store.kbVersion() ?? ''} is ${this.store.kbStatus()} — promote it to ACTIVE (use the KB stage Promote button) before running PRD`;
    }
    if (s.reason) return s.reason;
    if (s.unmet_prerequisites?.length) {
      return `Locked — waiting for approval of: ${s.unmet_prerequisites.join(', ')}`;
    }
    return 'Enabled once the previous stage is approved';
  }

  /** Show the start/re-run action — hidden while in-flight or awaiting a decision,
   *  EXCEPT when the stage is in draft-only-wait (no run record): show re-run so
   *  the user can create a proper pipeline record and then approve. */
  showStart(s: Stage): boolean {
    if (this.draftOnlyWait(s)) return true;   // always show re-run when no run record
    return !this.isRunning(s) && this.stageState(s) !== "waiting_for_approval";
  }

  /** Approval UI is available when the stage awaits a decision AND has a valid run
   *  in this workspace to approve, or when it failed but produced a run. */
  canDecide(s: Stage): boolean {
    if (this.draftOnlyWait(s)) return false;   // no valid run → can't approve
    return this.stageState(s) === 'waiting_for_approval' || (this.isFailed(s) && !!s.run_id);
  }

  /** Return timing rows for a stage's most recent run.
   *  Fields:
   *    queuedAt      — human-readable queue time
   *    start         — agent start time
   *    end           — agent finish time ('—' while running)
   *    duration      — agent execution time: started_at → finished_at
   *    totalDuration — wall-clock time:      queued_at  → finished_at
   *    elapsed       — time since start for in-progress runs (updates each poll)
   *    status        — queued | running | completed | failed
   *
   *  Returns null for stages that have not yet been worked on in this workspace
   *  (state = not_ready or ready) so cross-workspace run_id references don't
   *  pollute the timing display with stale data from a different workspace.
   */
  stageTiming(s: Stage): {
    queuedAt: string; start: string; end: string;
    duration: string; totalDuration: string; elapsed: string;
    status: 'queued' | 'running' | 'completed' | 'failed' | 'waiting_for_approval';
  } | null {
    // Guard: only show timing when the stage has actually been worked on here.
    // not_ready / ready mean it hasn't started in this workspace yet — any
    // run_id on the stage is a stale cross-workspace reference.
    const st = this.stageState(s);
    if (st === 'not_ready' || st === 'ready') return null;

    const run = this.runFor(s);
    if (!run) return null;

    // Need at least one timestamp to show anything
    const hasAny = run.queued_at || run.started_at || run.created_at;
    if (!hasAny) return null;

    const fmt = (d: Date): string =>
      d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

    const queuedAt  = run.queued_at  ? fmt(new Date(run.queued_at))  : '—';
    const startRaw  = run.started_at ?? run.created_at;
    const start     = startRaw ? fmt(new Date(startRaw)) : '—';
    let end           = '—';
    let duration      = '';
    let totalDuration = '';
    let elapsed       = '';

    if (run.finished_at && startRaw) {
      // Run is complete — compute both execution and total wall-clock time
      const endDate    = new Date(run.finished_at);
      const startDate  = new Date(startRaw);
      const queueDate  = run.queued_at ? new Date(run.queued_at) : startDate;
      end           = fmt(endDate);
      duration      = this.formatDuration(endDate.getTime() - startDate.getTime());
      totalDuration = this.formatDuration(endDate.getTime() - queueDate.getTime());
    } else if (startRaw && !this.isDone(s)) {
      // Only show a live elapsed counter for genuinely in-progress stages.
      // Completed/approved stages (per-EPIC approvals update artifact status but may
      // not set finished_at on the run) must not show a growing elapsed timer.
      elapsed = this.formatDuration(Date.now() - new Date(startRaw).getTime());
    }

    // Use stageState() as the authoritative source — run.finished_at may be null for
    // per-EPIC stages even after approval (backend updates artifacts, not the run record).
    const status: 'queued' | 'running' | 'completed' | 'failed' | 'waiting_for_approval' =
      this.isFailed(s)                          ? 'failed'              :
      st === 'waiting_for_approval'             ? 'waiting_for_approval' :
      (run.finished_at || this.isDone(s))       ? 'completed'           :
      run.started_at                            ? 'running'             : 'queued';

    return { queuedAt, start, end, duration, totalDuration, elapsed, status };
  }

  private formatDuration(ms: number): string {
    if (!isFinite(ms) || ms < 0) return '—';
    const totalSec = Math.floor(ms / 1000);
    const h = Math.floor(totalSec / 3600);
    const m = Math.floor((totalSec % 3600) / 60);
    const s = totalSec % 60;
    if (h > 0) return `${h}h ${m}m ${s}s`;
    if (m > 0) return `${m}m ${s}s`;
    return `${s}s`;
  }

  /** Open the PRD extraction dialog instead of starting immediately. */
  openPrdDialog(s: Stage): void {
    this.prdScopeType = 'specific';
    this.prdModuleName = '';
    this.prdBusinessArea = '';
    this.prdRole = '';
    this.moduleList = [];
    this.prdDialog.set(s);
    const gearId = (this.data()?.workspace?.gear_id ?? this.store.selectedProject()?.gear_id) ?? undefined;
    this.modulesLoading = true;
    this.svc.listModules(gearId).subscribe({
      next: (modules) => { this.moduleList = modules; this.modulesLoading = false; },
      error: () => { this.modulesLoading = false; },
    });
  }

  closePrdDialog(): void {
    this.prdDialog.set(null);
  }

  /** Called from the PRD dialog "Start PRD Agent" button. */
  startPrd(s: Stage): void {
    const moduleScope    = this.prdScopeType === 'specific' ? this.prdModuleName.trim() || null : null;
    const businessArea   = this.prdBusinessArea.trim() || null;
    const role           = this.prdRole.trim() || null;
    this.closePrdDialog();
    this._doStart(s, moduleScope, businessArea, role);
  }

  /** Start (or re-run) a stage. Force a new attempt when it already ran.
   *  Per-EPIC stages (feature/user-story/coverage) live in uw-cr-global (tier=global).
   *  workspace_id must be the global workspace for pipeline resolution; epic_workspace_id
   *  carries the selected mini workspace so the backend scopes {N} to the right EPIC. */
  start(s: Stage): void {
    if (!this.canStart(s)) return;

    // Guard: warn before superseding DRAFT documents that are waiting for approval.
    // This prevents accidental re-runs that discard hours of agent output.
    const hasDraft = this.artifactsFor(s).some(
      a => (a.status ?? '').toUpperCase() === 'DRAFT',
    );
    if (hasDraft) {
      const ok = confirm(
        `"${s.name}" already has documents drafted and waiting for approval.\n\n` +
        `Re-running will start a NEW agent run and supersede the existing drafts.\n\n` +
        `To approve the existing output instead, close this dialog and click\n` +
        `"⚑ Review & approve".\n\n` +
        `Continue with re-run?`,
      );
      if (!ok) return;
    }

    // PRD stage: show the RE Graph extraction dialog only for RE Graph workspaces.
    // RED/requirements workspaces (intake_source != "re-graph") run directly without dialog.
    if (s.key === 'prd') {
      const intakeSource = (this.data()?.workspace as any)?.intake_source;
      if (intakeSource === 're-graph') {
        this.openPrdDialog(s);
      } else {
        this._doStart(s, null);
      }
      return;
    }

    this._doStart(s, null);
  }

  /** Shared start logic — called from start() and startPrd(). */
  private _doStart(s: Stage, moduleScope: string | null, businessArea?: string | null, role?: string | null): void {
    this.runError.set(null);
    const isPerEpic = this.PER_EPIC_KEYS.has(s.key);
    const globalId = this.store.globalWorkspace()?.id;
    const workspaceId = isPerEpic ? (globalId ?? this.store.selectedId()) : this.store.selectedId();
    const pipeline = isPerEpic
      ? (this.store.globalWorkspace()?.pipeline || this.store.globalPipeline() || null)
      : (this.data()?.pipeline ?? "");
    if (!workspaceId) return;
    const epicWorkspaceId = isPerEpic && this.isMini() ? (this.store.selectedId() ?? undefined) : undefined;
    this.svc.startStage(s.key, pipeline, workspaceId, this.hasRun(s), epicWorkspaceId, moduleScope, businessArea, role).subscribe({
      next: () => {
        if (isPerEpic) { this.store.refreshGlobalStages(); this.loadGlobalForMini(); }
        this.reloadCurrent();
        this.schedulePoll();
      },
      error: (err) => {
        console.error('[pipeline] startStage failed:', err);
        this.runError.set(err?.error?.detail ?? err?.message ?? 'Run failed — check console for details.');
      },
    });
  }

  decide(s: Stage, decision: "approve" | "reject"): void {
    // Use the most-recent run's ID rather than s.run_id — for per-EPIC stages the
    // stage's run_id points to an old global-workspace run, not the live per-EPIC one.
    const runId = this.runFor(s)?.run_id ?? s.run_id;
    if (!runId || !this.approver.trim()) return;
    this.runError.set(null);
    this.svc
      .approve(runId, this.approver.trim(), s.approval_persona ?? "", decision, this.comment || undefined)
      .pipe(
        // Fallback: if the run-level approval fails (e.g. 409 because the run is in
        // `completed` state due to a backend workspace-mismatch bug), approve each
        // DRAFT artifact individually.  This avoids needing a manual SQL fix.
        catchError((err) => {
          const status = err?.status ?? 0;
          if (status !== 409) throw err;           // unexpected error — surface it

          const draftArtifacts = this.artifactsFor(s)
            .filter(a => (a.status ?? '').toUpperCase() === 'DRAFT');
          if (!draftArtifacts.length) throw err;  // nothing to fall back on

          console.warn(
            `[pipeline] run-level approval returned 409 for run ${runId}; ` +
            `falling back to per-artifact approval for ${draftArtifacts.length} DRAFT artifact(s).`,
          );
          return forkJoin(
            draftArtifacts.map(a =>
              this.svc.approveArtifact(
                a.id,
                this.approver.trim(),
                s.approval_persona ?? '',
                decision,
                this.comment || undefined,
              ).pipe(catchError(e => of({ _err: e }))),
            ),
          );
        }),
      )
      .subscribe({
        next: () => {
          this.comment = "";
          this.closeDrawer();
          this.store.load();
          // Refresh workspace list after any approval — EPIC Set approval fans out
          // one Mini Workspace per EPIC (FR-P4); without this the sidebar stays empty
          // until a full page reload.
          if (decision === "approve") {
            this.store.refreshWorkspaces();
          }
          this.reloadCurrent();
        },
        error: (err) => {
          console.error('[pipeline] approve failed:', err);
          this.runError.set(err?.error?.detail ?? err?.message ?? 'Approval failed — check console for details.');
        },
      });
  }

  private reloadCurrent(): void {
    const id = this.store.selectedId();
    if (id) {
      // Clear caches so newly-registered runs are re-fetched on each reload.
      this.fetchedRunIds.clear();
      this.perEpicRuns.set([]);
      // load() fetches fresh workspaceStages and syncs store.stages() — no need
      // to also call store.select(id) which would clear selectedStageKey on every
      // poll tick and wipe store.stages() to [] on any transient API failure.
      this.load(id);
      const depStage = this.stages().find(s => s.key === 'local-deployment');
      if (depStage) this.parseDeployLog(this.runFor(depStage));
      // While viewing a mini workspace, keep global stages live so per-EPIC
      // stage state (running → awaiting / completed) stays up to date.
      if (this.isMini()) this.store.refreshGlobalStages();
    }
  }

  ngOnDestroy(): void {
    if (this.pollTimer) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
  }

  /** Dense poll right after a user action (start/rerun/orchestrate) so the
   *  board transitions from queued → running → awaiting quickly.
   *  The continuous 10-second auto-poll takes over once grace runs out. */
  private schedulePoll(grace = 3): void {
    // Restart auto-poll immediately so the interval resets from now.
    this.startAutoPoll();
    // Fire one quick reload after 4s to catch the queued→running transition.
    setTimeout(() => {
      this.reloadCurrent();
      if (grace > 0) this.schedulePoll(grace - 1);
    }, 4_000);
  }
}
