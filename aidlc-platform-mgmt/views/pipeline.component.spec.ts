import { TestBed } from "@angular/core/testing";
import { of } from "rxjs";

import { AidlcPlatformMgmtService, Artifact, Stage } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";
import { PipelineComponent } from "./pipeline.component";

function stage(partial: Partial<Stage>): Stage {
  return { seq: 1, key: "k", name: "K", state: "pending", ...partial };
}

describe("PipelineComponent", () => {
  let cmp: PipelineComponent;

  beforeEach(() => {
    const api = jasmine.createSpyObj("AidlcPlatformMgmtService", [
      "workspaceStages",
      "runs",
      "artifacts",
      "artifactContent",
      "reviseArtifact",
      "uploadRevision",
      "base",
    ]);
    api.workspaceStages.and.returnValue(of(null));
    api.runs.and.returnValue(of({ items: [] }));
    api.artifacts.and.returnValue(of({ items: [] }));
    api.artifactContent.and.returnValue(of({ artifact_id: "a", files: [{ filename: "prd.md", content: "# PRD" }] }));
    api.reviseArtifact.and.returnValue(of({ artifact: { id: "a2" } }));
    api.uploadRevision.and.returnValue(of({ artifact: { id: "a3" } }));

    const store = jasmine.createSpyObj("WorkspaceStore", ["selectStage", "select", "load"]);
    store.selectedId = () => null;
    store.stages = () => [];
    store.selectedProject = () => null;
    store.selectedStageKey = () => null;

    TestBed.configureTestingModule({
      providers: [
        PipelineComponent,
        { provide: AidlcPlatformMgmtService, useValue: api },
        { provide: WorkspaceStore, useValue: store },
      ],
    });
    cmp = TestBed.inject(PipelineComponent);
  });

  it("classifies a completed stage as done/good/Completed", () => {
    const s = stage({ state: "completed" });
    expect(cmp.isDone(s)).toBeTrue();
    expect(cmp.dot(s)).toBe("✔");
    expect(cmp.tone(s)).toBe("good");
    expect(cmp.label(s)).toBe("Completed");
  });

  it("classifies a waiting stage as warn/Awaiting", () => {
    const s = stage({ state: "waiting_for_approval" });
    expect(cmp.dot(s)).toBe("⚑");
    expect(cmp.tone(s)).toBe("warn");
    expect(cmp.label(s)).toBe("Awaiting");
  });

  it("classifies a ready stage as Ready", () => {
    const s = stage({ state: "pending", ready: true });
    expect(cmp.dot(s)).toBe("▶");
    expect(cmp.label(s)).toBe("Ready");
  });

  it("artifactsFor filters by stage key", () => {
    const arts: Artifact[] = [
      { id: "a", stage_key: "prd" },
      { id: "b", stage_key: "frd" },
    ];
    cmp.artifacts.set(arts);
    expect(cmp.artifactsFor(stage({ key: "prd" })).map((a) => a.id)).toEqual(["a"]);
  });

  it("classifies the architecture tier with its own icon and CLI run-mode", () => {
    cmp.data.set({ tier: "architecture", stages: [] });
    expect(cmp.isArchitecture()).toBeTrue();
    expect(cmp.isMini()).toBeFalse();
    expect(cmp.tierIcon()).toBe("△");
    expect(cmp.runMode()).toBe("Claude Code CLI");
  });

  it("shows LangGraph/Bedrock run-mode for the global tier", () => {
    cmp.data.set({ tier: "global", stages: [] });
    expect(cmp.runMode()).toBe("LangGraph · Bedrock");
    expect(cmp.tierIcon()).toBe("◧");
  });

  it("allows revision only while a stage awaits approval", () => {
    expect(cmp.canRevise(stage({ state: "waiting_for_approval" }))).toBeTrue();
    expect(cmp.canRevise(stage({ state: "completed" }))).toBeFalse();
  });

  it("startEdit loads the primary .md content into the editor", () => {
    cmp.startEdit({ id: "a", artifact_type: "prd" });
    expect(cmp.editingId).toBe("a");
    expect(cmp.editContent).toBe("# PRD");
  });

  it("saveEdit calls reviseArtifact then clears the editor", () => {
    const api = TestBed.inject(AidlcPlatformMgmtService) as jasmine.SpyObj<AidlcPlatformMgmtService>;
    cmp.editingId = "a";
    cmp.editContent = "# edited";
    cmp.editComment = "note";
    cmp.saveEdit({ id: "a", artifact_type: "prd" });
    expect(api.reviseArtifact).toHaveBeenCalledWith("a", "# edited", "note");
    expect(cmp.editingId).toBeNull();
  });

  it("saveEdit is a no-op when the editor is empty", () => {
    const api = TestBed.inject(AidlcPlatformMgmtService) as jasmine.SpyObj<AidlcPlatformMgmtService>;
    cmp.editContent = "   ";
    cmp.saveEdit({ id: "a" });
    expect(api.reviseArtifact).not.toHaveBeenCalled();
  });

  it("pickRevision uploads the chosen file", () => {
    const api = TestBed.inject(AidlcPlatformMgmtService) as jasmine.SpyObj<AidlcPlatformMgmtService>;
    const file = new File(["x"], "prd.docx");
    const event = { target: { files: [file] } } as unknown as Event;
    cmp.pickRevision({ id: "a" }, event);
    expect(api.uploadRevision).toHaveBeenCalled();
  });
});
