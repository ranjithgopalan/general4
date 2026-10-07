import {
  AfterViewInit, ChangeDetectorRef, Component, ElementRef, Input, NgZone, OnChanges, OnDestroy, SimpleChanges, ViewChild,
  ViewEncapsulation,
} from "@angular/core";
import * as d3 from "d3";

import {
  AidlcPlatformMgmtService, LibraryGraph, LibraryGraphEdge, LibraryGraphNode, LibraryGraphNodeDetail, LibraryGraphPath,
  LibraryGraphVersion, LibraryGraphWalk, LibraryPageCards, ProjectAspGraph,
} from "../services/aidlc-platform-mgmt.service";

/** Grouping of pages for the matrix / module graph: the source folder, or the business domain. */
type GroupBy = "module" | "domain";
type Tab = "matrix" | "modgraph" | "files" | "trav" | "table" | "versions" | "project";
const CARD_KINDS = ["BR", "FR", "SCR", "CMP", "API", "ENT", "WF"];

interface Pair { a: string; b: string; c: number; }
interface Selection { type: "group" | "pair" | "file" | "trav"; id?: string; a?: string; b?: string; }
/** A version row as the Versions tab shows it (the API adds where the files were stored). */
type VersionRow = LibraryGraphVersion & { storage?: { ok?: boolean; store?: string; uri?: string; key_prefix?: string; error?: string } };
interface FileRef { id: string; file: string; group: string; n?: string; }
interface GroupedRefs { group: string; items: FileRef[]; }

/** The detail panel's view-model (one flat shape; `kind` says which block renders). */
interface Detail {
  kind: "default" | "group" | "pair" | "file" | "walk" | "path";
  title?: string;
  hubs?: (FileRef & { degree: number })[];
  rows?: [string, string][];
  dependsOn?: Pair[];
  dependedBy?: Pair[];
  pages?: (FileRef & { degree: number })[];
  pairEdges?: { sId: string; sFile: string; tId: string; tFile: string; evidence: string; label: string }[];
  reverse?: Pair | null;
  node?: LibraryGraphNode & { submodule?: string };
  loading?: boolean;
  purpose?: string;
  tables?: string[];
  includes?: string[];
  library?: LibraryPageCards | null;
  cardKinds?: string[];
  out?: GroupedRefs[];
  inc?: GroupedRefs[];
  touched?: Pair[];
  hops?: { sId: string; sFile: string; tId: string; tFile: string; evidence: string; label: string }[];
  rings?: { depth: number; items: FileRef[] }[];
  note?: string;
}

const KIND_LABEL: Record<string, string> = { DEPENDS_ON: "include / redirect / link", TRIGGERS: "form post / navigation" };
const LAYER_COLOR: Record<string, string> = { UI: "#2a78d6", "Business Logic": "#eb6834", "Data Access": "#1baf7a" };
const RISK_COLOR: Record<string, string> = { low: "#0ca30c", medium: "#fab219", high: "#d03b3b" };
const OTHER_COLOR = "#aeaca4";

/**
 * Dependency graph of a Gear ID's ASP pages, read from the platform KB tables through
 * GET /library/graph*. Five views: module (or domain) matrix, module graph, page graph clustered by
 * module, traverse (server-side walk of the edge table), and the reference table; a detail panel on
 * the right shows the module / domain / references of each page so cards can be arranged by them.
 */
