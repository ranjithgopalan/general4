import {
  AfterViewInit, Component, ElementRef, NgZone, OnDestroy, OnInit,
  ViewChild, effect, signal,
} from "@angular/core";
import * as d3 from "d3";
import { DomSanitizer, SafeHtml } from "@angular/platform-browser";

import {
  Artifact, ArtifactContent, AidlcPlatformMgmtService,
  GraphEdge, GraphNode, OntologyGraph, Stage,
} from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

interface ChatMessage { role: "user" | "kb"; text: string; ts: number; }
interface NeighborPanel { node: GraphNode; nodes: GraphNode[]; edges: GraphEdge[]; loading: boolean; }
type D3Node = GraphNode & d3.SimulationNodeDatum;
type D3Link = { source: string | D3Node; target: string | D3Node; label: string; };

@Component({
  selector: "app-apm-knowledge-ontology",
  template: `
    <ng-container *ngIf="store.selectedId(); else pickProject">

      <!-- breadcrumb -->
      <div class="apm-crumb">{{ store.selectedProject()?.kb_application_id }} / Knowledge &amp; Ontology</div>

      <!-- header -->
      <div class="apm-hdr">
        <div class="apm-hdr-icon">📚</div>
        <div>
          <h2>Knowledge &amp; Ontology</h2>
          <p class="sub">Documents · Graph · Grounded Q&amp;A</p>
        </div>
        <div class="ko-tabs" style="margin-left:16px">
          <button class="ko-tab" [class.active]="activeView()==='documents'" (click)="switchView('documents')">
            📄 Documents
          </button>
          <button class="ko-tab" [class.active]="activeView()==='ontology'" (click)="switchView('ontology')">
            🕸 Ontology Graph
          </button>
        </div>
        <button class="apm-btn" style="margin-left:auto" (click)="chatOpen.set(true)">
          💬 Ask the Knowledge Base
        </button>
      </div>

      <!-- ═══════════════════════════════ DOCUMENT VIEW ═══════════════════════════════ -->
      <div [style.display]="activeView() === 'documents' ? '' : 'none'">

        <div class="apm-card" *ngIf="!stages().length">
          <div class="apm-card-body sub">
            No pipeline loaded — select a workspace from the sidebar first.
          </div>
        </div>

        <div class="ko-doc-layout" *ngIf="stages().length">

          <!-- left: stage / artifact list -->
          <div class="ko-doc-list">
            <div class="apm-card" *ngFor="let s of stages()">
              <div class="apm-card-head">
                <h4>{{ dot(s) }} {{ s.name }}</h4>
                <span style="display:flex;gap:8px;align-items:center">
                  <span class="apm-chip" [ngClass]="tone(s)">{{ stageLabel(s) }}</span>
                  <span class="sub" *ngIf="artifactsFor(s).length">
                    {{ artifactsFor(s).length }}&nbsp;doc{{ artifactsFor(s).length !== 1 ? 's' : '' }}
                  </span>
                </span>
              </div>
              <div class="apm-card-body">
                <ng-container *ngIf="artifactsFor(s).length; else noContent">
                  <div class="ko-artifact-row" *ngFor="let a of artifactsFor(s)"
                       [class.selected]="selectedArtifact()?.id === a.id"
                       (click)="selectArtifact(a)">
                    <span class="apm-fi" [class.ok]="a.status === 'APPROVED' || a.status === 'approved'">▤</span>
                    <div class="ko-artifact-meta">
                      <div class="apm-fn">{{ a.artifact_type }}</div>
                      <div style="display:flex;gap:6px;align-items:center;flex-wrap:wrap">
                        <span class="apm-chip" [ngClass]="a.status === 'APPROVED' ? 'ok' : ''">{{ a.status }}</span>
                        <span class="sub">v{{ a.version }}</span>
                        <span class="sub" *ngIf="a.produced_by_persona">· {{ a.produced_by_persona }}</span>
                      </div>
                    </div>
                    <button class="apm-btn sm outline" style="margin-left:auto;white-space:nowrap;flex-shrink:0"
                            (click)="$event.stopPropagation(); askAboutDoc(a)" title="Ask the KB about this document">
                      💬 Ask
                    </button>
                  </div>
                </ng-container>
                <ng-template #noContent>
                  <span class="apm-chip amber">not yet indexed</span>
                  <span class="sub" style="margin-left:8px">
                    Run and approve this stage to index its output into the knowledge base.
                  </span>
                </ng-template>
              </div>
            </div>
          </div>

          <!-- right: content panel when artifact selected -->
          <div class="ko-content-panel" *ngIf="selectedArtifact() as art">
            <div class="apm-card" style="height:100%;display:flex;flex-direction:column">
              <div class="apm-card-head">
                <h4>{{ art.artifact_type }}</h4>
                <div style="display:flex;gap:6px;align-items:center">
                  <span class="apm-chip" [ngClass]="art.status === 'APPROVED' ? 'ok' : ''">{{ art.status }}</span>
                  <span class="sub">v{{ art.version }}</span>
                  <button class="apm-btn sm outline" (click)="askAboutDoc(art)">💬 Ask about this</button>
                  <button class="apm-btn sm outline" (click)="viewOntologyForArtifact(art)"
                          title="See related ontology nodes">🕸 Related graph</button>
                  <button class="apm-x" (click)="selectedArtifact.set(null); artifactHtml.set(null)">×</button>
                </div>
              </div>
              <div class="ko-content-body apm-card-body" style="flex:1;overflow-y:auto">
                <div *ngIf="contentLoading()" class="sub" style="padding:20px">Loading content…</div>
                <div *ngIf="!contentLoading() && artifactHtml()" [innerHTML]="artifactHtml()"></div>
                <div *ngIf="!contentLoading() && !artifactHtml()" class="sub" style="padding:20px">
                  No content available for this artifact.
                </div>
              </div>
            </div>
          </div>

          <!-- right: placeholder -->
          <div class="ko-content-empty" *ngIf="!selectedArtifact()">
            <div style="text-align:center;color:#64748b">
              <div style="font-size:36px;margin-bottom:12px">📄</div>
              <div>Select a document to view its content</div>
              <div style="font-size:11px;margin-top:6px">or click 💬 Ask to query the knowledge base</div>
            </div>
          </div>

        </div>
      </div>

      <!-- ═══════════════════════════════ ONTOLOGY GRAPH VIEW ═══════════════════════════════ -->
      <div [style.display]="activeView() === 'ontology' ? '' : 'none'">

        <div class="apm-card" *ngIf="graphError()">
          <div class="apm-card-body sub" style="color:#f87171">{{ graphError() }}</div>
        </div>
        <div class="apm-card" *ngIf="graphLoading() && !graph()">
          <div class="apm-card-body sub">Loading knowledge graph…</div>
        </div>

        <ng-container *ngIf="graph() as g">

          <!-- controls -->
          <div class="ko-graph-controls">
            <div class="ko-kind-tiles">
              <div class="ko-kind-tile" [class.active]="!activeKind()"
                   (click)="activeKind.set(null); reRenderGraph()" title="Show all nodes">
                <div class="ko-kind-val">{{ g.nodes.length }}</div>
                <div class="ko-kind-lbl">All</div>
              </div>
              <div class="ko-kind-tile" *ngFor="let k of g.kinds"
                   [class.active]="activeKind() === k.kind"
                   (click)="activeKind.set(k.kind); reRenderGraph()"
                   [title]="'Filter: ' + (k.kind_label || k.kind)">
                <div class="ko-kind-val">{{ k.count }}</div>
                <div class="ko-kind-lbl">{{ k.kind_label || k.kind }}</div>
              </div>
            </div>
            <input class="apm-input" style="width:200px;flex-shrink:0"
                   placeholder="Search nodes…"
                   [(ngModel)]="searchTerm" (ngModelChange)="reRenderGraph()" />
            <span class="apm-chip" *ngIf="g.kb_version" style="margin-left:auto">{{ g.kb_version }}</span>
            <button class="apm-btn sm outline" [disabled]="graphLoading()" (click)="loadOntology()">
              {{ graphLoading() ? '…' : '⟳ Refresh' }}
            </button>
          </div>

          <!-- graph + detail panel -->
          <div class="ko-graph-area" [class.ko-graph-area--split]="selectedNode()">

            <!-- SVG canvas — always in DOM so @ViewChild is always available -->
            <div class="ko-graph-canvas-wrap">
              <svg #graphSvg class="ko-graph-svg"></svg>
              <div class="ko-graph-hint" *ngIf="!graphLoading()">
                Scroll to zoom · Drag to pan · Click a node to inspect
              </div>
              <div class="ko-graph-legend">
                <div class="ko-legend-item" *ngFor="let k of g.kinds">
                  <span class="ko-legend-dot" [style.background]="kindColor(k.kind)"></span>
                  <span>{{ k.kind_label || k.kind }}</span>
                </div>
              </div>
            </div>

            <!-- node detail panel -->
            <div class="ko-node-panel" *ngIf="selectedNode() as node">
              <div class="ko-node-panel-head">
                <div style="flex:1;min-width:0">
                  <span class="apm-chip" style="font-size:11px">{{ node.kind_label || node.kind }}</span>
                  <div class="ko-node-title">{{ node.label }}</div>
                  <div class="sub" style="font-size:10px;word-break:break-all">{{ node.id }}</div>
                </div>
                <button class="apm-x" (click)="selectedNode.set(null); neighbor.set(null)">×</button>
              </div>
              <div class="ko-node-panel-body">
                <p *ngIf="node.summary" style="font-size:12px;line-height:1.6;color:#94a3b8;margin-bottom:14px">
                  {{ node.summary }}
                </p>
                <button class="apm-btn green" style="width:100%;justify-content:center;margin-bottom:14px"
                        (click)="askAboutNode(node)">
                  💬 Ask about this node
                </button>
                <button class="apm-btn sm outline" style="width:100%;justify-content:center;margin-bottom:16px"
                        (click)="switchToDocForNode(node)">
                  📄 View related documents
                </button>

                <div *ngIf="neighbor()?.loading" class="sub" style="font-size:12px">Loading connections…</div>

                <ng-container *ngIf="neighbor() as nb">
                  <ng-container *ngIf="!nb.loading">
                    <div *ngIf="nb.nodes.length" style="margin-bottom:14px">
                      <div class="ko-section-hdr">CONNECTED NODES ({{ nb.nodes.length }})</div>
                      <div class="ko-conn-node" *ngFor="let n of nb.nodes" (click)="selectGraphNode(n)">
                        <span class="apm-chip" style="font-size:10px;flex-shrink:0">{{ n.kind }}</span>
                        <span style="font-size:12px;margin-left:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">
                          {{ n.label }}
                        </span>
                      </div>
                    </div>
                    <div *ngIf="nb.edges.length">
                      <div class="ko-section-hdr">RELATIONSHIPS ({{ nb.edges.length }})</div>
                      <div class="ko-rel-row" *ngFor="let e of nb.edges">
                        <span class="sub" style="font-size:10px;overflow:hidden;text-overflow:ellipsis">{{ e.source }}</span>
                        <span class="apm-chip" style="font-size:9px;flex-shrink:0">{{ e.label }}</span>
                        <span class="sub" style="font-size:10px;overflow:hidden;text-overflow:ellipsis;text-align:right">{{ e.target }}</span>
                      </div>
                    </div>
                    <div *ngIf="!nb.nodes.length && !nb.edges.length" class="sub" style="font-size:12px">
                      No connected nodes found.
                    </div>
                  </ng-container>
                </ng-container>
              </div>
            </div>

          </div><!-- /ko-graph-area -->

          <!-- collapsible edge list -->
          <div class="apm-card" *ngIf="g.edges.length" style="margin-top:12px">
            <div class="apm-card-head" (click)="edgesOpen.set(!edgesOpen())" style="cursor:pointer">
              <h4>{{ edgesOpen() ? '▾' : '▸' }} Edges ({{ g.edges.length }})</h4>
            </div>
            <div class="apm-card-body" *ngIf="edgesOpen()">
              <div class="ko-edge-table">
                <div class="ko-edge-row ko-edge-hdr">
                  <span>Source</span><span>Relation</span><span>Target</span>
                </div>
                <div class="ko-edge-row" *ngFor="let e of g.edges">
                  <span class="sub" style="font-size:11px">{{ e.source }}</span>
                  <span class="apm-chip" style="font-size:10px">{{ e.label }}</span>
                  <span class="sub" style="font-size:11px">{{ e.target }}</span>
                </div>
              </div>
            </div>
          </div>

        </ng-container>
      </div>

      <!-- ═══════════════════════════════ SHARED CHAT DRAWER ═══════════════════════════════ -->
      <ng-container *ngIf="chatOpen()">
        <div class="apm-modal-bg" (click)="chatOpen.set(false)"></div>
        <aside class="apm-drawer apm-kb-chat-drawer">

          <div class="apm-kb-chat-header">
            <div class="apm-kb-chat-header-left">
              <div class="apm-kb-chat-logo">🗄</div>
              <div>
                <div class="apm-kb-chat-title">Knowledge Base</div>
                <div class="apm-kb-chat-sub">grounded · cite-or-abstain</div>
              </div>
            </div>
            <div style="display:flex;gap:8px;align-items:center">
              <button class="apm-btn sm outline" *ngIf="chatHistory().length" (click)="downloadAll()"
                      title="Export chat history">⬇ Export</button>
              <button class="apm-x" (click)="chatOpen.set(false)">×</button>
            </div>
          </div>

          <!-- context badge -->
          <div *ngIf="chatContextLabel()" class="ko-ctx-badge">
            📌 {{ chatContextLabel() }}
          </div>

          <div class="apm-chat-history">
            <div class="apm-chat-empty" *ngIf="!chatHistory().length">
              <div class="apm-chat-empty-icon">💬</div>
              <p>Ask anything grounded in the indexed stage documents.</p>
              <small>The model will cite a source or abstain if the answer isn't in the KB.</small>
            </div>

            <ng-container *ngFor="let m of chatHistory(); let i = index">
              <div class="apm-chat-row user" *ngIf="m.role === 'user'">
                <div class="apm-chat-body">
                  <div class="apm-chat-meta">You · {{ formatTime(m.ts) }}</div>
                  <div class="apm-chat-bubble user">{{ m.text }}</div>
                </div>
                <div class="apm-chat-avatar user">👤</div>
              </div>
              <div class="apm-chat-row kb" *ngIf="m.role === 'kb'">
                <div class="apm-chat-avatar kb">🗄</div>
                <div class="apm-chat-body">
                  <div class="apm-chat-meta">Knowledge Base · {{ formatTime(m.ts) }}</div>
                  <div class="apm-chat-bubble kb" [innerHTML]="render(m.text)"></div>
                  <div class="apm-chat-actions">
                    <button class="apm-chat-action-btn" (click)="downloadMessage(m, i)" title="Download this response">
                      ⬇ Download response
                    </button>
                  </div>
                </div>
              </div>
            </ng-container>

            <div class="apm-chat-row kb" *ngIf="sending()">
              <div class="apm-chat-avatar kb">🗄</div>
              <div class="apm-chat-body">
                <div class="apm-chat-meta">Knowledge Base</div>
                <div class="apm-chat-bubble kb apm-chat-thinking">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          </div>

          <div class="apm-chat-input-bar">
            <textarea class="apm-ta" rows="2"
              style="flex:1;min-height:auto;resize:none;background:var(--panel-bg)"
              [(ngModel)]="question"
              placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
              (keydown.enter)="onChatKey($event)">
            </textarea>
            <button class="apm-btn green" style="align-self:flex-end;white-space:nowrap"
              [disabled]="sending() || !question.trim()" (click)="send()">
              {{ sending() ? '…' : '↑ Send' }}
            </button>
          </div>
        </aside>
      </ng-container>

      <style>
        /* ── tab switcher ── */
        .ko-tabs { display:flex; gap:4px; background:var(--panel-bg,#1a2740); border-radius:10px; padding:4px; }
        .ko-tab  { background:transparent; border:none; border-radius:8px; color:#64748b; cursor:pointer;
                    font:inherit; font-size:13px; font-weight:600; padding:6px 14px; transition:all .15s; }
        .ko-tab.active       { background:#0077c8; color:#fff; }
        .ko-tab:hover:not(.active) { background:#1e3a5f; color:#e2e8f0; }

        /* ── document layout ── */
        .ko-doc-layout { display:grid; grid-template-columns:1fr 1fr; gap:12px; align-items:start; }
        .ko-doc-list   { display:flex; flex-direction:column; gap:12px;
                          max-height:calc(100vh - 220px); overflow-y:auto; }
        .ko-artifact-row { display:flex; align-items:center; gap:10px; padding:10px 6px;
                            border-bottom:1px solid #1e293b; cursor:pointer; border-radius:6px;
                            transition:background .1s; }
        .ko-artifact-row:last-child { border-bottom:none; }
        .ko-artifact-row:hover   { background:#0d1f35; }
        .ko-artifact-row.selected { background:#0d2240; }
        .ko-artifact-meta { flex:1; min-width:0; }
        .ko-content-panel { max-height:calc(100vh - 220px); overflow-y:auto; }
        .ko-content-empty { display:flex; align-items:center; justify-content:center;
                             min-height:240px; color:#64748b; }
        /* markdown content */
        .ko-content-body .apm-md-p    { margin:0 0 10px; line-height:1.7; font-size:13.5px; }
        .ko-content-body .apm-md-h    { color:#93c5fd; font-size:15px; margin:16px 0 8px; }
        .ko-content-body .apm-md-gap  { height:6px; }
        .ko-content-body .apm-md-list { padding-left:20px; margin:6px 0 10px; }
        .ko-content-body .apm-md-list li { margin-bottom:5px; }
        .ko-content-body .apm-md-pre  { background:#0b1220; border:1px solid #1e3a5f;
                                          border-radius:8px; padding:12px 14px; overflow-x:auto; margin:10px 0; }
        .ko-content-body .apm-md-pre code { font-family:monospace; font-size:12px; color:#7dd3fc; line-height:1.6; }
        .ko-content-body .apm-md-code { background:rgba(0,119,200,.15); color:#7dd3fc;
                                          padding:2px 5px; border-radius:4px; font-family:monospace; font-size:12px; }
        .ko-content-body .apm-md-cite { display:inline-flex; min-width:18px; height:18px; padding:0 4px;
                                          background:rgba(0,119,200,.25); color:#7cc0f2; border-radius:999px;
                                          font-size:10px; font-weight:700; margin:0 2px; vertical-align:super;
                                          align-items:center; justify-content:center; }

        /* ── graph controls ── */
        .ko-graph-controls { display:flex; align-items:center; gap:10px; flex-wrap:wrap; margin-bottom:12px; }
        .ko-kind-tiles     { display:flex; gap:8px; flex-wrap:wrap; }
        .ko-kind-tile { background:var(--panel-bg,#1a2740); border:1px solid var(--border,#2a4a72);
                         border-radius:8px; padding:8px 14px; min-width:68px; text-align:center;
                         cursor:pointer; transition:border-color .15s,background .15s; }
        .ko-kind-tile:hover    { border-color:#0077c8; }
        .ko-kind-tile.active   { border-color:#0077c8; background:#0d2240; }
        .ko-kind-val           { font-size:18px; font-weight:700; color:#e2e8f0; }
        .ko-kind-tile.active .ko-kind-val { color:#60a5fa; }
        .ko-kind-lbl           { font-size:10px; color:#64748b; margin-top:2px; }

        /* ── graph canvas ── */
        .ko-graph-area { display:grid; grid-template-columns:1fr; gap:12px; }
        .ko-graph-area--split { grid-template-columns:1fr 300px; }
        .ko-graph-canvas-wrap { position:relative; background:#0b1220; border:1px solid #1e3a5f;
                                  border-radius:12px; height:520px; overflow:hidden; }
        .ko-graph-svg  { width:100%; height:100%; display:block; }
        .ko-graph-hint { position:absolute; top:10px; left:50%; transform:translateX(-50%);
                          font-size:10px; color:#334155; white-space:nowrap; pointer-events:none; }
        .ko-graph-legend { position:absolute; bottom:10px; left:10px;
                            display:flex; flex-wrap:wrap; gap:8px; max-width:380px; }
        .ko-legend-item  { display:flex; align-items:center; gap:4px; font-size:10px; color:#94a3b8; }
        .ko-legend-dot   { width:10px; height:10px; border-radius:50%; flex-shrink:0; }

        /* ── node detail panel ── */
        .ko-node-panel { background:var(--panel-bg,#1a2740); border:1px solid #2a4a72;
                          border-radius:12px; display:flex; flex-direction:column;
                          height:520px; overflow:hidden; }
        .ko-node-panel-head { display:flex; align-items:flex-start; justify-content:space-between;
                               gap:8px; padding:14px 16px; border-bottom:1px solid #1e3a5f; }
        .ko-node-title      { font-size:14px; font-weight:700; color:#e2e8f0; margin:6px 0 4px; }
        .ko-node-panel-body { flex:1; overflow-y:auto; padding:14px 16px; }
        .ko-section-hdr     { font-size:10px; font-weight:700; letter-spacing:.06em;
                               color:#64748b; margin-bottom:8px; }
        .ko-conn-node { display:flex; align-items:center; padding:7px 0;
                         border-bottom:1px solid #1e293b; cursor:pointer; transition:color .1s; }
        .ko-conn-node:hover { color:#60a5fa; }
        .ko-rel-row   { display:grid; grid-template-columns:1fr auto 1fr; gap:6px;
                         align-items:center; padding:5px 0; border-bottom:1px solid #1e293b; }

        /* ── edge table ── */
        .ko-edge-table { display:flex; flex-direction:column; gap:4px; }
        .ko-edge-row   { display:grid; grid-template-columns:1fr auto 1fr; gap:8px;
                          align-items:center; padding:4px 0; border-bottom:1px solid #1e293b; font-size:11px; }
        .ko-edge-hdr   { font-weight:700; color:#64748b; font-size:10px; letter-spacing:.04em; }

        /* ── chat context badge ── */
        .ko-ctx-badge  { background:#0d2240; border-bottom:1px solid #1e3a5f;
                          padding:6px 16px; font-size:11px; color:#60a5fa; font-weight:600; }

        /* ── shared input ── */
        .apm-input { background:var(--panel-bg,#1a2740); border:1px solid var(--border,#2a4a72);
                      border-radius:8px; color:#e2e8f0; padding:6px 12px; font-size:13px; outline:none; }
        .apm-input:focus { border-color:#0077c8; }
      </style>

    </ng-container>

    <ng-template #pickProject>
      <div class="apm-card">
        <div class="apm-card-body sub">
          Pick a project on the Dashboard to view its Knowledge &amp; Ontology.
        </div>
      </div>
    </ng-template>
  `,
})
export class KnowledgeOntologyComponent implements OnInit, AfterViewInit, OnDestroy {
  @ViewChild("graphSvg") private graphSvgRef?: ElementRef<SVGSVGElement>;

