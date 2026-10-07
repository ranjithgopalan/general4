import { Component, HostBinding, OnInit, ViewEncapsulation } from "@angular/core";
import { Router } from "@angular/router";

import { AidlcPlatformMgmtService, Project, Stage } from "../services/aidlc-platform-mgmt.service";
import { openChatWindow } from "../services/chat-window";
import { WorkspaceStore } from "../services/workspace.store";

/**
 * AIDLC Platform Management — console shell. Dark left sidebar carries the project tree (Dashboard →
 * project → Global Workspace stages → Mini/EPICs); the main area is a router-outlet. Selecting a
 * project expands the tree and opens the pipeline; Dashboard collapses it. ViewEncapsulation.None so
 * the shared `.apm-*` page styles apply to routed child views (house pattern, mirrors re-console).
 */
@Component({
  selector: "app-aidlc-platform-mgmt",
  templateUrl: "./aidlc-platform-mgmt.component.html",
  styleUrl: "./aidlc-platform-mgmt.component.scss",
  encapsulation: ViewEncapsulation.None,
})
export class AidlcPlatformMgmtComponent implements OnInit {
  @HostBinding("class.full-screen") readonly fullScreen = true;

  private readonly root = "/aidlc-platform-management";

  constructor(
    readonly store: WorkspaceStore,
    private readonly router: Router,
    private readonly svc: AidlcPlatformMgmtService,
  ) {}

  ngOnInit(): void {
    this.store.load();
  }

  goDashboard(): void {
    this.store.clearProject();
    this.router.navigate([this.root, "dashboard"]);
  }

  goGlobalLibrary(): void {
    this.store.clearProject();
    this.router.navigate([this.root, "global-library"]);
  }

  isGlobalLibrary(): boolean {
    return this.router.url.includes("/global-library");
  }

  openOnboard(): void {
    this.store.clearProject();
    this.router.navigate([this.root, "onboard"]);
  }

  openProject(p: Project): void {
    this.store.selectProject(p.kb_application_id);
    this.router.navigate([this.root, "pipeline"]);
  }

  openWorkspace(id: string): void {
    this.store.select(id);
    this.router.navigate([this.root, "pipeline"]);
  }

  selectStage(key: string): void {
    this.store.selectStage(key);
  }

  // Stage dot glyph/tone for the sidebar tree.
  dot(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "✔";
    if (s.state === "waiting_for_approval") return "⚑";
    if (s.ready) return "▶";
    return "○";
  }
  tone(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "good";
    if (s.state === "waiting_for_approval") return "warn";
    return "idle";
  }

  /** Open the grounded Q&A chat in a SEPARATE window (backend: POST {base}/projects/{id}/chat). */
  openChat(): void {
    const pid = this.store.selectedProject()?.kb_application_id ?? "";
    const token = (typeof localStorage !== "undefined" && localStorage.getItem("access_token")) || "";
    openChatWindow(this.svc.base, pid, token);
  }

  /** Navigate to the Pipeline Summary view (global + arch + per-EPIC stage dots). */
  openPipelineSummary(): void {
    this.router.navigate([this.root, "pipeline-summary"]);
  }

  /** Navigate to the unified Knowledge & Ontology workspace. */
  openKnowledgeOntology(): void {
    this.router.navigate([this.root, "knowledge-ontology"]);
  }

  /** Navigate to the project's ASP dependency view (uploaded ASP files on the Gear ID's dependency graph). */
  openAspDependencies(): void {
    this.router.navigate([this.root, "asp-dependencies"]);
  }

  /** @deprecated Use openKnowledgeOntology(). Kept for any template references. */
  openKnowledgeBase(): void { this.openKnowledgeOntology(); }

  /** @deprecated Use openKnowledgeOntology(). Kept for any template references. */
  openOntology(): void { this.openKnowledgeOntology(); }

  /**
   * Filter global workspace stages to specific agent group(s), then group them.
   * Lets the sidebar show Business Analyst stages before architecture and
   * Scrum Master (EPIC Set) after architecture — always visible regardless of
   * which workspace is currently selected.
   */
  agentGroupsFor(stages: Stage[], groups: string[]): { label: string; stages: Stage[] }[] {
    const filtered = stages.filter(s => groups.includes(s.agent_group ?? ""));
    return this.agentGroups(filtered);
  }

  /** Navigate to the global workspace and select a specific stage (e.g. EPIC Set). */
  selectGlobalStage(key: string): void {
    const gId = this.store.globalWorkspace()?.id;
    if (!gId) return;
    if (this.store.selectedId() !== gId) this.store.select(gId);
    this.store.selectStage(key);
    this.router.navigate([this.root, "pipeline"]);
  }

  /**
   * Navigate to a Mini (EPIC) workspace pipeline board and select a per-EPIC stage
   * (Feature / User Story / Coverage Validation). Keeps context inside the EPIC —
   * does NOT jump to the global workspace even though the stage runs globally.
   */
  selectMiniEpicStage(miniId: string, key: string): void {
    if (this.store.selectedId() !== miniId) this.store.select(miniId);
    this.store.selectStage(key);
    this.router.navigate([this.root, "pipeline"]);
  }

  /** Fixed display order for agent groups in the sidebar.
   *  Groups not listed here sort to the end in declaration order. */
  private readonly AGENT_GROUP_ORDER = [
    "Scrum Master Agent",
    "Business Analyst Agent",
    "EPIC Assembler Agent",  // epic-assemble stage (first in assembler pipeline)
    "Developer Agent",       // db-integration runs after assembly, before testing
    "Tester Agent",
    "Testing Agent",         // ui-smoke + ui-e2e (post-assembly, assembler pipeline)
    "PR Merge Agent",        // pr-merge (assembler pipeline)
    "DevOps Agent",          // deployment
    "Traceability Agent",
  ];