@Component({
  selector: "app-apm-library-dependency-graph",
  // None: the SVG marks are created by d3, not Angular, so emulated encapsulation would not reach
  // them (hulls, rings and edges would fall back to black fills). Every class is prefixed .ldg-.
  encapsulation: ViewEncapsulation.None,
  template: `
    <div class="apm-card">
      <div class="apm-card-head ldg-head">
        <button type="button" class="ldg-toggle" (click)="toggleCollapsed()" [attr.aria-expanded]="!collapsed"
                [title]="collapsed ? 'Expand the dependency graph' : 'Collapse the dependency graph'">{{ collapsed ? '▸' : '▾' }}</button>
        <h4 (click)="toggleCollapsed()" style="cursor:pointer">Dependency graph</h4>
        <span style="font-size:12px;color:var(--text-secondary);margin-left:8px;">Gear: <b>{{ gearId }}</b>
          <span *ngIf="graph?.built_at"> · built {{ graph?.built_at | date:'medium' }}</span></span>
        <label class="ldg-verpick" *ngIf="versions.length && !collapsed">Version
          <select [(ngModel)]="selectedVersion" (ngModelChange)="load()">
            <option *ngFor="let v of versions" [value]="v.kb_version">v{{ v.version }}{{ v.is_current ? ' · current' : '' }} · {{ v.built_at | date:'MMM d, HH:mm' }}</option>
          </select>
        </label>
        <span class="apm-chip ok" *ngIf="!collapsed && graph?.is_current">current</span>
        <button *ngIf="!collapsed && graph && !graph.is_current" class="apm-btn green sm" type="button" (click)="activate(graph.kb_version)" [disabled]="activating">
          {{ activating ? 'Switching…' : 'Make v' + graph.version + ' current' }}</button>
        <span class="ldg-headsum" *ngIf="collapsed && graph">
          v{{ graph.version }}{{ graph.is_current ? ' (current)' : '' }} · {{ graph.nodes.length | number }} pages · {{ E.length | number }} references · {{ crossCount | number }} cross-{{ groupBy }} · {{ groups.length }} {{ groupBy === 'module' ? 'modules' : 'domains' }}
        </span>
        <span class="ldg-headsum" *ngIf="collapsed && !graph && !loading">not built yet</span>
        <span style="flex:1"></span>
        <button *ngIf="!collapsed" class="apm-btn outline sm" type="button" (click)="load()" [disabled]="loading">{{ loading ? 'Loading…' : 'Refresh' }}</button>
      </div>

      <div [hidden]="collapsed">
      <div class="apm-banner" *ngIf="graph && !graph.is_current" style="margin:12px 16px 0;background:#fff8e1;border-color:#fcd34d;color:#92400e;">
        You are viewing v{{ graph.version }}, which is not the current version{{ currentVersionNumber ? ' (current is v' + currentVersionNumber + ')' : '' }}.
        Projects and downstream agents use the current one. Use "Make v{{ graph.version }} current" to switch going forward.
      </div>
      <div class="apm-card-body" *ngIf="!graph && !loading">
        <p class="sub">No dependency graph for Gear ID <b>{{ gearId }}</b> yet. Upload the RED file-analysis JSON
          (document kind <b>red-analysis</b>) in the upload card below; the page-to-page references are derived and
          written to the KB graph tables, and this view reads them back.</p>
      </div>

      <ng-container *ngIf="graph">
        <div class="ldg-stats">
          <div *ngIf="projectId"><b>{{ projectPages.length | number }}</b><span>Project pages</span></div>
          <div *ngIf="projectId && projectGraph"><b>{{ projectGraph.neighbours.length | number }}</b><span>Connected pages</span></div>
          <div *ngIf="!projectId"><b>{{ graph.nodes.length | number }}</b><span>Pages</span></div>
          <div><b>{{ E.length | number }}</b><span>References</span></div>
          <div><b>{{ crossCount | number }}</b><span>Cross-{{ groupBy }}</span></div>
          <div><b>{{ groups.length | number }}</b><span>{{ groupBy === 'module' ? 'Modules' : 'Domains' }}</span></div>
        </div>

        <div class="ldg-filters">
          <label>Group by
            <select [(ngModel)]="groupBy" (ngModelChange)="renderAll()">
              <option value="module">Module (source folder)</option>
              <option value="domain">Business domain</option>
            </select></label>
          <label>Reference kind
            <select [(ngModel)]="kind" (ngModelChange)="renderAll()">
              <option value="all">All references</option>
              <option value="DEPENDS_ON">Includes, redirects and links</option>
              <option value="TRIGGERS">Form posts and navigation</option>
            </select></label>
          <label>Hide hub pages with at least
            <input type="number" min="5" max="500" step="5" placeholder="off" [(ngModel)]="hubInput" (change)="renderAll()" style="width:70px">
            <span class="ldg-hint">connections</span></label>
          <span class="ldg-chips"><span class="ldg-chip static" *ngFor="let h of hubs">{{ byId.get(h)?.file }} <i>hidden</i></span></span>
        </div>

        <div class="apm-banner" *ngIf="projectId && projectGraph && projectGraph.unmatched.length" style="margin:10px 16px 0;background:#fff8e1;border-color:#fcd34d;color:#92400e;">
          {{ projectGraph.unmatched.length }} uploaded file(s) are not in the Gear ID's dependency graph: {{ projectGraph.unmatched.join(', ') }}
        </div>
        <div class="ldg-tabs" role="tablist">
          <button *ngFor="let t of visibleTabs" role="tab" type="button" [attr.aria-selected]="tab === t.key" (click)="switchTab(t.key)">{{ t.label }}</button>
        </div>

        <div class="ldg-layout" [class.ldg-layout-full]="tab === 'versions'">
          <div class="ldg-views">

            <section [hidden]="tab !== 'matrix'">
              <p class="sub ldg-lead">Rows are the referencing {{ groupBy }}, columns the referenced one. Darker means more page-level references; the diagonal is references that stay inside. Click a cell to list the pages behind it.</p>
              <div class="ldg-scroll"><svg #matrixSvg class="ldg-svg" role="img" aria-label="Module-to-module reference counts"></svg></div>
              <div class="ldg-legend"><span>1</span><span class="ldg-ramp"></span><span>{{ matrixMax }}</span><span class="ldg-hint">references between two {{ groupBy === 'module' ? 'modules' : 'domains' }}</span></div>
              <h5 class="ldg-h5">Strongest cross-{{ groupBy }} links</h5>
              <div class="ldg-pairs">
                <button type="button" class="ldg-pairrow" *ngFor="let p of topPairs" (click)="select({ type: 'pair', a: p.a, b: p.b })">
                  <span class="lbl">{{ p.a }} → {{ p.b }}</span>
                  <span class="barwrap"><span class="bar" [style.width.%]="topPairs[0] ? p.c / topPairs[0].c * 100 : 0"></span></span>
                  <span class="num">{{ p.c }}</span>
                </button>
              </div>
            </section>

            <section [hidden]="tab !== 'modgraph'">
              <p class="sub ldg-lead">Each circle is a {{ groupBy }}, sized by its page count. Arrows point from the referencing side to the referenced one, thicker for more references. Hover one to isolate its links.</p>
              <div class="ldg-controls"><label>Show links with at least <input type="range" min="1" [max]="modMinMax" [(ngModel)]="minMod" (ngModelChange)="renderModGraph()"> <b>{{ minMod }}</b> references</label></div>
              <svg #modSvg class="ldg-svg" role="img" aria-label="Directed graph of module dependencies"></svg>
            </section>

            <section [hidden]="tab !== 'files'">
              <p class="sub ldg-lead">Pages clustered by {{ groupBy }}. Arrows point from the referencing page to the page it includes, redirects to or posts to. Drag to pan, scroll to zoom, click a page for its details.</p>
              <div class="ldg-controls"><span>Show:</span>
                <button type="button" class="ldg-link" (click)="setScope(groups)">all</button>
                <button type="button" class="ldg-link" (click)="setScope([])">none</button></div>
              <div class="ldg-chips">
                <button type="button" class="ldg-chip" *ngFor="let g of groups" [attr.aria-pressed]="scope.has(g)" (click)="toggleScope(g)">{{ g }} <i>{{ groupInfo.get(g)?.files }}</i></button>
              </div>
              <div class="ldg-controls" style="margin-top:8px">
                <label><input type="checkbox" [(ngModel)]="neighbours" (ngModelChange)="renderFileGraph()"> Include directly connected pages from other {{ groupBy }}s</label>
                <label><input type="checkbox" [(ngModel)]="hideIntra" (ngModelChange)="renderFileGraph()"> Hide references inside a {{ groupBy }}</label>
                <label>Colour by <select [(ngModel)]="colorBy" (ngModelChange)="renderFileGraph(); runTraverse(false)"><option value="layer">Layer</option><option value="risk">Risk level</option></select></label>
                <label>Find page <input type="search" list="ldg-files" [(ngModel)]="search" (change)="findPage()" placeholder="e.g. accgetBusinessline.asp" autocomplete="off"></label>
              </div>
              <svg #fileSvg class="ldg-svg ldg-graph" role="img" aria-label="Force-directed graph of page references"></svg>
              <div class="ldg-legend">
                <span *ngFor="let l of legend"><i class="sw" [style.background]="l[1]"></i>{{ l[0] }}</span>
                <span>Circle size: lines of code. Faded circles: pages outside the chosen {{ groupBy }}s.</span>
              </div>
              <p class="ldg-hint">{{ fileCount }}</p>
            </section>

            <section [hidden]="tab !== 'trav'">
              <p class="sub ldg-lead">Each hop is a query against the edge table. Downstream answers "what does this page use"; upstream answers "what breaks if this page changes". Give a second page to get the shortest reference chain instead. Hidden hub pages are skipped.</p>
              <div class="ldg-controls">
                <label>Start page <input type="search" list="ldg-files" [(ngModel)]="travStartText" (change)="travStartChanged()" placeholder="e.g. accaddbusinessline.asp" autocomplete="off"></label>
                <label>Direction <select [(ngModel)]="travDir" (ngModelChange)="runTraverse(true)"><option value="down">Downstream: what it references</option><option value="up">Upstream: what references it</option><option value="both">Both</option></select></label>
                <label>Depth <input type="range" min="1" max="6" [(ngModel)]="travDepth" (ngModelChange)="runTraverse(true)"> <b>{{ travDepth }}</b></label>
                <label>Path to <input type="search" list="ldg-files" [(ngModel)]="travToText" (change)="travToChanged()" placeholder="optional second page" autocomplete="off"></label>
                <button type="button" class="apm-btn outline sm" (click)="clearTraverse()">Clear</button>
              </div>
              <svg #travSvg class="ldg-svg ldg-graph" role="img" aria-label="Reachability rings from the start page"></svg>
              <div class="ldg-legend"><span *ngFor="let l of legend"><i class="sw" [style.background]="l[1]"></i>{{ l[0] }}</span><span>Thick ring: start page. Solid arrows: edges the walk followed.</span></div>
              <p class="ldg-hint">{{ travCount }}</p>
            </section>

            <section [hidden]="tab !== 'project'" *ngIf="projectId">
              <p class="sub ldg-lead">Each ASP file the project uploaded, where it sits (folder module, sub-module, library module, domain) and the Global Library cards it brings to the PRD and FRD. Click a file for its full detail and references.</p>
              <div class="ldg-scroll"><table class="apm-table ldg-table">
                <thead><tr><th>ASP file</th><th>Module</th><th>Sub-module</th><th>Library module</th><th>Domain</th><th class="num" *ngFor="let k of cardKinds">{{ k }}</th><th class="num">Refs</th></tr></thead>
                <tbody>
                  <tr *ngFor="let p of projectPages" [style.background]="p.id === fileSel ? 'rgba(0,119,200,.06)' : null">
                    <td class="mono"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: p.id })">{{ p.file }}</button></td>
                    <td>{{ p.module }}</td><td>{{ p.submodule || p.library_submodule || '—' }}</td>
                    <td>{{ p.library_module || p.module_key || '—' }}</td><td>{{ p.library_domain || p.domain || '—' }}</td>
                    <td class="num" *ngFor="let k of cardKinds">{{ p.cards?.[k] || '' }}</td>
                    <td class="num">{{ deg.get(p.id) || 0 }}</td>
                  </tr>
                  <tr *ngFor="let u of projectGraph?.unmatched || []"><td class="mono">{{ u }}</td><td colspan="12" class="ldg-hint">not in the dependency graph of this Gear ID</td></tr>
                </tbody></table></div>
              <p class="ldg-hint" *ngIf="projectGraph && !projectGraph.library.available">The Global Library has no module JSON uploaded yet, so no cards can be shown; upload it on the Global Library page.</p>
              <p class="ldg-hint" *ngIf="projectGraph?.library?.available">Cards from the {{ projectGraph?.library?.source === 'approved' ? 'approved library build v' + projectGraph?.library?.version : projectGraph?.library?.source === 'latest' ? 'latest library build (not approved)' : 'uploaded module JSONs (library not built yet)' }}.</p>
            </section>

            <section [hidden]="tab !== 'versions'">
              <p class="sub ldg-lead">Every RED file-analysis upload for this Gear ID is kept as its own version in the KB tables (<span class="mono">_library-{{ gearId }}-deps-v&lt;N&gt;</span>) and its files under the artifact store. One version is <b>current</b>: that is what projects and agents use. View any version here; make an older one current to roll back.</p>
              <div class="ldg-scroll"><table class="apm-table ldg-table">
                <thead><tr><th>Version</th><th>State</th><th>Built</th><th class="num">Pages</th><th class="num">Edges</th><th class="num">Modules</th><th>Source file</th><th>Stored at</th><th></th></tr></thead>
                <tbody><tr *ngFor="let v of versionsNewestFirst" [style.background]="v.kb_version === selectedVersion ? 'rgba(0,119,200,.06)' : null">
                  <td class="mono">v{{ v.version }}<div class="ldg-hint mono" style="font-size:10.5px">{{ v.kb_version }}</div></td>
                  <td><span class="apm-chip" [class.ok]="v.is_current" [class.amber]="v.status === 'STAGING' && !v.is_current">{{ v.is_current ? 'current' : (v.status || '').toLowerCase() }}</span></td>
                  <td>{{ v.built_at | date:'medium' }}</td>
                  <td class="num">{{ v.pages | number }}</td><td class="num">{{ v.edges | number }}</td><td class="num">{{ v.modules }}</td>
                  <td class="mono" style="font-size:11.5px;white-space:nowrap">{{ v.source }}</td>
                  <td style="min-width:240px;max-width:420px">
                    <ng-container *ngIf="v.storage?.ok"><span class="apm-chip">{{ v.storage?.store }}</span>
                      <div class="mono" style="font-size:11px;word-break:break-all;margin-top:3px">{{ v.storage?.key_prefix }}</div></ng-container>
                    <span *ngIf="v.storage && !v.storage?.ok" class="apm-chip err" [title]="v.storage?.error">not stored ({{ v.storage?.store }})</span>
                    <span *ngIf="!v.storage" class="ldg-hint">—</span>
                  </td>
                  <td style="white-space:nowrap">
                    <button type="button" class="apm-btn outline sm" (click)="selectedVersion = v.kb_version; load()" [disabled]="v.kb_version === selectedVersion">View</button>
                    <button type="button" class="apm-btn green sm" style="margin-left:6px" *ngIf="!v.is_current" (click)="activate(v.kb_version)" [disabled]="activating">Make current</button>
                  </td></tr></tbody></table></div>
              <p class="ldg-hint" *ngIf="!versions.length">No versions yet.</p>
            </section>

            <section [hidden]="tab !== 'table'">
              <div class="ldg-controls"><label>Filter <input type="search" [(ngModel)]="tblFilter" placeholder="page or module name"></label><span class="ldg-hint">{{ tableRows.length | number }} of {{ E.length | number }} references</span></div>
              <h5 class="ldg-h5">{{ groupBy === 'module' ? 'Module' : 'Domain' }} pairs</h5>
              <div class="ldg-scroll"><table class="apm-table ldg-table"><thead><tr><th>From</th><th>To</th><th class="num">References</th></tr></thead>
                <tbody><tr *ngFor="let p of tablePairs"><td><button type="button" class="ldg-link" (click)="select({ type: 'group', id: p.a })">{{ p.a }}</button></td>
                  <td><button type="button" class="ldg-link" (click)="select({ type: 'group', id: p.b })">{{ p.b }}</button></td>
                  <td class="num"><button type="button" class="ldg-link" (click)="select({ type: 'pair', a: p.a, b: p.b })">{{ p.c }}</button></td></tr></tbody></table></div>
              <h5 class="ldg-h5">Page references</h5>
              <div class="ldg-scroll"><table class="apm-table ldg-table"><thead><tr><th>From page</th><th>{{ groupBy }}</th><th>To page</th><th>{{ groupBy }}</th><th>Kind</th><th>Evidence</th></tr></thead>
                <tbody><tr *ngFor="let r of tableRows"><td class="mono"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: r.a.id })">{{ r.a.file }}</button></td><td>{{ keyOf(r.a) }}</td>
                  <td class="mono"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: r.b.id })">{{ r.b.file }}</button></td><td>{{ keyOf(r.b) }}</td>
                  <td>{{ kindLabel(r.e.label) }}</td><td class="ev">{{ r.e.evidence }}</td></tr></tbody></table></div>
            </section>
          </div>

          <aside class="ldg-detail" aria-live="polite" [hidden]="tab === 'versions'">
            <ng-container [ngSwitch]="detail.kind">
              <ng-container *ngSwitchCase="'default'">
                <div class="kicker">Reading the map</div><h5>Pick a cell, {{ groupBy }}, link or page</h5>
                <p class="note">A reference means one page includes, redirects to, links to or posts to another, as recorded in its RED analysis. Pages are Component nodes and references are DEPENDS_ON / TRIGGERS edges in the KB graph tables for this Gear ID.</p>
                <p class="note">Menus and sign-out pages connect to almost everything. Set a hub threshold above to drop them from every view and from the traversal.</p>
                <h6>Most connected pages</h6>
                <ul class="list"><li *ngFor="let h of detail.hubs"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: h.id })">{{ h.file }}</button><span class="n">{{ h.degree }} · {{ h.group }}</span></li></ul>
              </ng-container>

              <ng-container *ngSwitchCase="'group'">
                <div class="kicker">{{ groupBy }}</div><h5>{{ detail.title }}</h5>
                <dl class="kv"><ng-container *ngFor="let r of detail.rows"><dt>{{ r[0] }}</dt><dd>{{ r[1] }}</dd></ng-container></dl>
                <div class="btnrow"><button type="button" class="apm-btn sm" (click)="showGroupInGraph(detail.title!)">Show pages in graph</button></div>
                <h6>Depends on</h6>
                <p class="note" *ngIf="!detail.dependsOn?.length">None</p>
                <ul class="list"><li *ngFor="let p of detail.dependsOn"><button type="button" class="ldg-link" (click)="select({ type: 'pair', a: p.a, b: p.b })">{{ p.b }}</button><span class="n">{{ p.c }}</span></li></ul>
                <h6>Depended on by</h6>
                <p class="note" *ngIf="!detail.dependedBy?.length">None</p>
                <ul class="list"><li *ngFor="let p of detail.dependedBy"><button type="button" class="ldg-link" (click)="select({ type: 'pair', a: p.a, b: p.b })">{{ p.a }}</button><span class="n">{{ p.c }}</span></li></ul>
                <h6>Pages</h6>
                <ul class="list"><li *ngFor="let f of detail.pages"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: f.id })">{{ f.file }}</button><span class="n">{{ f.degree }} refs</span></li></ul>
              </ng-container>

              <ng-container *ngSwitchCase="'pair'">
                <div class="kicker">{{ groupBy }} link</div><h5>{{ detail.title }}</h5>
                <dl class="kv"><dt>References</dt><dd>{{ detail.pairEdges?.length }}</dd><dt>Reverse direction</dt>
                  <dd><button *ngIf="detail.reverse" type="button" class="ldg-link" (click)="select({ type: 'pair', a: detail.reverse!.a, b: detail.reverse!.b })">{{ detail.reverse!.c }} ({{ detail.reverse!.a }} → {{ detail.reverse!.b }})</button><span *ngIf="!detail.reverse">0</span></dd></dl>
                <div class="btnrow"><button type="button" class="apm-btn sm" (click)="openPairInGraph()">Open in page graph</button></div>
                <h6>Pages behind this link</h6>
                <ul class="edgelist"><li *ngFor="let e of detail.pairEdges"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: e.sId })">{{ e.sFile }}</button> → <button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: e.tId })">{{ e.tFile }}</button><span class="ev" *ngIf="e.evidence && e.evidence !== e.tFile">{{ e.evidence }}</span></li></ul>
              </ng-container>

              <ng-container *ngSwitchCase="'file'">
                <div class="kicker">Page</div><h5 class="mono">{{ detail.node?.file }}</h5>
                <dl class="kv">
                  <dt>Module</dt><dd><button type="button" class="ldg-link" (click)="selectGroupOf(detail.node!, 'module')">{{ detail.node?.module }}</button><span class="ldg-hint" *ngIf="detail.node?.submodule"> / {{ detail.node?.submodule }}</span></dd>
                  <dt>Sub-module</dt><dd>{{ detail.library?.submodule || detail.node?.submodule || '—' }}</dd>
                  <dt>Library module</dt><dd>{{ detail.library?.module || detail.library?.module_key || (detail.library ? 'not in the library' : '—') }}</dd>
                  <dt>Domain</dt><dd><button type="button" class="ldg-link" (click)="selectGroupOf(detail.node!, 'domain')">{{ detail.library?.domain || detail.node?.domain || 'unknown' }}</button></dd>
                  <dt>Layer</dt><dd>{{ detail.node?.layer || 'unknown' }}</dd>
                  <dt>Risk</dt><dd><i class="dot" [style.background]="riskColor(detail.node?.risk)"></i>{{ detail.node?.risk || 'unknown' }}</dd>
                  <dt>Lines of code</dt><dd>{{ detail.node?.loc | number }}</dd>
                  <dt>Complexity</dt><dd>{{ detail.node?.complexity }} (cyclomatic {{ detail.node?.cyclomatic }})</dd>
                  <dt>Business rules</dt><dd>{{ detail.node?.rules }}</dd>
                  <dt>Security findings</dt><dd>{{ detail.node?.security?.['critical'] || 0 }} critical, {{ detail.node?.security?.['high'] || 0 }} high</dd>
                  <dt>Node id</dt><dd class="mono">{{ detail.node?.id }}</dd>
                </dl>
                <div class="btnrow"><button type="button" class="apm-btn sm" (click)="traverseFrom(detail.node!.id)">Traverse from here</button><button type="button" class="apm-btn outline sm" (click)="focusFile(detail.node!.id)">Focus in page graph</button></div>
                <p class="note" *ngIf="detail.loading">Loading details from the table…</p>
                <p class="purpose" *ngIf="!detail.loading">{{ detail.purpose || 'No purpose recorded.' }}</p>
                <ng-container *ngIf="!detail.loading">
                  <h6>Global Library cards<span *ngIf="detail.library?.found" class="ldg-hint" style="text-transform:none;letter-spacing:0;margin-left:6px">from {{ detail.library?.source === 'approved' ? 'approved build v' + detail.library?.version : detail.library?.source === 'latest' ? 'latest build' : 'uploaded module JSON' }}</span></h6>
                  <p class="note" *ngIf="!detail.library">No module JSON uploaded to the Global Library yet, so there are no cards for this page.</p>
                  <p class="note" *ngIf="detail.library && !detail.library.found">The library's module JSONs have no section for this page.</p>
                  <ng-container *ngIf="detail.library?.found">
                    <div class="ldg-chips" style="margin-bottom:6px"><span class="ldg-chip static" *ngFor="let k of cardKinds">{{ k }} <i>{{ detail.library?.counts?.[k] || 0 }}</i></span></div>
                    <div class="modgroup" *ngFor="let k of detail.cardKinds">
                      <div class="mg">{{ k }} · {{ cardKindLabel(k) }}</div>
                      <ul class="list"><li *ngFor="let c of detail.library?.cards?.[k]" [title]="c.summary || ''"><span class="mono" style="white-space:nowrap">{{ c.id }}</span><span class="n" style="white-space:normal;text-align:right;color:var(--text-primary)">{{ c.label }}</span></li></ul>
                    </div>
                    <div class="modgroup" *ngIf="detail.library?.shared_entities?.length">
                      <div class="mg">Shared entities</div>
                      <ul class="list"><li *ngFor="let c of detail.library?.shared_entities" [title]="c.summary || ''"><span class="mono">{{ c.id }}</span><span class="n" style="color:var(--text-primary)">{{ c.label }}</span></li></ul>
                    </div>
                  </ng-container>
                  <h6>References ({{ countRefs(detail.out) }})</h6>
                  <p class="note" *ngIf="!detail.out?.length">None</p>
                  <div class="modgroup" *ngFor="let g of detail.out"><div class="mg"><button type="button" class="ldg-link" (click)="select({ type: 'group', id: g.group })">{{ g.group }}</button></div>
                    <ul class="list"><li *ngFor="let f of g.items"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: f.id })">{{ f.file }}</button><span class="n">{{ f.n }}</span></li></ul></div>
                  <h6>Referenced by ({{ countRefs(detail.inc) }})</h6>
                  <p class="note" *ngIf="!detail.inc?.length">None</p>
                  <div class="modgroup" *ngFor="let g of detail.inc"><div class="mg"><button type="button" class="ldg-link" (click)="select({ type: 'group', id: g.group })">{{ g.group }}</button></div>
                    <ul class="list"><li *ngFor="let f of g.items"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: f.id })">{{ f.file }}</button><span class="n">{{ f.n }}</span></li></ul></div>
                  <h6>Tables ({{ detail.tables?.length }})</h6>
                  <div class="ldg-chips"><span class="ldg-chip static mono" *ngFor="let t of detail.tables">{{ t }}</span></div>
                  <h6>Shared includes ({{ detail.includes?.length }})</h6>
                  <div class="ldg-chips"><span class="ldg-chip static mono" *ngFor="let t of detail.includes">{{ t }}</span></div>
                </ng-container>
              </ng-container>

              <ng-container *ngSwitchCase="'walk'">
                <div class="kicker">Traversal</div><h5 class="mono">{{ detail.title }}</h5>
                <dl class="kv"><ng-container *ngFor="let r of detail.rows"><dt>{{ r[0] }}</dt><dd>{{ r[1] }}</dd></ng-container></dl>
                <h6>{{ groupBy }}s touched</h6>
                <ul class="list"><li *ngFor="let p of detail.touched"><button type="button" class="ldg-link" (click)="select({ type: 'group', id: p.a })">{{ p.a }}</button><span class="n">{{ p.c }}</span></li></ul>
                <ng-container *ngFor="let r of detail.rings"><h6>{{ r.depth }} hop{{ r.depth === 1 ? '' : 's' }} ({{ r.items.length }})</h6>
                  <ul class="list"><li *ngFor="let f of r.items"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: f.id })">{{ f.file }}</button><span class="n">{{ f.group }}</span></li></ul></ng-container>
              </ng-container>

              <ng-container *ngSwitchCase="'path'">
                <div class="kicker">Reference chain</div><h5 class="mono">{{ detail.title }}</h5>
                <p class="note" *ngIf="detail.note">{{ detail.note }}</p>
                <dl class="kv" *ngIf="detail.rows?.length"><ng-container *ngFor="let r of detail.rows"><dt>{{ r[0] }}</dt><dd>{{ r[1] }}</dd></ng-container></dl>
                <h6 *ngIf="detail.hops?.length">Hops</h6>
                <ul class="edgelist"><li *ngFor="let e of detail.hops"><button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: e.sId })">{{ e.sFile }}</button> → <button type="button" class="ldg-link mono" (click)="select({ type: 'file', id: e.tId })">{{ e.tFile }}</button><span class="ev">{{ kindLabel(e.label) }}<ng-container *ngIf="e.evidence"> · {{ e.evidence }}</ng-container></span></li></ul>
              </ng-container>
            </ng-container>
          </aside>
        </div>

        <datalist id="ldg-files"><option *ngFor="let n of graph.nodes" [value]="fileKey(n)"></option></datalist>
      </ng-container>
      </div>
    </div>
    <div #tip class="ldg-tip" hidden></div>
  `,
  styles: [`
    .ldg-head { flex-wrap: wrap; }
    .ldg-toggle { appearance: none; display: grid; place-items: center; width: 26px; height: 26px; border: 1px solid var(--border); border-radius: 6px; background: var(--card-bg); color: var(--text-secondary); font: inherit; font-size: 12px; cursor: pointer; }
    .ldg-toggle:hover { color: var(--text-heading); border-color: var(--text-secondary); }
    .ldg-headsum { font-size: 12px; color: var(--text-secondary); margin-left: 10px; }
    .ldg-verpick { display: inline-flex; align-items: center; gap: 6px; margin-left: 14px; font-size: 12px; color: var(--text-secondary); }
    .ldg-verpick select { font: inherit; color: var(--text-primary); background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 3px 6px; }
    .ldg-stats { display: flex; flex-wrap: wrap; gap: 6px 26px; padding: 12px 16px 0; }
    .ldg-stats div { display: flex; flex-direction: column; }
    .ldg-stats b { font-size: 20px; font-weight: 600; color: var(--text-heading); }
    .ldg-stats span { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--text-secondary); }
    .ldg-filters, .ldg-controls { display: flex; flex-wrap: wrap; gap: 8px 22px; align-items: center; padding: 10px 16px; color: var(--text-secondary); font-size: 13px; }
    .ldg-controls { padding: 0 0 10px; }
    .ldg-filters label, .ldg-controls label { display: inline-flex; align-items: center; gap: 6px; }
    .ldg-filters select, .ldg-filters input, .ldg-controls select, .ldg-controls input[type=search], .ldg-controls input[type=number] { font: inherit; color: var(--text-primary); background: var(--card-bg); border: 1px solid var(--border); border-radius: 6px; padding: 4px 8px; }
    .ldg-controls input[type=range] { width: 150px; accent-color: var(--aig-cobalt); }
    .ldg-hint { color: var(--text-secondary); font-size: 12px; }
    .ldg-tabs { display: flex; gap: 2px; margin: 4px 16px 0; border-bottom: 1px solid var(--border); overflow-x: auto; }
    .ldg-tabs button { appearance: none; background: none; border: 0; border-bottom: 2px solid transparent; margin-bottom: -1px; padding: 8px 12px; font: inherit; font-weight: 600; color: var(--text-secondary); cursor: pointer; white-space: nowrap; }
    .ldg-tabs button[aria-selected="true"] { color: var(--text-heading); border-bottom-color: var(--aig-cobalt); }
    .ldg-layout { display: grid; grid-template-columns: minmax(0, 1fr) 340px; gap: 16px; padding: 14px 16px 16px; align-items: start; }
    @media (max-width: 1100px) { .ldg-layout { grid-template-columns: minmax(0, 1fr); } }
    .ldg-layout.ldg-layout-full { grid-template-columns: minmax(0, 1fr); }
    .ldg-views { min-width: 0; }
    .ldg-lead { margin: 0 0 10px; max-width: 80ch; }
    .ldg-scroll { overflow-x: auto; }
    .ldg-svg { display: block; width: 100%; height: auto; font-family: var(--f-ui, Inter, sans-serif); }
    .ldg-svg text { fill: var(--text-secondary); }
    .ldg-graph { background: var(--panel-bg); border: 1px solid var(--border); border-radius: 8px; cursor: grab; }
    .ldg-legend { display: flex; flex-wrap: wrap; gap: 6px 16px; align-items: center; font-size: 12px; color: var(--text-secondary); margin-top: 8px; }
    .ldg-legend .sw { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 5px; vertical-align: -1px; }
    .ldg-ramp { display: inline-block; width: 120px; height: 10px; border-radius: 2px; background: linear-gradient(to right, #cde2fb, #3987e5, #0d366b); vertical-align: -1px; margin: 0 6px; }
    .ldg-h5 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--text-secondary); margin: 16px 0 6px; }
    .ldg-pairs { display: grid; gap: 4px; }
    .ldg-pairrow { display: grid; grid-template-columns: minmax(0, 1fr) 2fr 44px; gap: 10px; align-items: center; appearance: none; border: 0; background: none; padding: 3px 4px; font: inherit; color: var(--text-primary); text-align: left; cursor: pointer; border-radius: 4px; }
    .ldg-pairrow:hover { background: var(--panel-bg); }
    .ldg-pairrow .lbl { font-size: 12px; color: var(--text-secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .ldg-pairrow .barwrap { display: block; } .ldg-pairrow .bar { display: block; height: 10px; border-radius: 0 3px 3px 0; background: var(--aig-cobalt); }
    .ldg-pairrow .num { font-size: 12px; text-align: right; font-variant-numeric: tabular-nums; }
    .ldg-chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .ldg-chip { font: inherit; font-size: 12px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--border); background: var(--card-bg); color: var(--text-secondary); cursor: pointer; line-height: 1.3; }
    .ldg-chip[aria-pressed="true"] { background: #e6f3fb; border-color: var(--aig-cobalt); color: var(--text-heading); }
    .ldg-chip.static { cursor: default; } .ldg-chip i { font-style: normal; color: var(--text-secondary); margin-left: 4px; font-variant-numeric: tabular-nums; }
    .ldg-link { appearance: none; border: 0; background: none; padding: 0; font: inherit; color: var(--aig-cobalt); cursor: pointer; text-align: left; }
    .ldg-link:hover { text-decoration: underline; }
    .mono { font-family: ui-monospace, Consolas, "Courier New", monospace; font-size: 12.5px; }
    .ldg-table { font-size: 12.5px; } .ldg-table td.num, .ldg-table th.num { text-align: right; } .ldg-table td.ev { color: var(--text-secondary); max-width: 360px; }
    .ldg-detail { position: sticky; top: 70px; max-height: calc(100vh - 90px); overflow: auto; background: var(--panel-bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; min-width: 0; font-size: 13px; }
    .ldg-detail h5 { margin: 0 0 6px; font-size: 14.5px; font-weight: 700; color: var(--text-heading); word-break: break-word; }
    .ldg-detail h6 { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--text-secondary); margin: 14px 0 6px; }
    .ldg-detail .kicker { font-size: 11px; text-transform: uppercase; letter-spacing: .06em; color: var(--text-secondary); margin-bottom: 2px; }
    .ldg-detail .kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 14px; margin: 8px 0 0; } .ldg-detail .kv dt { color: var(--text-secondary); } .ldg-detail .kv dd { margin: 0; }
    .ldg-detail .note { color: var(--text-secondary); margin: 0 0 8px; } .ldg-detail .purpose { color: var(--text-secondary); margin-top: 10px; }
    .ldg-detail .list { list-style: none; margin: 0; padding: 0; display: grid; gap: 3px; } .ldg-detail .list li { display: flex; justify-content: space-between; gap: 10px; align-items: baseline; }
    .ldg-detail .list .n { color: var(--text-secondary); white-space: nowrap; font-variant-numeric: tabular-nums; }
    .ldg-detail .edgelist { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; } .ldg-detail .edgelist li { padding-left: 8px; border-left: 2px solid var(--border); }
    .ldg-detail .ev { display: block; color: var(--text-secondary); font-size: 12px; margin-top: 1px; }
    .ldg-detail .modgroup { margin-top: 6px; } .ldg-detail .mg { font-size: 12px; color: var(--text-secondary); margin-bottom: 2px; }
    .ldg-detail .btnrow { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
    .ldg-detail .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
    .ldg-tip { position: fixed; z-index: 50; pointer-events: none; background: #0f1c2e; color: #f9f9f7; padding: 7px 10px; border-radius: 6px; font-size: 12px; max-width: 320px; box-shadow: 0 4px 14px rgba(0,0,0,.18); line-height: 1.4; }
    .ldg-tip .t { font-weight: 600; font-family: ui-monospace, Consolas, monospace; word-break: break-all; } .ldg-tip .k { color: #c3c2b7; } .ldg-tip .v { font-weight: 600; }
    /* svg marks */
    .ldg-cell { stroke: var(--card-bg); stroke-width: 2; cursor: pointer; } .ldg-cell:hover { stroke: var(--text-heading); }
    .ldg-cell-label { font-size: 10px; pointer-events: none; } .ldg-cell-label.hi { fill: #fff; } .ldg-cell-label.lo { fill: var(--text-heading); } .ldg-cell-label.diag { fill: var(--text-secondary); }
    .ldg-rowlab, .ldg-collab { font-size: 12px; cursor: pointer; } .ldg-rowlab:hover, .ldg-collab:hover { fill: var(--aig-cobalt); }
    .ldg-total { font-size: 10px; fill: var(--text-secondary); } .ldg-axis { font-size: 11px; fill: var(--text-secondary); text-transform: uppercase; letter-spacing: .06em; }
    .ldg-medge { fill: none; stroke: #898781; stroke-opacity: .45; cursor: pointer; } .ldg-medge.on { stroke: var(--aig-cobalt); stroke-opacity: .95; } .ldg-medge.dim { stroke-opacity: .07; }
    .ldg-mnode circle { fill: var(--aig-cobalt); stroke: var(--card-bg); stroke-width: 2; cursor: pointer; } .ldg-mnode.dim circle { fill-opacity: .25; } .ldg-mnode text { font-size: 11.5px; pointer-events: none; } .ldg-mnode.dim text { fill-opacity: .35; }
    .ldg-hull { fill: var(--text-heading); fill-opacity: .035; stroke: var(--text-heading); stroke-opacity: .07; stroke-width: 30; stroke-linejoin: round; }
    .ldg-hull-label { font-size: 11px; font-weight: 600; fill: var(--text-secondary); letter-spacing: .05em; text-transform: uppercase; pointer-events: none; }
    .ldg-edge { stroke: #898781; stroke-opacity: .4; stroke-width: 1; } .ldg-edge.intra { stroke: #c3c2b7; stroke-opacity: .7; } .ldg-edge.on { stroke: var(--aig-cobalt); stroke-opacity: .95; stroke-width: 1.6; } .ldg-edge.dim { stroke-opacity: .06; } .ldg-edge.faint { stroke-opacity: .14; }
    .ldg-node { stroke: var(--card-bg); stroke-width: 1.5; cursor: pointer; } .ldg-node.out { fill-opacity: .5; } .ldg-node.sel { stroke: var(--text-heading); stroke-width: 2.5; } .ldg-node.dim { fill-opacity: .12; } .ldg-node.start { stroke: var(--text-heading); stroke-width: 3; }
    .ldg-flabel { font: 10px ui-monospace, Consolas, monospace; fill: var(--text-primary); paint-order: stroke; stroke: var(--panel-bg); stroke-width: 3px; stroke-linejoin: round; pointer-events: none; }
    .ldg-arrow { fill: #898781; } .ldg-arrow-intra { fill: #c3c2b7; } .ldg-arrow-on { fill: var(--aig-cobalt); }
    .ldg-ring { fill: none; stroke: var(--border); } .ldg-ring-label { font-size: 10px; fill: var(--text-secondary); text-transform: uppercase; letter-spacing: .05em; }
    .ldg-hop-label { font-size: 10px; fill: var(--text-secondary); } .ldg-hop-name { font: 11px ui-monospace, Consolas, monospace; fill: var(--text-heading); }
    .ldg-empty { fill: var(--text-secondary); font-size: 14px; }
  `],
})
export class LibraryDependencyGraphComponent implements OnChanges, AfterViewInit, OnDestroy {
  @Input() gearId = "";
  /** Bump to reload after an upload. */
  @Input() reloadKey = 0;
  /** Project mode: show only this project's uploaded ASP files (solid) and the pages they connect to (faded). */
  @Input() projectId: string | null = null;