  // ── tab ─────────────────────────────────────────────────────────────────
  readonly activeView = signal<"documents" | "ontology">("documents");

  // ── document state ───────────────────────────────────────────────────────
  readonly artifacts        = signal<Artifact[]>([]);
  readonly selectedArtifact = signal<Artifact | null>(null);
  readonly artifactHtml     = signal<SafeHtml | null>(null);
  readonly contentLoading   = signal(false);

  // ── ontology state ───────────────────────────────────────────────────────
  readonly graph        = signal<OntologyGraph | null>(null);
  readonly graphLoading = signal(false);
  readonly graphError   = signal<string | null>(null);
  readonly selectedNode = signal<GraphNode | null>(null);
  readonly neighbor     = signal<NeighborPanel | null>(null);
  readonly activeKind   = signal<string | null>(null);
  readonly edgesOpen    = signal(false);
  searchTerm = "";

  // ── shared chat ──────────────────────────────────────────────────────────
  readonly chatOpen    = signal(false);
  readonly chatHistory = signal<ChatMessage[]>([]);
  readonly sending     = signal(false);
  question = "";

  private viewReady = false;
  private simulation?: d3.Simulation<D3Node, D3Link>;

  private readonly KIND_COLOR: Record<string, string> = {
    SYS: "#0077c8", PROC: "#00BF6F", ENT: "#f59e0b", INT: "#8b5cf6",
    BR:  "#ef4444", SCR:  "#06b6d4", TERM:"#64748b", ROLE:"#10b981", WF: "#3b82f6",
  };

