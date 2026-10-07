import { Component, OnInit, signal } from "@angular/core";
import {
  AidlcPlatformMgmtService,
  GraphNode,
  GraphEdge,
  LayerBand,
  OntologyGraph,
} from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

interface NeighborPanel {
  node: GraphNode;
  nodes: GraphNode[];
  edges: GraphEdge[];
  loading: boolean;
}

@Component({
  selector: "app-apm-ontology",
  template: `
    <div class="apm-crumb">Knowledge Graph · Ontology</div>

    <div class="apm-hdr">
      <div class="apm-hdr-icon">🕸</div>
      <div>
        <h2>Knowledge Graph Ontology</h2>
        <p class="sub">
          Nodes · edges · layer bands derived from the active KB version
          <span *ngIf="graph()?.kb_version" style="margin-left:8px" class="apm-chip">
            {{ graph()?.kb_version }}
          </span>
        </p>
      </div>
      <button class="apm-btn" style="margin-left:auto" (click)="load()" [disabled]="loading()">
        {{ loading() ? '…' : '⟳ Refresh' }}
      </button>
    </div>

    <!-- error -->
    <div class="apm-card" *ngIf="error()">
      <div class="apm-card-body sub" style="color:#f87171">{{ error() }}</div>
    </div>

    <!-- loading skeleton -->
    <div class="apm-card" *ngIf="loading() && !graph()">
      <div class="apm-card-body sub">Loading knowledge graph…</div>
    </div>

    <ng-container *ngIf="graph() as g">

      <!-- ── stats row ── -->
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px">
        <div class="apm-stat-tile" [class.ont-stat-active]="!activeKind()"
             style="cursor:pointer" (click)="activeKind.set(null)" title="Show all nodes">
          <div class="apm-stat-val">{{ g.nodes.length }}</div>
          <div class="apm-stat-lbl">Nodes</div>
        </div>
        <div class="apm-stat-tile">
          <div class="apm-stat-val">{{ g.edges.length }}</div>
          <div class="apm-stat-lbl">Edges</div>
        </div>
        <div class="apm-stat-tile" *ngFor="let k of g.kinds"
             [class.ont-stat-active]="activeKind() === k.kind"
             style="cursor:pointer" (click)="activeKind.set(k.kind)"
             title="Filter by {{ k.kind_label || k.kind }}">
          <div class="apm-stat-val">{{ k.count }}</div>
          <div class="apm-stat-lbl">{{ k.kind_label || k.kind }}</div>
        </div>
      </div>

      <!-- ── search + kind filter ── -->
      <div class="apm-card" style="margin-bottom:12px">
        <div class="apm-card-body" style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
          <input class="apm-input" style="flex:1;min-width:200px"
            placeholder="Search nodes by label or id…"
            [(ngModel)]="searchTerm" />
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="apm-chip" [class.ok]="!activeKind()"
                    (click)="activeKind.set(null)">All</button>
            <button class="apm-chip" *ngFor="let k of g.kinds"
                    [class.ok]="activeKind() === k.kind"
                    (click)="activeKind.set(k.kind)">
              {{ k.kind_label || k.kind }} ({{ k.count }})
            </button>
          </div>
        </div>
      </div>

      <!-- ── layer bands ── -->
      <ng-container *ngFor="let band of orderedBands(g)">
        <ng-container *ngIf="nodesForBand(band) as bandNodes">
          <div class="apm-card" *ngIf="bandNodes.length">
            <div class="apm-card-head">
              <h4>{{ band.title }}</h4>
              <span class="sub">{{ bandNodes.length }} node{{ bandNodes.length !== 1 ? 's' : '' }}</span>
            </div>
            <div class="apm-card-body">
              <div class="ont-node-grid">
                <div class="ont-node-card" *ngFor="let n of bandNodes"
                     [class.ont-node-selected]="selectedNode()?.id === n.id"
                     (click)="selectNode(n)">
                  <div class="ont-node-top">
                    <span class="apm-chip" style="font-size:10px">{{ n.kind }}</span>
                    <span class="ont-node-origin" *ngIf="n.origin === 'forward'">⟳ synced</span>
                  </div>
                  <div class="ont-node-label" [title]="n.id">{{ n.label }}</div>
                  <div class="ont-node-id sub">{{ n.id }}</div>
                  <div class="ont-node-summary sub" *ngIf="n.summary">{{ n.summary }}</div>
                </div>
              </div>
            </div>
          </div>
        </ng-container>
      </ng-container>

      <!-- unassigned (no band) -->
      <ng-container *ngIf="nodesWithoutBand(g) as loose">
        <div class="apm-card" *ngIf="loose.length">
          <div class="apm-card-head">
            <h4>Other</h4>
            <span class="sub">{{ loose.length }} node{{ loose.length !== 1 ? 's' : '' }}</span>
          </div>
          <div class="apm-card-body">
            <div class="ont-node-grid">
              <div class="ont-node-card" *ngFor="let n of loose"
                   [class.ont-node-selected]="selectedNode()?.id === n.id"
                   (click)="selectNode(n)">
                <div class="ont-node-top">
                  <span class="apm-chip" style="font-size:10px">{{ n.kind }}</span>
                </div>
                <div class="ont-node-label">{{ n.label }}</div>
                <div class="ont-node-id sub">{{ n.id }}</div>
                <div class="ont-node-summary sub" *ngIf="n.summary">{{ n.summary }}</div>
              </div>
            </div>
          </div>
        </div>
      </ng-container>

      <!-- ── edge list (compact) ── -->
      <div class="apm-card" *ngIf="g.edges.length">
        <div class="apm-card-head" (click)="edgesOpen.set(!edgesOpen())" style="cursor:pointer">
          <h4>{{ edgesOpen() ? '▾' : '▸' }} Edges ({{ g.edges.length }})</h4>
        </div>
        <div class="apm-card-body" *ngIf="edgesOpen()">
          <div class="ont-edge-table">
            <div class="ont-edge-row ont-edge-hdr">
              <span>Source</span><span>Relation</span><span>Target</span>
            </div>
            <div class="ont-edge-row" *ngFor="let e of g.edges">
              <span class="sub" [title]="e.source">{{ e.source }}</span>
              <span class="apm-chip" style="font-size:10px">{{ e.label }}</span>
              <span class="sub" [title]="e.target">{{ e.target }}</span>
            </div>
          </div>
        </div>
      </div>
    </ng-container>

    <!-- ── neighbor drill-down drawer ── -->
    <ng-container *ngIf="neighbor()">
      <div class="apm-modal-bg" (click)="neighbor.set(null)"></div>
      <aside class="apm-drawer">
        <div class="apm-kb-chat-header">
          <div class="apm-kb-chat-header-left">
            <div class="apm-kb-chat-logo">🕸</div>
            <div>
              <div class="apm-kb-chat-title">{{ neighbor()!.node.label }}</div>
              <div class="apm-kb-chat-sub">{{ neighbor()!.node.kind_label || neighbor()!.node.kind }} · {{ neighbor()!.node.id }}</div>
            </div>
          </div>
          <button class="apm-x" (click)="neighbor.set(null)">×</button>
        </div>

        <div style="padding:16px;overflow-y:auto;flex:1">
          <!-- summary -->
          <p class="sub" *ngIf="neighbor()!.node.summary" style="margin-bottom:16px">
            {{ neighbor()!.node.summary }}
          </p>

          <div *ngIf="neighbor()!.loading" class="sub">Loading neighbors…</div>

          <!-- neighbor nodes -->
          <ng-container *ngIf="!neighbor()!.loading && neighbor()!.nodes.length">
            <div style="font-weight:700;font-size:12px;margin-bottom:8px;color:#94a3b8">
              CONNECTED NODES ({{ neighbor()!.nodes.length }})
            </div>
            <div class="ont-node-grid" style="grid-template-columns:1fr 1fr">
              <div class="ont-node-card" *ngFor="let n of neighbor()!.nodes" (click)="selectNode(n)">
                <div class="ont-node-top">
                  <span class="apm-chip" style="font-size:10px">{{ n.kind }}</span>
                </div>
                <div class="ont-node-label">{{ n.label }}</div>
                <div class="ont-node-id sub">{{ n.id }}</div>
              </div>
            </div>
          </ng-container>

          <!-- neighbor edges -->
          <ng-container *ngIf="!neighbor()!.loading && neighbor()!.edges.length">
            <div style="font-weight:700;font-size:12px;margin:16px 0 8px;color:#94a3b8">
              RELATIONSHIPS ({{ neighbor()!.edges.length }})
            </div>
            <div class="ont-edge-table">
              <div class="ont-edge-row" *ngFor="let e of neighbor()!.edges">
                <span class="sub" style="font-size:11px" [title]="e.source">{{ e.source }}</span>
                <span class="apm-chip" style="font-size:10px">{{ e.label }}</span>
                <span class="sub" style="font-size:11px" [title]="e.target">{{ e.target }}</span>
              </div>
            </div>
          </ng-container>

          <div *ngIf="!neighbor()!.loading && !neighbor()!.nodes.length" class="sub">
            No connected nodes found.
          </div>
        </div>
      </aside>
    </ng-container>

    <style>
      .apm-stat-tile {
        background: var(--panel-bg, #1a2740);
        border: 1px solid var(--border, #2a4a72);
        border-radius: 10px;
        padding: 12px 20px;
        min-width: 90px;
        text-align: center;
        transition: border-color 0.15s, box-shadow 0.15s;
      }
      .apm-stat-tile[style*="cursor"]:hover { border-color: #0077c8; box-shadow: 0 0 0 2px rgba(0,119,200,.15); }
      .ont-stat-active { border-color: #0077c8 !important; background: #0d2240 !important; }
      .ont-stat-active .apm-stat-val { color: #60a5fa; }
      .apm-stat-val { font-size: 22px; font-weight: 700; color: #e2e8f0; }
      .apm-stat-lbl { font-size: 11px; color: #64748b; margin-top: 2px; }
      button.apm-chip { cursor: pointer; border: 1px solid transparent; transition: border-color 0.15s, background 0.15s; }
      button.apm-chip:hover { border-color: #0077c8; }
      button.apm-chip.ok { background: #0d2240; color: #60a5fa; border-color: #0077c8; }

      .apm-input {
        background: var(--panel-bg, #1a2740);
        border: 1px solid var(--border, #2a4a72);
        border-radius: 8px;
        color: #e2e8f0;
        padding: 6px 12px;
        font-size: 13px;
        outline: none;
      }
      .apm-input:focus { border-color: #0077c8; }

      .ont-node-grid {
        display: grid;
        grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
        gap: 10px;
      }
      .ont-node-card {
        background: #0f1c2e;
        border: 1px solid #1e3a5f;
        border-radius: 8px;
        padding: 10px 12px;
        cursor: pointer;
        transition: border-color 0.15s;
      }
      .ont-node-card:hover { border-color: #0077c8; }
      .ont-node-selected { border-color: #0077c8 !important; background: #0d2240 !important; }
      .ont-node-top { display: flex; justify-content: space-between; margin-bottom: 6px; }
      .ont-node-label { font-size: 13px; font-weight: 600; color: #e2e8f0; margin-bottom: 3px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .ont-node-id { font-size: 10px; margin-bottom: 4px; }
      .ont-node-summary { font-size: 11px; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
      .ont-node-origin { font-size: 10px; color: #22d3ee; }

      .ont-edge-table { display: flex; flex-direction: column; gap: 4px; }
      .ont-edge-row { display: grid; grid-template-columns: 1fr auto 1fr; gap: 8px; align-items: center; padding: 4px 0; border-bottom: 1px solid #1e293b; font-size: 11px; }
      .ont-edge-hdr { font-weight: 700; color: #64748b; font-size: 10px; letter-spacing: .04em; }
    </style>
  `,
})
export class OntologyComponent implements OnInit {
  readonly graph    = signal<OntologyGraph | null>(null);
  readonly loading  = signal(false);
  readonly error    = signal<string | null>(null);
  readonly activeKind = signal<string | null>(null);
  readonly selectedNode = signal<GraphNode | null>(null);
  readonly neighbor = signal<NeighborPanel | null>(null);
  readonly edgesOpen = signal(false);
  searchTerm = "";