  readonly cardKinds = CARD_KINDS;
  projectGraph: ProjectAspGraph | null = null;
  get projectPages(): ProjectAspGraph["pages"] { return this.projectGraph?.pages ?? []; }
  get visibleTabs(): { key: Tab; label: string }[] {
    const base = this.tabs.filter((t) => t.key !== "project");
    return this.projectId ? [{ key: "project", label: "Project pages" }, ...base] : base;
  }
  cardKindLabel(k: string): string {
    return ({ BR: "Business rules", FR: "Functional requirements", SCR: "Screens", CMP: "Components / services", API: "APIs", ENT: "Entities", WF: "Workflows" } as Record<string, string>)[k] ?? k;
  }

  @ViewChild("matrixSvg") private matrixSvg?: ElementRef<SVGSVGElement>;
  @ViewChild("modSvg") private modSvg?: ElementRef<SVGSVGElement>;
  @ViewChild("fileSvg") private fileSvg?: ElementRef<SVGSVGElement>;
  @ViewChild("travSvg") private travSvg?: ElementRef<SVGSVGElement>;
  @ViewChild("tip") private tipRef?: ElementRef<HTMLDivElement>;

  readonly tabs: { key: Tab; label: string }[] = [
    { key: "matrix", label: "Module matrix" }, { key: "modgraph", label: "Module graph" }, { key: "files", label: "Page graph" },
    { key: "trav", label: "Traverse" }, { key: "table", label: "Reference table" }, { key: "versions", label: "Versions" },
  ];