  constructor(
    readonly store: WorkspaceStore,
    private readonly svc: AidlcPlatformMgmtService,
    private readonly sanitizer: DomSanitizer,
    private readonly zone: NgZone,
  ) {
    effect(() => {
      const id = this.store.selectedId();
      if (id) {
        this.svc.artifacts(id).subscribe((r) => this.artifacts.set(r?.["items"] ?? []));
      }
    });
  }

  ngOnInit(): void { this.loadOntology(); }

  ngAfterViewInit(): void {
    this.viewReady = true;
    const g = this.graph();
    if (g && this.activeView() === "ontology") {
      this.zone.runOutsideAngular(() => this.renderGraph(g));
    }
  }

  ngOnDestroy(): void { this.simulation?.stop(); }

  // ── tab switching ─────────────────────────────────────────────────────────
  switchView(view: "documents" | "ontology"): void {
    this.activeView.set(view);
    if (view === "ontology") {
      if (!this.graph() && !this.graphLoading()) this.loadOntology();
      // Re-render after the browser recalculates layout (was display:none → block)
      setTimeout(() => this.reRenderGraph(), 20);
    }
  }

  // ── document helpers ──────────────────────────────────────────────────────
  stages(): Stage[] { return this.store.stages(); }

  artifactsFor(s: Stage): Artifact[] {
    return this.artifacts().filter((a) => a.stage_key === s.key);
  }

