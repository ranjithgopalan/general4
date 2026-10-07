import { Injectable, computed, signal } from "@angular/core";

import { AidlcPlatformMgmtService, Principal, Project, Stage, Workspace } from "./aidlc-platform-mgmt.service";

/**
 * Single source of truth for the console (signals), mirroring the forward-engineering WorkspaceStore.
 * One project at a time; selecting a project loads its workspaces; selecting a workspace loads its
 * stages. Clearing the project (Dashboard) collapses the sidebar tree.
 */
@Injectable({ providedIn: "root" })
export class WorkspaceStore {
  readonly principal = signal<Principal | null>(null);
  readonly projects = signal<Project[]>([]);
  readonly loading = signal(false);
  readonly selectedProjectId = signal<string | null>(null);
  readonly workspaces = signal<Workspace[]>([]);
  readonly selectedId = signal<string | null>(null);
  readonly stages = signal<Stage[]>([]);
  readonly selectedStageKey = signal<string | null>(null);
  /** Global workspace stages — persists when other workspaces are selected so the sidebar
   *  can always show Business Analyst and Scrum Master stages without requiring global WS
   *  to be the active selection. Populated once on project load and refreshed on re-select. */
  readonly globalStages = signal<Stage[]>([]);
  readonly globalPipeline = signal<string>("");

  /** KB state — set by pipeline.component when the kb artifact is loaded */
  readonly kbVersion    = signal<string | null>(null);
  readonly kbStatus     = signal<'STAGING' | 'ACTIVE' | 'SUPERSEDED' | null>(null);
  readonly kbNodeCount  = signal<number>(0);
  readonly kbEdgeCount  = signal<number>(0);
  readonly kbChunkCount = signal<number>(0);

  readonly selectedProject = computed(() =>
    this.projects().find((p) => p.kb_application_id === this.selectedProjectId()) ?? null,
  );
  readonly globalWorkspace = computed(() => this.workspaces().find((w) => w.tier === "global") ?? null);
  readonly architectureWorkspace = computed(() => this.workspaces().find((w) => w.tier === "architecture") ?? null);
  readonly miniWorkspaces = computed(() => this.workspaces().filter((w) => w.tier === "mini"));
  /** The single Epic Assembler Workspace — null until the backend creates it after all
   *  per-EPIC mini workspaces complete their last stage. Identified by pipeline name
   *  (not tier) because the assembler also carries tier="mini" in the backend. */
  readonly assemblerWorkspace = computed(() =>
    this.workspaces().find((w) => w.pipeline === "uw-cr-epic-assembler") ?? null
  );

  constructor(private readonly api: AidlcPlatformMgmtService) {}

  load(): void {
    // Show skeleton only on cold load; on return visits show stale data while refreshing silently.
    if (!this.projects().length) this.loading.set(true);
    this.api.whoami().subscribe((p) => this.principal.set(p));
    this.api.listProjects().subscribe({
      next: (r) => { this.projects.set(r); this.loading.set(false); },
      error: ()  => { this.loading.set(false); },
    });
  }

  selectProject(id: string): void {
    this.selectedProjectId.set(id);
    this.selectedId.set(null);
    this.selectedStageKey.set(null);
    this.stages.set([]);
    this.api.listWorkspaces(id).subscribe((ws) => {
      this.workspaces.set(ws);
      const g = ws.find((w) => w.tier === "global");
      if (g) this.select(g.id);
    });
  }

  select(workspaceId: string): void {
    this.selectedId.set(workspaceId);
    this.selectedStageKey.set(null);
    this.api.workspaceStages(workspaceId).subscribe((d) => {
      const stgs = d?.stages ?? [];
      this.stages.set(stgs);
      // Cache global stages so the sidebar can display BA + Scrum Master rows
      // even when a different workspace (architecture, mini) is active.
      if (workspaceId === this.globalWorkspace()?.id) {
        this.globalStages.set(stgs);
        if (d?.pipeline) this.globalPipeline.set(d.pipeline);
      }
    });
  }

  selectStage(key: string): void {
    this.selectedStageKey.set(key);
  }

  /**
   * Re-fetch the workspace list for the current project.
   * Call after an approval that may fan out new workspaces (e.g. EPIC Set → mini workspaces).
   */
  refreshWorkspaces(): void {
    const id = this.selectedProjectId();
    if (!id) return;
    this.api.listWorkspaces(id).subscribe((ws) => this.workspaces.set(ws));
  }

  /**
   * Refresh global workspace stages without changing the selected workspace.
   * Call from the mini workspace poll loop so per-EPIC stage state stays live
   * even while a mini workspace is active.
   */
  refreshGlobalStages(): void {
    const g = this.globalWorkspace();
    if (!g) return;
    this.api.workspaceStages(g.id).subscribe((d) => {
      this.globalStages.set(d?.stages ?? []);
      if (d?.pipeline) this.globalPipeline.set(d.pipeline);
    });
  }

  clearProject(): void {
    this.selectedProjectId.set(null);
    this.selectedId.set(null);
    this.selectedStageKey.set(null);
    this.workspaces.set([]);
    this.stages.set([]);
  }
}