  /** Frontend-only display overrides for agent group labels in the mini workspace sidebar. */
  private readonly AGENT_GROUP_DISPLAY: Record<string, string> = {
    "Tester Agent": "Unit Test Agent",
  };

  /** Frontend-only display overrides for stage names shown in the sidebar.
   *  Covers both long-form (ui-smoke-test) and short-form (ui-smoke) backend key variants. */
  private readonly STAGE_NAME_DISPLAY: Record<string, string> = {
    "ui-smoke-test": "UI Jasmine Test",
    "ui-smoke":      "UI Jasmine Test",
  };

  /**
   * Group sidebar stages by agent_group, sorted by AGENT_GROUP_ORDER so
   * Business Analyst always appears before Developer regardless of YAML seq order.
   * Falls back to "Other" when agent_group is absent (old backend) — no crash.
   */
  agentGroups(stages: Stage[]): { label: string; stages: Stage[] }[] {
    const map = new Map<string, Stage[]>();
    for (const s of stages) {
      const key = s.agent_group ?? "Other";
      if (!map.has(key)) map.set(key, []);
      map.get(key)?.push(s);
    }
    return Array.from(map.entries())
      .map(([label, stageList]) => ({ label, stages: stageList }))
      .sort((a, b) => {
        const ai = this.AGENT_GROUP_ORDER.indexOf(a.label);
        const bi = this.AGENT_GROUP_ORDER.indexOf(b.label);
        return (ai === -1 ? 999 : ai) - (bi === -1 ? 999 : bi);
      });
  }

  displayGroupLabel(label: string): string {
    return this.AGENT_GROUP_DISPLAY[label] ?? label;
  }

  displayStageName(s: Stage): string {
    return this.STAGE_NAME_DISPLAY[s.key] ?? s.name;
  }

  /** Mini workspace stages excluding UI E2E and DB Integration — both moved to the
   *  EPIC Assembler workspace. Both long-form (ui-e2e-testing) and short-form (ui-e2e)
   *  backend keys are excluded for UI E2E. */
  miniStagesFiltered(): Stage[] {
    const ASSEMBLER_STAGE_KEYS = new Set(["ui-e2e-testing", "ui-e2e", "db-integration"]);
    return this.store.stages().filter(s => !ASSEMBLER_STAGE_KEYS.has(s.key));
  }

  /**
   * Returns true for stage keys that run at global/Bedrock scope but are
   * displayed inside each EPIC Mini Workspace (not in the global sections).
   * These stages show a blue GLOBAL badge.
   */
  private readonly _globalScopeStages = new Set(['feature', 'user-story', 'coverage']);
  isGlobalScope(key: string): boolean {
    return this._globalScopeStages.has(key);
  }

  /**
   * Global workspace stages EXCLUDING per-EPIC stages (feature/user-story/coverage).
   * Used by the Business Analyst and Scrum Master sidebar sections so those
   * stages do not appear at programme level — they live under each EPIC instead.
   */
  globalSidebarStages(): import('../services/aidlc-platform-mgmt.service').Stage[] {
    return this.store.globalStages().filter(s => !this.isGlobalScope(s.key));
  }

  /**
   * The per-EPIC stages from the global pipeline (feature, user-story, coverage).
   * Shown at the top of each expanded EPIC workspace with a GLOBAL badge.
   */
  perEpicStages(): import('../services/aidlc-platform-mgmt.service').Stage[] {
    return this.store.globalStages().filter(s => this.isGlobalScope(s.key));
  }

  /** Mini workspaces excluding the EPIC Assembler — used in the per-EPIC sidebar loop
   *  so the assembler workspace doesn't appear twice (once here, once in its own section). */
  perEpicWorkspaces() {
    return this.store.miniWorkspaces().filter(w => w.pipeline !== 'uw-cr-epic-assembler');
  }

  /** Assembler workspace stage list with db-wiring injected after epic-assemble
   *  when the backend hasn't added it yet (pre-existing workspace created before YAML update). */
  assemblerSidebarStages(): Stage[] {
    const stages = this.store.stages();
    if (stages.some(s => s.key === 'db-wiring')) return stages;
    const dbStage: Stage = { key: 'db-wiring', name: 'DB Integration', seq: 2, state: 'idle', agent_group: 'Developer Agent' };
    const idx = stages.findIndex(s => s.key === 'epic-assemble');
    const result = [...stages];
    result.splice(idx >= 0 ? idx + 1 : result.length, 0, dbStage);
    return result;
  }

  /** Open the EPIC Assembler Workspace.
   *  If the workspace already exists, selects it directly.
   *  If not yet created (recovery/testing), calls the backend to create it first. */
  openEpicAssemblerWs(): void {
    const appId = this.store.selectedProject()?.kb_application_id;
    if (!appId) return;
    const existing = this.store.assemblerWorkspace();
    if (existing) {
      this.openWorkspace(existing.id);
      return;
    }
    this.svc.ensureAssembler(appId).subscribe((ws) => {
      if (!ws) return;
      this.store.refreshWorkspaces();
      this.openWorkspace(ws.id);
    });
  }

  /** Navigate to the Workspace Traceability page for a given workspace ID. */
  openTraceability(workspaceId: string): void {
    this.router.navigate([this.root, "workspaces", workspaceId, "traceability"]);
  }
}