  selectArtifact(a: Artifact): void {
    this.selectedArtifact.set(a);
    this.artifactHtml.set(null);
    this.contentLoading.set(true);
    this.svc.artifactContent(a.id).subscribe({
      next: (c: ArtifactContent | null) => {
        const combined = (c?.files ?? []).map((f) => f.content).join("\n\n---\n\n");
        this.artifactHtml.set(this.sanitizer.bypassSecurityTrustHtml(this.toHtml(combined)));
        this.contentLoading.set(false);
      },
      error: () => {
        this.artifactHtml.set(
          this.sanitizer.bypassSecurityTrustHtml("<p style='color:#f87171'>Could not load document content.</p>"),
        );
        this.contentLoading.set(false);
      },
    });
  }

  askAboutDoc(a: Artifact): void {
    this.selectedArtifact.set(a);
    this.chatOpen.set(true);
    this.question = `Summarize the key points of the ${a.artifact_type} document.`;
  }

  viewOntologyForArtifact(_a: Artifact): void {
    this.switchView("ontology");
  }

  switchToDocForNode(_n: GraphNode): void {
    this.switchView("documents");
  }

  dot(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "✔";
    if (s.state === "waiting_for_approval") return "⚑";
    if (s.state === "running" || s.state === "queued") return "◔";
    if (s.state === "failed"  || s.state === "cancelled") return "✕";
    if (s.ready) return "▶";
    return "○";
  }
  tone(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "ok";
    if (s.state === "waiting_for_approval") return "warn";
    if (s.state === "running" || s.state === "queued") return "run";
    if (s.state === "failed"  || s.state === "cancelled") return "err";
    return "";
  }
  stageLabel(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "Indexed";
    if (s.state === "waiting_for_approval") return "Pending";
    if (s.state === "running" || s.state === "queued") return "Processing";
    if (s.state === "failed"  || s.state === "cancelled") return "Failed";
    if (s.ready) return "Ready";
    return "—";
  }