  graph: LibraryGraph | null = null;
  loading = false;
  /** All dependency-graph versions of the Gear ID (oldest first) and the one being viewed. */
  versions: VersionRow[] = [];
  selectedVersion: string | null = null;
  activating = false;
  /** Card collapsed state; remembered per browser so the page opens the way it was left. */
  collapsed = false;
  private static readonly COLLAPSED_KEY = "apm.library-dependency-graph.collapsed";
  tab: Tab = "matrix";
  groupBy: GroupBy = "module";
  kind: "all" | "DEPENDS_ON" | "TRIGGERS" = "all";
  hubInput: number | null = null;
  minMod = 5;
  modMinMax = 50;
  neighbours = true;
  hideIntra = false;
  colorBy: "layer" | "risk" = "layer";
  search = "";
  tblFilter = "";
  travStartText = "";
  travToText = "";
  travDir: "down" | "up" | "both" = "down";
  travDepth = 2;
  fileCount = "";
  travCount = "";
  matrixMax = 0;

  byId = new Map<string, LibraryGraphNode>();
  groups: string[] = [];
  groupInfo = new Map<string, { files: number; loc: number }>();
  scope = new Set<string>();
  E: LibraryGraphEdge[] = [];
  hubs: string[] = [];
  deg = new Map<string, number>();
  outE = new Map<string, LibraryGraphEdge[]>();
  inE = new Map<string, LibraryGraphEdge[]>();
  pairMap = new Map<string, LibraryGraphEdge[]>();
  pairList: Pair[] = [];
  topPairs: Pair[] = [];
  crossCount = 0;
  selected: Selection | null = null;
  fileSel: string | null = null;
  travStart: string | null = null;
  travTo: string | null = null;
  detail: Detail = { kind: "default" };
  private viewReady = false;
  private sim?: d3.Simulation<any, any>;
  private fileHighlight?: () => void;
  private rLoc: (loc: number) => number = () => 5;

  constructor(private readonly svc: AidlcPlatformMgmtService, private readonly zone: NgZone, private readonly cdr: ChangeDetectorRef) {
    try { this.collapsed = localStorage.getItem(LibraryDependencyGraphComponent.COLLAPSED_KEY) === "1"; } catch { /* storage unavailable: stay expanded */ }
  }