  constructor(
    readonly store: WorkspaceStore,
    private readonly svc: AidlcPlatformMgmtService,
  ) {}

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.svc.ontologyGraph().subscribe({
      next: (g) => { this.graph.set(g); this.loading.set(false); },
      error: (e) => {
        this.error.set("Failed to load knowledge graph: " + (e?.message ?? "unknown error"));
        this.loading.set(false);
      },
    });
  }

  orderedBands(g: OntologyGraph): LayerBand[] {
    return [...(g.layers ?? [])].sort((a, b) => a.order - b.order);
  }

  nodesForBand(band: LayerBand): GraphNode[] {
    return this.filteredNodes().filter(n => band.kinds.includes(n.kind));
  }

  nodesWithoutBand(g: OntologyGraph): GraphNode[] {
    const allBandKinds = new Set((g.layers ?? []).flatMap(b => b.kinds));
    return this.filteredNodes().filter(n => !allBandKinds.has(n.kind));
  }

  filteredNodes(): GraphNode[] {
    const g = this.graph();
    if (!g) return [];
    let nodes = g.nodes;
    if (this.activeKind()) nodes = nodes.filter(n => n.kind === this.activeKind());
    const term = this.searchTerm.toLowerCase().trim();
    if (term) nodes = nodes.filter(n =>
      n.label.toLowerCase().includes(term) || n.id.toLowerCase().includes(term)
    );
    return nodes;
  }

  selectNode(node: GraphNode): void {
    this.selectedNode.set(node);
    const pid = this.store.selectedProject()?.kb_application_id;
    if (!pid) {
      this.neighbor.set({ node, nodes: [], edges: [], loading: false });
      return;
    }
    this.neighbor.set({ node, nodes: [], edges: [], loading: true });
    this.svc.kbNeighbors(pid, node.id).subscribe({
      next: (r) => this.neighbor.set({ node, nodes: r.nodes ?? [], edges: r.edges ?? [], loading: false }),
      error: ()  => this.neighbor.set({ node, nodes: [], edges: [], loading: false }),
    });
  }
}