  // ── ontology helpers ──────────────────────────────────────────────────────
  loadOntology(): void {
    this.graphLoading.set(true);
    this.graphError.set(null);
    this.svc.ontologyGraph().subscribe({
      next: (g) => {
        this.graph.set(g);
        this.graphLoading.set(false);
        if (this.viewReady && this.activeView() === "ontology") {
          this.zone.runOutsideAngular(() => this.renderGraph(g));
        }
      },
      error: (e) => {
        this.graphError.set("Failed to load knowledge graph: " + (e?.message ?? "unknown"));
        this.graphLoading.set(false);
      },
    });
  }

  filteredNodes(): GraphNode[] {
    const g = this.graph();
    if (!g) return [];
    let nodes = g.nodes;
    const k = this.activeKind();
    if (k) nodes = nodes.filter((n) => n.kind === k);
    const term = this.searchTerm.toLowerCase().trim();
    if (term) nodes = nodes.filter((n) =>
      n.label.toLowerCase().includes(term) || n.id.toLowerCase().includes(term));
    return nodes;
  }

  reRenderGraph(): void {
    const g = this.graph();
    if (g && this.viewReady && this.graphSvgRef?.nativeElement) {
      this.zone.runOutsideAngular(() => this.renderGraph(g));
    }
  }