  toggleCollapsed(): void {
    this.collapsed = !this.collapsed;
    try { localStorage.setItem(LibraryDependencyGraphComponent.COLLAPSED_KEY, this.collapsed ? "1" : "0"); } catch { /* ignore */ }
    // Re-run the force layout when the card comes back into view so it settles on screen.
    if (!this.collapsed && this.graph && this.viewReady) this.renderFileGraph();
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes["projectId"] && this.projectId && this.tab !== "project") this.tab = "project";
    if (changes["gearId"] || changes["reloadKey"] || changes["projectId"]) this.load();
  }
  ngAfterViewInit(): void { this.viewReady = true; if (this.graph) this.renderAll(); }
  ngOnDestroy(): void { this.sim?.stop(); }

  // ── data ─────────────────────────────────────────────────────────────────
  get versionsNewestFirst() { return this.versions.slice().reverse(); }
  get currentVersionNumber(): number | null { return this.versions.find((v) => v.is_current)?.version ?? null; }

  /** Reload the version list, then the graph of the selected (default: current) version. */
  load(): void {
    if (!this.gearId && !this.projectId) { this.graph = null; this.versions = []; return; }
    this.loading = true;
    this.svc.libraryGraphVersions(this.gearId).subscribe((vs) => {
      this.versions = (vs?.items ?? []) as VersionRow[];
      const known = this.versions.some((v) => v.kb_version === this.selectedVersion);
      if (!known) this.selectedVersion = vs?.current ?? vs?.latest ?? null;
      this.loadGraph();
    });
  }

  /** Make a version the current one for this Gear ID, then show it. */
  activate(kbVersion: string): void {
    if (!this.gearId || this.activating) return;
    this.activating = true;
    this.svc.activateLibraryGraphVersion(this.gearId, kbVersion).subscribe((r) => {
      this.activating = false;
      if (r) this.selectedVersion = r.current;
      this.load();
    });
  }

  private loadGraph(): void {
    if (this.projectId) {
      this.svc.projectAspGraph(this.projectId, this.gearId, this.selectedVersion).subscribe((pg) => this.applyGraph(this.fromProject(pg)));
      return;
    }
    this.svc.libraryGraph(this.gearId, this.selectedVersion).subscribe((g) => this.applyGraph(g));
  }

  /** Shape a project subgraph like the library graph so every view works unchanged. */
  private fromProject(pg: ProjectAspGraph | null): LibraryGraph | null {
    this.projectGraph = pg;
    if (!pg) return null;
    return { gear_id: pg.gear_id, kb_version: pg.kb_version, version: pg.version, is_current: pg.is_current,
             built_at: pg.built_at, nodes: [...pg.pages, ...pg.neighbours], edges: pg.edges, modules: pg.modules, pairs: pg.pairs };
  }

  private applyGraph(g: LibraryGraph | null): void {
    {
      this.loading = false;
      this.graph = g && g.nodes?.length ? g : null;
      if (this.graph) this.selectedVersion = this.graph.kb_version;
      this.selected = null; this.fileSel = null; this.travStart = null; this.travTo = null;
      this.travStartText = ""; this.travToText = "";
      if (this.graph) {
        this.byId = new Map(this.graph.nodes.map((n) => [n.id, n]));
        this.inProject = new Set((this.projectGraph?.pages ?? []).map((p) => p.id));
        if (this.projectId) this.scope = new Set(this.projectPages.map((p) => this.keyOf(p)));
        else { const busiest = this.rankGroups()[0]; this.scope = new Set(busiest ? [busiest] : []); }
        this.rLoc = d3.scaleSqrt().domain([0, d3.max(this.graph.nodes, (n) => n.loc) || 1]).range([3.5, 15]) as any;
      }
      this.cdr.detectChanges();
      if (this.graph) this.renderAll();
    }
  }
  /** Ids of the project's own pages in project mode (drawn solid; everything else faded). */
  inProject = new Set<string>();

  keyOf(n: LibraryGraphNode): string { return this.groupBy === "module" ? (n.module || "(root)") : (n.domain || "unknown"); }
  fileKey(n: LibraryGraphNode): string { return `${n.file} [${n.module}]`; }
  kindLabel(l: string): string { return KIND_LABEL[l] ?? l; }
  riskColor(r?: string | null): string { return RISK_COLOR[r ?? ""] ?? OTHER_COLOR; }
  countRefs(g?: GroupedRefs[]): number { return (g ?? []).reduce((s, x) => s + x.items.length, 0); }
  get legend(): [string, string][] {
    return this.colorBy === "layer"
      ? [["UI", LAYER_COLOR["UI"]], ["Business Logic", LAYER_COLOR["Business Logic"]], ["Data Access", LAYER_COLOR["Data Access"]], ["Other", OTHER_COLOR]]
      : [["Low risk", RISK_COLOR["low"]], ["Medium risk", RISK_COLOR["medium"]], ["High risk", RISK_COLOR["high"]]];
  }
  private rankGroups(): string[] {
    const c = new Map<string, number>();
    this.graph?.nodes.forEach((n) => c.set(this.keyOf(n), (c.get(this.keyOf(n)) ?? 0) + 1));
    return [...c.entries()].sort((a, b) => b[1] - a[1]).map((e) => e[0]);
  }
  private resolveFile(text: string): LibraryGraphNode | null {
    const v = (text || "").trim(); if (!v || !this.graph) return null;
    const low = v.toLowerCase();
    return this.graph.nodes.find((n) => this.fileKey(n) === v) || this.graph.nodes.find((n) => n.file.toLowerCase() === low)
      || this.graph.nodes.find((n) => n.file.toLowerCase().includes(low)) || null;
  }

  private recompute(): void {
    const g = this.graph; if (!g) return;
    this.groupInfo = new Map(); this.scope = new Set([...this.scope]);
    g.nodes.forEach((n) => { const k = this.keyOf(n); const m = this.groupInfo.get(k) ?? { files: 0, loc: 0 }; m.files++; m.loc += n.loc || 0; this.groupInfo.set(k, m); });
    this.groups = [...this.groupInfo.keys()].sort();
    if (![...this.scope].some((s) => this.groupInfo.has(s))) { const first = this.rankGroups()[0]; this.scope = new Set(first ? [first] : []); }
    const base = g.edges.filter((e) => this.kind === "all" || e.label === this.kind);
    const d = new Map<string, number>();
    base.forEach((e) => { d.set(e.source, (d.get(e.source) ?? 0) + 1); d.set(e.target, (d.get(e.target) ?? 0) + 1); });
    const hub = this.hubInput && this.hubInput > 0 ? this.hubInput : null;
    this.hubs = hub ? g.nodes.filter((n) => (d.get(n.id) ?? 0) >= hub).map((n) => n.id).sort((a, b) => (d.get(b) ?? 0) - (d.get(a) ?? 0)) : [];
    const hubSet = new Set(this.hubs);
    this.E = base.filter((e) => !hubSet.has(e.source) && !hubSet.has(e.target));
    this.deg = new Map(); this.outE = new Map(); this.inE = new Map(); this.pairMap = new Map();
    const push = (m: Map<string, LibraryGraphEdge[]>, k: string, v: LibraryGraphEdge) => { if (!m.has(k)) m.set(k, []); m.get(k)!.push(v); };
    this.E.forEach((e) => {
      this.deg.set(e.source, (this.deg.get(e.source) ?? 0) + 1); this.deg.set(e.target, (this.deg.get(e.target) ?? 0) + 1);
      push(this.outE, e.source, e); push(this.inE, e.target, e);
      push(this.pairMap, this.keyOf(this.byId.get(e.source)!) + "|" + this.keyOf(this.byId.get(e.target)!), e);
    });
    this.pairList = [...this.pairMap.entries()].map(([k, es]) => { const [a, b] = k.split("|"); return { a, b, c: es.length }; });
    this.crossCount = this.pairList.filter((p) => p.a !== p.b).reduce((s, p) => s + p.c, 0);
    this.topPairs = this.pairList.filter((p) => p.a !== p.b).sort((x, y) => y.c - x.c).slice(0, 12);
    this.modMinMax = Math.max(1, d3.max(this.pairList.filter((p) => p.a !== p.b), (p) => p.c) ?? 1);
    if (this.minMod > this.modMinMax) this.minMod = Math.max(1, Math.min(5, this.modMinMax));
  }
  pairEdges(a: string, b: string): LibraryGraphEdge[] { return this.pairMap.get(a + "|" + b) ?? []; }
  pairCount(a: string, b: string): number { return this.pairEdges(a, b).length; }

  renderAll(): void {
    if (!this.graph) return;
    this.recompute();
    this.renderDetail();
    if (!this.viewReady) return;
    this.zone.runOutsideAngular(() => { this.renderMatrix(); this.renderModGraph(); this.renderFileGraph(); });
    this.runTraverse(false);
  }
  switchTab(t: Tab): void { this.tab = t; }

  // ── tooltip ──────────────────────────────────────────────────────────────
  private showTip(ev: MouseEvent, title: string, rows: [string, string][]): void {
    const el = this.tipRef?.nativeElement; if (!el) return;
    el.replaceChildren();
    const t = document.createElement("div"); t.className = "t"; t.textContent = title; el.appendChild(t);
    rows.forEach(([k, v]) => { const r = document.createElement("div"); const kk = document.createElement("span"); kk.className = "k"; kk.textContent = k + " "; const vv = document.createElement("span"); vv.className = "v"; vv.textContent = v; r.append(kk, vv); el.appendChild(r); });
    el.hidden = false; this.moveTip(ev);
  }
  private moveTip = (ev: MouseEvent): void => {
    const el = this.tipRef?.nativeElement; if (!el) return;
    const pad = 14, w = el.offsetWidth, h = el.offsetHeight;
    let x = ev.clientX + pad, y = ev.clientY + pad;
    if (x + w > window.innerWidth - 8) x = ev.clientX - w - pad;
    if (y + h > window.innerHeight - 8) y = ev.clientY - h - pad;
    el.style.left = x + "px"; el.style.top = y + "px";
  };
  private hideTip = (): void => { const el = this.tipRef?.nativeElement; if (el) el.hidden = true; };

  // ── matrix ───────────────────────────────────────────────────────────────
  private renderMatrix(): void {
    const el = this.matrixSvg?.nativeElement; if (!el) return;
    const MODS = this.groups, n = MODS.length, cell = 30, left = 196, top = 150, right = 54, bottom = 34;
    const W = left + n * cell + right, H = top + n * cell + bottom;
    const svg = d3.select(el).attr("viewBox", `0 0 ${W} ${H}`); svg.selectAll("*").remove();
    if (!n) return;
    const counts = MODS.map((a) => MODS.map((b) => this.pairCount(a, b)));
    const max = d3.max(MODS.flatMap((a, i) => MODS.map((b, j) => (i === j ? 0 : counts[i][j])))) || 1;
    this.matrixMax = max;
    const g = svg.append("g").attr("transform", `translate(${left},${top})`);
    const seq = d3.interpolateRgb("#cde2fb", "#0d366b");
    g.append("text").attr("class", "ldg-axis").attr("x", -10).attr("y", -12).attr("text-anchor", "end").text("references from");
    g.append("text").attr("class", "ldg-axis").attr("transform", `translate(${n * cell / 2},${n * cell + 28})`).attr("text-anchor", "middle").text("references to");
    const pick = (d: any) => this.zone.run(() => (d.i === d.j ? this.select({ type: "group", id: d.a }) : this.select({ type: "pair", a: d.a, b: d.b })));
    g.selectAll(".ldg-collab").data(MODS).join("text").attr("class", "ldg-collab").attr("transform", (d, j) => `translate(${j * cell + cell / 2 + 4},-8) rotate(-62)`).attr("text-anchor", "start").text((d) => d)
      .on("click", (ev, d) => this.zone.run(() => this.select({ type: "group", id: d })));
    g.selectAll(".ldg-rowlab").data(MODS).join("text").attr("class", "ldg-rowlab").attr("x", -10).attr("y", (d, i) => i * cell + cell / 2 + 4).attr("text-anchor", "end").text((d) => d)
      .on("click", (ev, d) => this.zone.run(() => this.select({ type: "group", id: d })));
    const cells: any[] = []; MODS.forEach((a, i) => MODS.forEach((b, j) => cells.push({ a, b, i, j, c: counts[i][j] })));
    g.selectAll(".ldg-cell").data(cells).join("rect").attr("class", "ldg-cell")
      .attr("x", (d) => d.j * cell).attr("y", (d) => d.i * cell).attr("width", cell).attr("height", cell).attr("rx", 3)
      .attr("fill", (d) => (d.i === d.j || d.c === 0 ? "#f1f0ec" : seq(0.15 + 0.85 * d.c / max)))
      .on("mouseenter", (ev, d) => this.showTip(ev, d.i === d.j ? d.a : `${d.a} → ${d.b}`, [[d.i === d.j ? "inside" : "references", String(d.c)]]))
      .on("mousemove", this.moveTip).on("mouseleave", this.hideTip).on("click", (ev, d) => pick(d));
    g.selectAll(".ldg-cell-label").data(cells.filter((d) => d.c > 0 && (d.i === d.j || d.c >= max * 0.25))).join("text")
      .attr("class", (d) => "ldg-cell-label " + (d.i === d.j ? "diag" : d.c / max >= 0.55 ? "hi" : "lo"))
      .attr("x", (d) => d.j * cell + cell / 2).attr("y", (d) => d.i * cell + cell / 2 + 3.5).attr("text-anchor", "middle").text((d) => d.c);
    g.selectAll(".rowtot").data(MODS).join("text").attr("class", "ldg-total").attr("x", n * cell + 8).attr("y", (d, i) => i * cell + cell / 2 + 3.5).text((d, i) => counts[i].reduce((s, c, j) => s + (j === i ? 0 : c), 0));
    g.append("text").attr("class", "ldg-axis").attr("x", n * cell + 8).attr("y", -12).text("out");
    g.selectAll(".coltot").data(MODS).join("text").attr("class", "ldg-total").attr("x", (d, j) => j * cell + cell / 2).attr("y", n * cell + 12).attr("text-anchor", "middle").text((d, j) => counts.reduce((s, row, i) => s + (i === j ? 0 : row[j]), 0));
    g.append("text").attr("class", "ldg-axis").attr("x", -10).attr("y", n * cell + 12).attr("text-anchor", "end").text("in");
  }

  // ── module graph ─────────────────────────────────────────────────────────
  private addMarkers(defs: any, specs: [string, string, number, number][]): void {
    specs.forEach(([id, cls, ref, size]) => defs.append("marker").attr("id", id).attr("viewBox", "0 0 10 10").attr("refX", ref).attr("refY", 5).attr("markerWidth", size).attr("markerHeight", size).attr("markerUnits", "userSpaceOnUse").attr("orient", "auto").append("path").attr("class", cls).attr("d", "M0,1.5 L8,5 L0,8.5 Z"));
  }
  renderModGraph(): void {
    const el = this.modSvg?.nativeElement; if (!el || !this.graph) return;
    const MODS = this.groups, W = 780, H = 720, cx = W / 2, cy = H / 2, R = 262;
    const svg = d3.select(el).attr("viewBox", `0 0 ${W} ${H}`); svg.selectAll("*").remove();
    if (!MODS.length) return;
    this.addMarkers(svg.append("defs"), [["ldg-marr", "ldg-arrow", 9, 9], ["ldg-marr-on", "ldg-arrow-on", 9, 9]]);
    const rS = d3.scaleSqrt().domain([0, d3.max(MODS, (m) => this.groupInfo.get(m)!.files) || 1]).range([6, 26]);
    const pos = new Map(MODS.map((m, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / MODS.length; return [m, { x: cx + R * Math.cos(a), y: cy + R * Math.sin(a), a, r: rS(this.groupInfo.get(m)!.files) }]; }));
    const cross = this.pairList.filter((p) => p.a !== p.b), maxAll = d3.max(cross, (p) => p.c) || 1;
    const shown = cross.filter((p) => p.c >= this.minMod), wS = d3.scaleSqrt().domain([1, maxAll]).range([1, 9]);
    const path = (p: Pair) => { const s = pos.get(p.a)!, t = pos.get(p.b)!; const mx = (s.x + t.x) / 2, my = (s.y + t.y) / 2, dx = t.x - s.x, dy = t.y - s.y, L = Math.hypot(dx, dy) || 1, nx = -dy / L, ny = dx / L; const qx = mx + (cx - mx) * 0.35 + nx * 20, qy = my + (cy - my) * 0.35 + ny * 20; const sdx = qx - s.x, sdy = qy - s.y, sl = Math.hypot(sdx, sdy) || 1, tdx = qx - t.x, tdy = qy - t.y, tl = Math.hypot(tdx, tdy) || 1; return `M${s.x + sdx / sl * s.r},${s.y + sdy / sl * s.r} Q${qx},${qy} ${t.x + tdx / tl * (t.r + 4)},${t.y + tdy / tl * (t.r + 4)}`; };
    const edges = svg.append("g").selectAll("path").data(shown).join("path").attr("class", "ldg-medge").attr("d", path).attr("stroke-width", (p) => wS(p.c)).attr("marker-end", "url(#ldg-marr)")
      .on("mouseenter", (ev, p) => { edges.classed("dim", (q) => q !== p); d3.select(ev.currentTarget as any).classed("on", true).attr("marker-end", "url(#ldg-marr-on)").raise(); this.showTip(ev, `${p.a} → ${p.b}`, [["references", String(p.c)], ["reverse", String(this.pairCount(p.b, p.a))]]); })
      .on("mousemove", this.moveTip).on("mouseleave", (ev) => { edges.classed("dim", false); d3.select(ev.currentTarget as any).classed("on", false).attr("marker-end", "url(#ldg-marr)"); this.hideTip(); })
      .on("click", (ev, p) => this.zone.run(() => this.select({ type: "pair", a: p.a, b: p.b })));
    const nodeG = svg.append("g").selectAll("g").data(MODS).join("g").attr("class", "ldg-mnode").attr("transform", (m) => { const p = pos.get(m)!; return `translate(${p.x},${p.y})`; })
      .on("mouseenter", (ev, m) => { edges.classed("dim", (p) => p.a !== m && p.b !== m).classed("on", (p) => p.a === m || p.b === m).attr("marker-end", (p) => (p.a === m || p.b === m ? "url(#ldg-marr-on)" : "url(#ldg-marr)")); nodeG.classed("dim", (o) => o !== m && !shown.some((p) => (p.a === m && p.b === o) || (p.b === m && p.a === o))); const outC = cross.filter((p) => p.a === m).reduce((s, p) => s + p.c, 0), inC = cross.filter((p) => p.b === m).reduce((s, p) => s + p.c, 0); this.showTip(ev, m, [["pages", String(this.groupInfo.get(m)!.files)], ["references out", String(outC)], ["references in", String(inC)], ["inside", String(this.pairCount(m, m))]]); })
      .on("mousemove", this.moveTip).on("mouseleave", () => { edges.classed("dim", false).classed("on", false).attr("marker-end", "url(#ldg-marr)"); nodeG.classed("dim", false); this.hideTip(); })
      .on("click", (ev, m) => this.zone.run(() => this.select({ type: "group", id: m })));
    nodeG.append("circle").attr("r", (m) => pos.get(m)!.r);
    nodeG.append("text").text((m) => m).attr("x", (m) => { const p = pos.get(m)!; return Math.cos(p.a) * (p.r + 10); }).attr("y", (m) => { const p = pos.get(m)!; return Math.sin(p.a) * (p.r + 10) + 4; })
      .attr("text-anchor", (m) => { const c = Math.cos(pos.get(m)!.a); return c > 0.15 ? "start" : c < -0.15 ? "end" : "middle"; }).attr("dy", (m) => { const s = Math.sin(pos.get(m)!.a); return s < -0.85 ? -8 : s > 0.85 ? 8 : 0; });
  }

  // ── page graph ───────────────────────────────────────────────────────────
  setScope(groups: string[]): void { this.scope = new Set(groups); this.renderFileGraph(); }
  toggleScope(g: string): void { if (this.scope.has(g)) this.scope.delete(g); else this.scope.add(g); this.renderFileGraph(); }
  findPage(): void { const hit = this.resolveFile(this.search); if (hit) { this.focusFile(hit.id); this.search = ""; } }
  focusFile(id: string): void {
    const n = this.byId.get(id); if (!n) return;
    if (!this.scope.has(this.keyOf(n))) this.scope.add(this.keyOf(n));
    this.fileSel = id; this.tab = "files"; this.renderFileGraph(); this.select({ type: "file", id });
  }
  showGroupInGraph(g: string): void { this.scope = new Set([g]); this.neighbours = true; this.hideIntra = false; this.tab = "files"; this.renderFileGraph(); }
  openPairInGraph(): void { const s = this.selected; if (!s || s.type !== "pair") return; this.scope = new Set([s.a!, s.b!]); this.neighbours = false; this.hideIntra = s.a !== s.b; this.tab = "files"; this.renderFileGraph(); }
  private nodeFill = (n: LibraryGraphNode): string => this.colorBy === "layer" ? (LAYER_COLOR[n.layer ?? ""] ?? OTHER_COLOR) : (RISK_COLOR[n.risk ?? ""] ?? OTHER_COLOR);

  renderFileGraph(): void {
    const el = this.fileSvg?.nativeElement; if (!el || !this.graph) return;
    this.zone.runOutsideAngular(() => {
      const inScope = new Set(this.graph!.nodes.filter((n) => this.scope.has(this.keyOf(n))).map((n) => n.id));
      const shownIds = new Set(inScope);
      if (this.neighbours) this.E.forEach((e) => { if (inScope.has(e.source)) shownIds.add(e.target); if (inScope.has(e.target)) shownIds.add(e.source); });
      let edges = this.E.filter((e) => shownIds.has(e.source) && shownIds.has(e.target) && (inScope.has(e.source) || inScope.has(e.target)));
      if (this.hideIntra) edges = edges.filter((e) => this.keyOf(this.byId.get(e.source)!) !== this.keyOf(this.byId.get(e.target)!));
      const shown = [...shownIds].map((id) => this.byId.get(id)!);
      this.sim?.stop();
      const W = 980, H = 680;
      const svg = d3.select(el).attr("viewBox", `0 0 ${W} ${H}`); svg.selectAll("*").remove(); svg.on(".zoom", null);
      this.fileCount = shown.length ? `${shown.length} pages and ${edges.length} references shown.` : "";
      if (!shown.length) { svg.append("text").attr("class", "ldg-empty").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").text("Pick at least one group above."); this.fileHighlight = undefined; return; }
      this.addMarkers(svg.append("defs"), [["ldg-arr", "ldg-arrow", 8, 7], ["ldg-arr-intra", "ldg-arrow-intra", 8, 7], ["ldg-arr-on", "ldg-arrow-on", 8, 7]]);
      const root = svg.append("g");
      svg.call(d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.3, 6]).on("zoom", (ev) => root.attr("transform", ev.transform.toString())));
      const fnodes: any[] = shown.map((n) => ({ id: n.id, n, r: this.rLoc(n.loc), inScope: inScope.has(n.id) && (!this.projectId || this.inProject.has(n.id)) })); const idx = new Map(fnodes.map((d) => [d.id, d]));
      const flinks: any[] = edges.map((e) => ({ source: idx.get(e.source), target: idx.get(e.target), e, intra: this.keyOf(this.byId.get(e.source)!) === this.keyOf(this.byId.get(e.target)!) }));
      const modsShown = [...new Set(shown.map((n) => this.keyOf(n)))].sort(); const k = modsShown.length, CR = k === 1 ? 0 : Math.min(W, H) * 0.34;
      const center = new Map(modsShown.map((m, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / k; return [m, { x: W / 2 + CR * Math.cos(a), y: H / 2 + CR * Math.sin(a) }]; }));
      const rnd = d3.randomLcg(42); fnodes.forEach((d) => { const c = center.get(this.keyOf(d.n))!; d.x = c.x + (rnd() - 0.5) * 80; d.y = c.y + (rnd() - 0.5) * 80; });
      const localDeg = new Map<string, number>(), adj = new Map<string, string[]>();
      flinks.forEach((l) => { localDeg.set(l.source.id, (localDeg.get(l.source.id) ?? 0) + 1); localDeg.set(l.target.id, (localDeg.get(l.target.id) ?? 0) + 1); (adj.get(l.source.id) ?? adj.set(l.source.id, []).get(l.source.id)!).push(l.target.id); (adj.get(l.target.id) ?? adj.set(l.target.id, []).get(l.target.id)!).push(l.source.id); });
      const sim = d3.forceSimulation(fnodes).force("link", d3.forceLink(flinks).id((d: any) => d.id).distance(36).strength(0.25)).force("charge", d3.forceManyBody().strength(-75).distanceMax(280)).force("collide", d3.forceCollide((d: any) => d.r + 3)).force("x", d3.forceX((d: any) => center.get(this.keyOf(d.n))!.x).strength(0.15)).force("y", d3.forceY((d: any) => center.get(this.keyOf(d.n))!.y).strength(0.15)).stop();
      this.sim = sim;
      const hullG = root.append("g"), linkG = root.append("g"), nodeG = root.append("g"), labelG = root.append("g");
      const hull = hullG.selectAll("path").data(modsShown).join("path").attr("class", "ldg-hull");
      const hullLab = hullG.selectAll("text").data(modsShown).join("text").attr("class", "ldg-hull-label").attr("text-anchor", "middle").text((d) => d);
      const markerFor = (l: any) => (l.intra ? "url(#ldg-arr-intra)" : "url(#ldg-arr)");
      const link = linkG.selectAll("line").data(flinks).join("line").attr("class", (l) => "ldg-edge" + (l.intra ? " intra" : "")).attr("marker-end", markerFor);
      const showAllLabels = fnodes.length <= 90;
      const nodeClass = (d: any) => "ldg-node" + (d.inScope ? "" : " out") + (d.id === this.fileSel ? " sel" : "");
      const node = nodeG.selectAll("circle").data(fnodes).join("circle").attr("class", nodeClass).attr("r", (d) => d.r).attr("fill", (d) => this.nodeFill(d.n))
        .on("mouseenter", (ev, d) => { const nb = new Set(adj.get(d.id) ?? []); node.classed("dim", (o) => o !== d && !nb.has(o.id)); link.classed("on", (l) => l.source === d || l.target === d).classed("dim", (l) => !(l.source === d || l.target === d)).attr("marker-end", (l) => (l.source === d || l.target === d ? "url(#ldg-arr-on)" : markerFor(l))); this.showTip(ev, d.n.file, [["module", d.n.module], ["domain", d.n.domain ?? "unknown"], ["layer", d.n.layer ?? "unknown"], ["risk", d.n.risk ?? "unknown"], ["lines", String(d.n.loc)], ["references out / in", `${(this.outE.get(d.id) ?? []).length} / ${(this.inE.get(d.id) ?? []).length}`]]); })
        .on("mousemove", this.moveTip).on("mouseleave", () => { node.classed("dim", false); link.classed("on", false).classed("dim", false).attr("marker-end", markerFor); this.hideTip(); applyHighlight(); })
        .on("click", (ev, d) => { ev.stopPropagation(); this.zone.run(() => this.select({ type: "file", id: d.id })); });
      const label = labelG.selectAll("text").data(fnodes.filter((d) => showAllLabels || (localDeg.get(d.id) ?? 0) >= 6 || d.id === this.fileSel || d.r >= 11)).join("text").attr("class", "ldg-flabel").text((d) => d.n.file);
      const applyHighlight = () => { const s = this.selected; if (s && s.type === "pair") { const hit = (l: any) => !l.intra && ((this.keyOf(l.source.n) === s.a && this.keyOf(l.target.n) === s.b) || (this.keyOf(l.source.n) === s.b && this.keyOf(l.target.n) === s.a)); link.classed("on", hit).attr("marker-end", (l) => (hit(l) ? "url(#ldg-arr-on)" : markerFor(l))); } node.classed("sel", (d) => d.id === this.fileSel); };
      const tick = () => {
        link.each((l: any, i: number, els: ArrayLike<any>) => { const el = els[i] as SVGLineElement; const dx = l.target.x - l.source.x, dy = l.target.y - l.source.y, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L; el.setAttribute("x1", String(l.source.x + ux * l.source.r)); el.setAttribute("y1", String(l.source.y + uy * l.source.r)); el.setAttribute("x2", String(l.target.x - ux * (l.target.r + 4))); el.setAttribute("y2", String(l.target.y - uy * (l.target.r + 4))); });
        node.attr("cx", (d) => d.x).attr("cy", (d) => d.y); label.attr("x", (d) => d.x + d.r + 3).attr("y", (d) => d.y + 3.5);
        const byMod = d3.group(fnodes, (d: any) => this.keyOf(d.n));
        hull.attr("d", (m) => { const pts: [number, number][] = (byMod.get(m) ?? []).flatMap((d: any) => [[d.x - d.r, d.y - d.r], [d.x + d.r, d.y - d.r], [d.x - d.r, d.y + d.r], [d.x + d.r, d.y + d.r]] as [number, number][]); const h = d3.polygonHull(pts); return h ? "M" + h.join("L") + "Z" : null; });
        hullLab.attr("x", (m) => d3.mean(byMod.get(m) ?? [], (d: any) => d.x) ?? 0).attr("y", (m) => (d3.min(byMod.get(m) ?? [], (d: any) => d.y - d.r) ?? 0) - 22);
      };
      this.fileHighlight = applyHighlight;
      if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { sim.tick(300); tick(); } else { sim.on("tick", tick).alpha(1).restart(); }
      applyHighlight();
    });
    this.cdr.detectChanges();
  }

  // ── traverse (server-side walk) ──────────────────────────────────────────
  travStartChanged(): void { const hit = this.resolveFile(this.travStartText); this.travStart = hit ? hit.id : null; if (hit) this.travStartText = this.fileKey(hit); this.runTraverse(true); }
  travToChanged(): void { const hit = this.resolveFile(this.travToText); this.travTo = hit ? hit.id : null; if (hit) this.travToText = this.fileKey(hit); this.runTraverse(true); }
  clearTraverse(): void { this.travStart = null; this.travTo = null; this.travStartText = ""; this.travToText = ""; this.runTraverse(false); this.selected = null; this.renderDetail(); }
  traverseFrom(id: string): void { const n = this.byId.get(id); if (!n) return; this.travStart = id; this.travTo = null; this.travStartText = this.fileKey(n); this.travToText = ""; this.tab = "trav"; this.runTraverse(true); }

  runTraverse(userTriggered: boolean): void {
    const el = this.travSvg?.nativeElement; if (!el || !this.graph) return;
    const W = 980, H = 680;
    const svg = d3.select(el).attr("viewBox", `0 0 ${W} ${H}`); svg.selectAll("*").remove(); svg.on(".zoom", null);
    const empty = (msg: string) => { svg.append("text").attr("class", "ldg-empty").attr("x", W / 2).attr("y", H / 2).attr("text-anchor", "middle").text(msg); this.travCount = ""; };
    if (!this.travStart || !this.byId.has(this.travStart)) { empty('Type a start page above, or use "Traverse from here" on any page.'); return; }
    this.addMarkers(svg.append("defs"), [["ldg-tarr", "ldg-arrow", 8, 7], ["ldg-tarr-on", "ldg-arrow-on", 8, 7]]);
    const root = svg.append("g"); svg.call(d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.3, 6]).on("zoom", (ev) => root.attr("transform", ev.transform.toString())));
    if (this.travTo && this.byId.has(this.travTo)) {
      this.svc.libraryGraphPath(this.gearId, this.travStart, this.travTo, this.hubs, this.selectedVersion).subscribe((res) => {
        if (!res) { empty("Path lookup failed."); return; }
        if (!res.found) { empty("No reference chain between these two pages."); if (userTriggered) this.showPathDetail(res); return; }
        this.zone.runOutsideAngular(() => this.drawPath(root, W, H, res));
        this.travCount = `${res.hops} hop${res.hops === 1 ? "" : "s"} ${res.direction === "down" ? "following references" : res.direction === "up" ? "against the reference direction" : "ignoring direction"}.`;
        if (userTriggered) this.showPathDetail(res); this.cdr.detectChanges();
      });
    } else {
      this.svc.libraryGraphTraverse(this.gearId, this.travStart, this.travDir, this.travDepth, this.hubs, this.selectedVersion).subscribe((res) => {
        if (!res) { empty("Traversal failed."); return; }
        this.zone.runOutsideAngular(() => this.drawRings(root, W, H, res));
        const touched = new Set(res.nodes.filter((n) => n.id !== res.start).map((n) => this.keyOf(n)));
        this.travCount = `${res.nodes.length - 1} pages reached within ${res.depth} hop${res.depth === 1 ? "" : "s"} across ${touched.size} ${this.groupBy}${touched.size === 1 ? "" : "s"}.`;
        if (userTriggered) this.showWalkDetail(res); this.cdr.detectChanges();
      });
    }
  }
  private drawRings(root: any, W: number, H: number, res: LibraryGraphWalk): void {
    const cx = W / 2, cy = H / 2, D = d3.max(res.nodes, (n) => n.depth ?? 0) || 0, step = D ? (Math.min(W, H) / 2 - 50) / D : 0;
    const byId = new Map(res.nodes.map((n) => [n.id, n])); const pos = new Map<string, { x: number; y: number; a: number }>(); pos.set(res.start, { x: cx, y: cy, a: 0 });
    for (let d = 1; d <= D; d++) {
      const ring = res.nodes.filter((n) => n.depth === d).sort((a, b) => ((pos.get(a.parent_id ?? "")?.a ?? 0) - (pos.get(b.parent_id ?? "")?.a ?? 0)) || this.keyOf(a).localeCompare(this.keyOf(b)) || a.file.localeCompare(b.file));
      ring.forEach((n, i) => { const a = -Math.PI / 2 + i * 2 * Math.PI / ring.length; pos.set(n.id, { x: cx + step * d * Math.cos(a), y: cy + step * d * Math.sin(a), a }); });
      root.append("circle").attr("class", "ldg-ring").attr("cx", cx).attr("cy", cy).attr("r", step * d);
      root.append("text").attr("class", "ldg-ring-label").attr("x", cx - (step * d - 14) * Math.SQRT1_2).attr("y", cy - (step * d - 14) * Math.SQRT1_2).attr("text-anchor", "middle").text(d === 1 ? "1 hop" : `${d} hops`);
    }
    const tree = new Set(res.nodes.map((n) => n.via_edge_id).filter((x) => x != null));
    const links = res.edges.filter((e) => pos.has(e.source) && pos.has(e.target));
    root.append("g").selectAll("line").data(links).join("line").attr("class", (e: LibraryGraphEdge) => "ldg-edge" + (tree.has(e.id) ? " on" : " faint")).attr("marker-end", (e: LibraryGraphEdge) => (tree.has(e.id) ? "url(#ldg-tarr-on)" : "url(#ldg-tarr)"))
      .each((e: LibraryGraphEdge, i: number, nodesEl: any) => { const s = pos.get(e.source)!, t = pos.get(e.target)!, rs = this.rLoc(byId.get(e.source)!.loc), rt = this.rLoc(byId.get(e.target)!.loc); const dx = t.x - s.x, dy = t.y - s.y, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L; d3.select(nodesEl[i]).attr("x1", s.x + ux * rs).attr("y1", s.y + uy * rs).attr("x2", t.x - ux * (rt + 4)).attr("y2", t.y - uy * (rt + 4)); });
    const data = res.nodes.map((n) => ({ id: n.id, n, r: Math.max(4, this.rLoc(n.loc)) }));
    root.append("g").selectAll("circle").data(data).join("circle").attr("class", (d: any) => "ldg-node" + (d.id === res.start ? " start" : "")).attr("r", (d: any) => d.r).attr("fill", (d: any) => this.nodeFill(d.n)).attr("cx", (d: any) => pos.get(d.id)!.x).attr("cy", (d: any) => pos.get(d.id)!.y)
      .on("mouseenter", (ev: MouseEvent, d: any) => this.showTip(ev, d.n.file, [["module", d.n.module], ["domain", d.n.domain ?? "unknown"], ["hops from start", String(d.n.depth)], ["reached via", d.n.parent_id ? byId.get(d.n.parent_id)!.file : "—"]]))
      .on("mousemove", this.moveTip).on("mouseleave", this.hideTip).on("click", (ev: MouseEvent, d: any) => { ev.stopPropagation(); this.zone.run(() => this.select({ type: "file", id: d.id })); });
    const ring1 = res.nodes.filter((n) => n.depth === 1).length, showAll = res.nodes.length <= 70;
    root.append("g").selectAll("text").data(data.filter((d) => showAll || d.id === res.start || (d.n.depth === 1 && ring1 <= 36))).join("text").attr("class", "ldg-flabel").text((d: any) => d.n.file)
      .attr("x", (d: any) => { const p = pos.get(d.id)!; return d.id === res.start ? p.x : p.x + Math.cos(p.a) * (d.r + 4); }).attr("y", (d: any) => { const p = pos.get(d.id)!; return d.id === res.start ? p.y + d.r + 12 : p.y + Math.sin(p.a) * (d.r + 4) + 3.5; })
      .attr("text-anchor", (d: any) => { const p = pos.get(d.id)!; if (d.id === res.start) return "middle"; const c = Math.cos(p.a); return c > 0.2 ? "start" : c < -0.2 ? "end" : "middle"; });
  }
  private drawPath(root: any, W: number, H: number, res: LibraryGraphPath): void {
    const p = res.nodes, perRow = 5, gapX = (W - 160) / (perRow - 1), rowH = 130, rows = Math.ceil(p.length / perRow), yOff = (H - (rows - 1) * rowH) / 2;
    const pos = p.map((n, i) => { const row = Math.floor(i / perRow); let col = i % perRow; if (row % 2 === 1) col = perRow - 1 - col; return { x: 80 + col * gapX, y: yOff + row * rowH }; });
    res.edges.forEach((e) => { const si = p.findIndex((n) => n.id === e.source), ti = p.findIndex((n) => n.id === e.target); if (si < 0 || ti < 0) return; const s = pos[si], t = pos[ti]; const dx = t.x - s.x, dy = t.y - s.y, L = Math.hypot(dx, dy) || 1, ux = dx / L, uy = dy / L;
      root.append("line").attr("class", "ldg-edge on").attr("marker-end", "url(#ldg-tarr-on)").attr("x1", s.x + ux * 11).attr("y1", s.y + uy * 11).attr("x2", t.x - ux * 15).attr("y2", t.y - uy * 15);
      const ev = (e.evidence ?? "").replace(/\s+/g, " "); root.append("text").attr("class", "ldg-hop-label").attr("x", (s.x + t.x) / 2).attr("y", (s.y + t.y) / 2 - 8).attr("text-anchor", "middle").text(ev.length > 42 ? ev.slice(0, 40) + "…" : ev); });
    const data = p.map((n) => ({ id: n.id, n }));
    root.append("g").selectAll("circle").data(data).join("circle").attr("class", (d: any, i: number) => "ldg-node" + (i === 0 ? " start" : "")).attr("r", 9).attr("fill", (d: any) => this.nodeFill(d.n)).attr("cx", (d: any, i: number) => pos[i].x).attr("cy", (d: any, i: number) => pos[i].y)
      .on("mouseenter", (ev: MouseEvent, d: any) => this.showTip(ev, d.n.file, [["module", d.n.module], ["domain", d.n.domain ?? "unknown"], ["layer", d.n.layer ?? "unknown"]])).on("mousemove", this.moveTip).on("mouseleave", this.hideTip)
      .on("click", (ev: MouseEvent, d: any) => this.zone.run(() => this.select({ type: "file", id: d.id })));
    root.append("g").selectAll("text").data(data).join("text").attr("class", "ldg-hop-name").attr("text-anchor", "middle").attr("x", (d: any, i: number) => pos[i].x).attr("y", (d: any, i: number) => pos[i].y + 24).text((d: any) => d.n.file);
    root.append("g").selectAll("text").data(data).join("text").attr("class", "ldg-hop-label").attr("text-anchor", "middle").attr("x", (d: any, i: number) => pos[i].x).attr("y", (d: any, i: number) => pos[i].y + 38).text((d: any) => this.keyOf(d.n));
  }

  // ── tables ───────────────────────────────────────────────────────────────
  get tablePairs(): Pair[] { const q = this.tblFilter.trim().toLowerCase(); return this.pairList.filter((p) => p.a !== p.b && (!q || p.a.toLowerCase().includes(q) || p.b.toLowerCase().includes(q))).sort((x, y) => y.c - x.c); }
  get tableRows(): { e: LibraryGraphEdge; a: LibraryGraphNode; b: LibraryGraphNode }[] {
    const q = this.tblFilter.trim().toLowerCase();
    return this.E.map((e) => ({ e, a: this.byId.get(e.source)!, b: this.byId.get(e.target)! }))
      .filter((r) => !q || [r.a.file, r.b.file, this.keyOf(r.a), this.keyOf(r.b)].some((s) => s.toLowerCase().includes(q)))
      .sort((x, y) => x.a.file.localeCompare(y.a.file) || x.b.file.localeCompare(y.b.file)).slice(0, 2000);
  }

  // ── detail panel ─────────────────────────────────────────────────────────
  select(sel: Selection | null): void { this.selected = sel; if (sel?.type === "file") this.fileSel = sel.id ?? null; this.renderDetail(); this.fileHighlight?.(); this.cdr.detectChanges(); }
  selectGroupOf(n: LibraryGraphNode, by: GroupBy): void { if (this.groupBy !== by) { this.groupBy = by; this.renderAll(); } this.select({ type: "group", id: this.keyOf(n) }); }
  private ref(id: string): FileRef { const n = this.byId.get(id); return { id, file: n?.file ?? id, group: n ? this.keyOf(n) : "" }; }
  private countBy<T>(arr: T[], f: (x: T) => string): [string, number][] { const c = new Map<string, number>(); arr.forEach((x) => c.set(f(x), (c.get(f(x)) ?? 0) + 1)); return [...c.entries()].sort((a, b) => b[1] - a[1]); }

  private renderDetail(): void {
    const s = this.selected;
    if (!s) { this.detail = { kind: "default", hubs: [...this.deg.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12).map(([id, c]) => ({ ...this.ref(id), degree: c })) }; return; }
    if (s.type === "group") { this.showGroup(s.id!); return; }
    if (s.type === "pair") { this.showPair(s.a!, s.b!); return; }
    if (s.type === "file") { this.showFile(s.id!); return; }
  }
  private showGroup(g: string): void {
    const files = (this.graph?.nodes ?? []).filter((n) => this.keyOf(n) === g), info = this.groupInfo.get(g) ?? { files: 0, loc: 0 };
    const outPairs = this.pairList.filter((p) => p.a === g && p.b !== g).sort((x, y) => y.c - x.c), inPairs = this.pairList.filter((p) => p.b === g && p.a !== g).sort((x, y) => y.c - x.c);
    this.detail = { kind: "group", title: g,
      rows: [["Pages", String(info.files)], ["Lines of code", info.loc.toLocaleString()], ["Layers", this.countBy(files, (f) => f.layer || "unknown").map(([k, v]) => `${k} ${v}`).join(", ")], ["Risk", this.countBy(files, (f) => f.risk || "unknown").map(([k, v]) => `${k} ${v}`).join(", ")],
        [this.groupBy === "module" ? "Domains" : "Modules", String(new Set(files.map((f) => (this.groupBy === "module" ? f.domain || "unknown" : f.module))).size)],
        ["References out", String(outPairs.reduce((s, p) => s + p.c, 0))], ["References in", String(inPairs.reduce((s, p) => s + p.c, 0))], ["Inside", String(this.pairCount(g, g))]],
      dependsOn: outPairs.slice(0, 10), dependedBy: inPairs.slice(0, 10),
      pages: files.slice().sort((a, b) => a.file.localeCompare(b.file)).map((f) => ({ ...this.ref(f.id), degree: this.deg.get(f.id) ?? 0 })) };
  }
  private showPair(a: string, b: string): void {
    const es = this.pairEdges(a, b); const rev = this.pairCount(b, a);
    this.detail = { kind: "pair", title: `${a} → ${b}`, reverse: rev ? { a: b, b: a, c: rev } : null,
      pairEdges: es.slice().sort((x, y) => this.byId.get(x.target)!.file.localeCompare(this.byId.get(y.target)!.file)).map((e) => ({ sId: e.source, sFile: this.byId.get(e.source)!.file, tId: e.target, tFile: this.byId.get(e.target)!.file, evidence: e.evidence ?? "", label: e.label })) };
  }
  private showFile(id: string): void {
    const n = this.byId.get(id); if (!n) { this.selected = null; this.renderDetail(); return; }
    this.detail = { kind: "file", node: n, loading: true };
    this.svc.libraryGraphNode(this.gearId, id, this.selectedVersion).subscribe((d) => {
      if (!d || this.selected?.type !== "file" || this.selected.id !== id) return;
      const lib = d.library ?? null;
      const kinds = CARD_KINDS.filter((k) => (lib?.cards?.[k]?.length ?? 0) > 0);
      const grouped = (edges: LibraryGraphNodeDetail["outgoing"]): GroupedRefs[] => {
        const pages = edges.filter((e) => e.other.kind === "Component"); const m = new Map<string, FileRef[]>();
        pages.forEach((e) => { const other = this.byId.get(e.other.id); const g = other ? this.keyOf(other) : e.other.module; (m.get(g) ?? m.set(g, []).get(g)!).push({ id: e.other.id, file: e.other.file, group: g, n: this.kindLabel(e.label) }); });
        return [...m.keys()].sort().map((g) => ({ group: g, items: m.get(g)!.sort((x, y) => x.file.localeCompare(y.file)) }));
      };
      this.detail = { kind: "file", node: d.node, loading: false, purpose: d.node.purpose, tables: d.node.tables, includes: d.node.includes,
                      out: grouped(d.outgoing), inc: grouped(d.incoming), library: lib, cardKinds: kinds };
      this.cdr.detectChanges();
    });
  }
  private showWalkDetail(res: LibraryGraphWalk): void {
    const start = this.byId.get(res.start)!, reached = res.nodes.filter((n) => n.id !== res.start), D = d3.max(reached, (n) => n.depth ?? 0) ?? 0;
    const rings: Detail["rings"] = [];
    for (let d = 1; d <= D; d++) rings.push({ depth: d, items: reached.filter((n) => n.depth === d).sort((a, b) => this.keyOf(a).localeCompare(this.keyOf(b)) || a.file.localeCompare(b.file)).map((n) => this.ref(n.id)) });
    this.selected = { type: "trav" };
    this.detail = { kind: "walk", title: start.file,
      rows: [["Direction", res.direction === "down" ? "downstream (what it uses)" : res.direction === "up" ? "upstream (what uses it)" : "both"], ["Depth", String(res.depth)], ["Pages reached", String(reached.length)], ["Edges followed", String(res.edges.length)]],
      touched: this.countBy(reached, (n) => this.keyOf(n)).map(([a, c]) => ({ a, b: a, c })), rings };
    this.cdr.detectChanges();
  }
  private showPathDetail(res: LibraryGraphPath): void {
    const a = this.byId.get(res.from)!, b = this.byId.get(res.to)!;
    this.selected = { type: "trav" };
    this.detail = { kind: "path", title: `${a.file} → ${b.file}`,
      note: res.found ? undefined : "No chain of references connects these two pages, in either direction, within 8 hops.",
      rows: res.found ? [["Hops", String(res.hops)], ["Direction", res.direction === "down" ? "following references" : res.direction === "up" ? "against references" : "ignoring direction"]] : [],
      hops: res.edges.map((e) => ({ sId: e.source, sFile: this.byId.get(e.source)?.file ?? e.source, tId: e.target, tFile: this.byId.get(e.target)?.file ?? e.target, evidence: e.evidence ?? "", label: e.label })) };
    this.cdr.detectChanges();
  }
}
