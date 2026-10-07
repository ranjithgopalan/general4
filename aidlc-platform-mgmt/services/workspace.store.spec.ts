import { TestBed } from "@angular/core/testing";
import { of } from "rxjs";

import { AidlcPlatformMgmtService } from "./aidlc-platform-mgmt.service";
import { WorkspaceStore } from "./workspace.store";

describe("WorkspaceStore", () => {
  let store: WorkspaceStore;
  let api: jasmine.SpyObj<AidlcPlatformMgmtService>;

  beforeEach(() => {
    api = jasmine.createSpyObj("AidlcPlatformMgmtService", [
      "whoami",
      "listProjects",
      "listWorkspaces",
      "workspaceStages",
    ]);
    api.whoami.and.returnValue(of({ email: "dev@local", personas: ["architect"] }));
    api.listProjects.and.returnValue(of([{ kb_application_id: "uw-credit-risk", name: "UW Credit Risk" }]));
    api.listWorkspaces.and.returnValue(
      of([
        { id: "ws--global", tier: "global", progress: { stages: 3, completed: 3 } },
        { id: "ws--architecture", tier: "architecture", progress: { stages: 4, completed: 0 } },
        { id: "epic-01", tier: "mini", epic_id: "epic-01", progress: { stages: 12, completed: 3 } },
      ]),
    );
    api.workspaceStages.and.returnValue(of({ stages: [
      { seq: 1, key: "kb",  name: "Knowledge Base Build", state: "completed" },
      { seq: 2, key: "prd", name: "PRD",                  state: "completed" },
    ] }));

    TestBed.configureTestingModule({
      providers: [WorkspaceStore, { provide: AidlcPlatformMgmtService, useValue: api }],
    });
    store = TestBed.inject(WorkspaceStore);
  });

  it("load() populates principal and projects", () => {
    store.load();
    expect(store.principal()?.email).toBe("dev@local");
    expect(store.projects().length).toBe(1);
  });

  it("selectProject loads workspaces and auto-selects the global one + its stages", () => {
    store.selectProject("uw-credit-risk");
    expect(store.selectedProjectId()).toBe("uw-credit-risk");
    expect(store.globalWorkspace()?.id).toBe("ws--global");
    expect(store.miniWorkspaces().length).toBe(1);
    expect(store.selectedId()).toBe("ws--global");
    expect(store.stages().length).toBe(2);
  });

  it("splits the three tiers: global, architecture (singleton) and mini", () => {
    store.selectProject("uw-credit-risk");
    expect(store.globalWorkspace()?.id).toBe("ws--global");
    expect(store.architectureWorkspace()?.id).toBe("ws--architecture");
    expect(store.miniWorkspaces().map((w) => w.id)).toEqual(["epic-01"]);
  });

  it("select loads that workspace's stages and resets the stage selection", () => {
    store.selectProject("uw-credit-risk");
    store.selectStage("prd");
    store.select("epic-01");
    expect(store.selectedId()).toBe("epic-01");
    expect(store.selectedStageKey()).toBeNull();
  });

  it("clearProject collapses the tree (dashboard view)", () => {
    store.selectProject("uw-credit-risk");
    store.clearProject();
    expect(store.selectedProjectId()).toBeNull();
    expect(store.workspaces().length).toBe(0);
    expect(store.stages().length).toBe(0);
  });
});