  selectGraphNode(node: GraphNode): void {
    this.selectedNode.set(node);
    const pid = this.store.selectedProject()?.kb_application_id;
    if (!pid) { this.neighbor.set({ node, nodes: [], edges: [], loading: false }); return; }
    this.neighbor.set({ node, nodes: [], edges: [], loading: true });
    this.svc.kbNeighbors(pid, node.id).subscribe({
      next: (r) => this.neighbor.set({ node, nodes: r.nodes ?? [], edges: r.edges ?? [], loading: false }),
      error: () => this.neighbor.set({ node, nodes: [], edges: [], loading: false }),
    });
  }

  askAboutNode(n: GraphNode): void {
    this.chatOpen.set(true);
    this.question = `What requirements and documents are related to "${n.label}" (${n.kind_label || n.kind})?`;
  }

  kindColor(kind: string): string {
    return this.KIND_COLOR[kind] ?? "#475569";
  }

  // ── D3 force graph ────────────────────────────────────────────────────────
  private renderGraph(g: OntologyGraph): void {
    const el = this.graphSvgRef?.nativeElement;
    if (!el) return;
    const w = el.clientWidth  || 800;
    const h = el.clientHeight || 520;

    d3.select(el).selectAll("*").remove();
    this.simulation?.stop();

    const nodes: D3Node[] = this.filteredNodes().map((n) => ({ ...n }));
    const nodeSet = new Set(nodes.map((n) => n.id));
    const links: D3Link[] = g.edges
      .filter((e) => nodeSet.has(e.source as string) && nodeSet.has(e.target as string))
      .map((e) => ({ source: e.source as string, target: e.target as string, label: e.label ?? "" }));

    const svg = d3.select(el);

    // zoom / pan
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.08, 5])
      .on("zoom", (ev) => container.attr("transform", ev.transform.toString()));
    svg.call(zoom);

    const container = svg.append("g");

    // arrow marker
    svg.append("defs").append("marker")
      .attr("id", "ko-arr").attr("viewBox", "0 -4 8 8")
      .attr("refX", 32).attr("refY", 0)
      .attr("markerWidth", 5).attr("markerHeight", 5).attr("orient", "auto")
      .append("path").attr("d", "M0,-4L8,0L0,4").attr("fill", "#2a4a72");

    // edges
    const edgeSel = container.append("g")
      .selectAll<SVGLineElement, D3Link>("line")
      .data(links).join("line")
      .attr("stroke", "#2a4a72").attr("stroke-width", 1.5)
      .attr("marker-end", "url(#ko-arr)");

    const edgeLabelSel = container.append("g")
      .selectAll<SVGTextElement, D3Link>("text")
      .data(links).join("text")
      .attr("fill", "#334155").attr("font-size", "8px").attr("text-anchor", "middle")
      .text((d) => d.label);

    // nodes
    const nodeSel = container.append("g")
      .selectAll<SVGGElement, D3Node>("g")
      .data(nodes).join("g")
      .style("cursor", "pointer")
      .call(
        d3.drag<SVGGElement, D3Node>()
          .on("start", (ev, d) => { if (!ev.active) sim.alphaTarget(0.3).restart(); d.fx = d.x; d.fy = d.y; })
          .on("drag",  (ev, d) => { d.fx = ev.x; d.fy = ev.y; })
          .on("end",   (ev, d) => { if (!ev.active) sim.alphaTarget(0); d.fx = null; d.fy = null; }),
      )
      .on("click", (ev, d) => { ev.stopPropagation(); this.zone.run(() => this.selectGraphNode(d)); });

    nodeSel.append("circle").attr("r", 22)
      .attr("fill", (d) => this.KIND_COLOR[d.kind] ?? "#475569")
      .attr("fill-opacity", 0.9)
      .attr("stroke", "#0f1c2e").attr("stroke-width", 1.5);

    nodeSel.append("text").attr("text-anchor", "middle").attr("dy", "0.35em")
      .attr("fill", "#fff").attr("font-size", "9px").attr("font-weight", "700")
      .attr("pointer-events", "none").text((d) => d.kind);

    nodeSel.append("text").attr("text-anchor", "middle").attr("dy", "38px")
      .attr("fill", "#cbd5e1").attr("font-size", "10px").attr("pointer-events", "none")
      .text((d) => d.label.length > 18 ? d.label.slice(0, 15) + "…" : d.label);

    svg.on("click", () => this.zone.run(() => { this.selectedNode.set(null); this.neighbor.set(null); }));

    // force simulation
    const sim = d3.forceSimulation<D3Node>(nodes)
      .force("link",    d3.forceLink<D3Node, D3Link>(links).id((d) => d.id).distance(120))
      .force("charge",  d3.forceManyBody<D3Node>().strength(-300))
      .force("center",  d3.forceCenter<D3Node>(w / 2, h / 2))
      .force("collide", d3.forceCollide<D3Node>(38));

    this.simulation = sim;

    sim.on("tick", () => {
      edgeSel
        .attr("x1", (d) => (d.source as D3Node).x ?? 0)
        .attr("y1", (d) => (d.source as D3Node).y ?? 0)
        .attr("x2", (d) => (d.target as D3Node).x ?? 0)
        .attr("y2", (d) => (d.target as D3Node).y ?? 0);
      edgeLabelSel
        .attr("x", (d) => (((d.source as D3Node).x ?? 0) + ((d.target as D3Node).x ?? 0)) / 2)
        .attr("y", (d) => (((d.source as D3Node).y ?? 0) + ((d.target as D3Node).y ?? 0)) / 2);
      nodeSel.attr("transform", (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });
  }

  // ── chat ─────────────────────────────────────────────────────────────────
  chatContextLabel(): string {
    const a = this.selectedArtifact();
    const n = this.selectedNode();
    if (this.activeView() === "documents" && a) return `📄 ${a.artifact_type}`;
    if (this.activeView() === "ontology"  && n) return `🕸 ${n.label} (${n.kind_label || n.kind})`;
    return "";
  }

  send(): void {
    const q = this.question.trim();
    if (!q || this.sending()) return;
    const pid  = this.store.selectedProject()?.kb_application_id ?? "";
    const wsId = this.store.selectedId() ?? undefined;

    const ctx = this.chatContextLabel();
    const enriched = ctx ? `[Context: ${ctx}]\n${q}` : q;

    this.chatHistory.update((h) => [...h, { role: "user", text: q, ts: Date.now() }]);
    this.question = "";
    this.sending.set(true);
    this.svc.chat(pid, enriched, wsId).subscribe({
      next:  (r) => {
        const text = (r?.["answer"] || r?.["text"] || "(no response)") as string;
        this.chatHistory.update((h) => [...h, { role: "kb", text, ts: Date.now() }]);
        this.sending.set(false);
      },
      error: () => {
        this.chatHistory.update((h) => [...h, {
          role: "kb", text: "Error contacting the knowledge base.", ts: Date.now(),
        }]);
        this.sending.set(false);
      },
    });
  }

  onChatKey(event: Event): void {
    if (!(event as KeyboardEvent).shiftKey) { event.preventDefault(); this.send(); }
  }

  formatTime(ts: number): string {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  render(text: string): SafeHtml {
    return this.sanitizer.bypassSecurityTrustHtml(this.toHtml(text));
  }

  // ── markdown renderer ─────────────────────────────────────────────────────
  private toHtml(text: string): string {
    const lines = text.split("\n");
    const out: string[] = [];
    let inCode = false, inList = false;
    for (const line of lines) {
      if (line.startsWith("```")) {
        if (inList) { out.push("</ul>"); inList = false; }
        inCode ? out.push("</code></pre>") : out.push('<pre class="apm-md-pre"><code>');
        inCode = !inCode; continue;
      }
      if (inCode) { out.push(this.esc(line)); continue; }
      if (line.startsWith("### ")) { if (inList) { out.push("</ul>"); inList=false; } out.push(`<h5 class="apm-md-h">${this.fmt(line.slice(4))}</h5>`); continue; }
      if (line.startsWith("## "))  { if (inList) { out.push("</ul>"); inList=false; } out.push(`<h4 class="apm-md-h">${this.fmt(line.slice(3))}</h4>`); continue; }
      if (line.startsWith("# "))   { if (inList) { out.push("</ul>"); inList=false; } out.push(`<h3 class="apm-md-h">${this.fmt(line.slice(2))}</h3>`); continue; }
      if (/^[-*] /.test(line)) { if (!inList) { out.push('<ul class="apm-md-list">'); inList=true; } out.push(`<li>${this.fmt(line.slice(2))}</li>`); continue; }
      if (inList) { out.push("</ul>"); inList=false; }
      if (!line.trim()) { out.push('<div class="apm-md-gap"></div>'); continue; }
      out.push(`<p class="apm-md-p">${this.fmt(line)}</p>`);
    }
    if (inList) out.push("</ul>");
    if (inCode)  out.push("</code></pre>");
    return out.join("");
  }

  private esc(s: string): string {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  private fmt(s: string): string {
    s = this.esc(s);
    s = s.replace(/`([^`]+)`/g,        '<code class="apm-md-code">$1</code>');
    s = s.replace(/\*\*([^*]+)\*\*/g,  "<strong>$1</strong>");
    s = s.replace(/\*([^*\n]+)\*/g,    "<em>$1</em>");
    s = s.replace(/\[(\d+)\]/g,        '<sup class="apm-md-cite">$1</sup>');
    return s;
  }

  // ── downloads ─────────────────────────────────────────────────────────────
  downloadAll(): void {
    const pid = this.store.selectedProject()?.kb_application_id ?? "project";
    this.triggerDownload(
      this.buildHtmlDoc(this.chatHistory(), `${pid} — KB Chat`,
        `${this.chatHistory().length} message(s) · ${new Date().toLocaleString()}`),
      `kb-chat-${pid}.html`,
    );
  }

  downloadMessage(m: ChatMessage, i: number): void {
    const h = this.chatHistory();
    const msgs = i > 0 && h[i - 1]?.role === "user" ? [h[i - 1], m] : [m];
    const pid = this.store.selectedProject()?.kb_application_id ?? "project";
    this.triggerDownload(
      this.buildHtmlDoc(msgs, `${pid} — KB Response`, `Exported ${new Date().toLocaleString()}`),
      `kb-response-${i + 1}.html`,
    );
  }

  private buildHtmlDoc(messages: ChatMessage[], title: string, subtitle: string): string {
    const rows = messages.map((m) => {
      const t = this.formatTime(m.ts);
      return m.role === "user"
        ? `<div class="msg user"><div class="msg-body"><div class="meta">You · ${t}</div><div class="bubble user">${this.esc(m.text)}</div></div><div class="avatar user">👤</div></div>`
        : `<div class="msg kb"><div class="avatar kb">🗄</div><div class="msg-body"><div class="meta">Knowledge Base · ${t}</div><div class="bubble kb">${this.toHtml(m.text)}</div></div></div>`;
    }).join("\n");
    return `<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${this.esc(title)}</title>
<style>body{font-family:Inter,sans-serif;background:#0b1220;color:#e2e8f0;padding:40px 20px}
.container{max-width:820px;margin:0 auto}h1{font-size:20px;color:#fff;margin-bottom:4px}
p.sub{font-size:12px;color:#64748b;margin-bottom:24px}
.msg{display:flex;gap:14px;margin-bottom:24px;align-items:flex-start}
.msg.user{flex-direction:row-reverse}
.avatar{width:36px;height:36px;border-radius:50%;background:#1e293b;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.avatar.user{background:linear-gradient(135deg,#003865,#0077c8)}
.msg-body{flex:1}.msg.user .msg-body{align-items:flex-end;display:flex;flex-direction:column}
.meta{font-size:11px;color:#64748b;margin-bottom:6px}
.bubble{padding:12px 16px;border-radius:12px;font-size:14px;line-height:1.7;max-width:90%}
.bubble.user{background:linear-gradient(135deg,#003865,#0077c8);color:#fff}
.bubble.kb{background:#1a2740;border:1px solid #2a4a72}</style>
</head><body><div class="container"><h1>${this.esc(title)}</h1><p class="sub">${this.esc(subtitle)}</p>${rows}</div></body></html>`;
  }

  private triggerDownload(html: string, filename: string): void {
    const a = Object.assign(document.createElement("a"), {
      href: URL.createObjectURL(new Blob([html], { type: "text/html;charset=utf-8" })),
      download: filename,
    });
    a.click();
    URL.revokeObjectURL(a.href);
  }
}
