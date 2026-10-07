import { ComponentFixture, TestBed, fakeAsync, tick } from "@angular/core/testing";
import { CommonModule } from "@angular/common";
import { FormsModule } from "@angular/forms";
import { RouterTestingModule } from "@angular/router/testing";
import { BrowserModule, DomSanitizer } from "@angular/platform-browser";
import { of, throwError } from "rxjs";
import { signal } from "@angular/core";

import { KnowledgeOntologyComponent } from "./knowledge-ontology.component";
import { AidlcPlatformMgmtService, Artifact, OntologyGraph } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

const STUB_GRAPH: OntologyGraph = {
  nodes: [
    { id: "n1", label: "Credit Risk Score", kind: "ENT", kind_label: "Entity", layer: "domain", category: null, summary: "Core scoring entity", origin: "forward" },
    { id: "n2", label: "Authentication", kind: "SYS", kind_label: "System", layer: "tech", category: null, summary: "Auth capability", origin: "forward" },
  ],
  edges: [{ id: null, source: "n1", target: "n2", label: "DEPENDS_ON", tag: null, origin: "forward" }],
  kinds: [
    { kind: "ENT", kind_label: "Entity", count: 1 },
    { kind: "SYS", kind_label: "System", count: 1 },
  ],
  layers: [],
  kb_version: "v1.2",
  category: null,
};

const STUB_ARTIFACT: Artifact = {
  id: "art-1",
  stage_key: "prd",
  artifact_type: "Product Requirement Document",
  status: "APPROVED",
  version: 1,
  produced_by_persona: "Business Analyst Agent",
};

describe("KnowledgeOntologyComponent", () => {
  let component: KnowledgeOntologyComponent;
  let fixture: ComponentFixture<KnowledgeOntologyComponent>;
  let svcSpy: jasmine.SpyObj<AidlcPlatformMgmtService>;
  let storeSpy: jasmine.SpyObj<WorkspaceStore>;

  beforeEach(async () => {
    svcSpy = jasmine.createSpyObj("AidlcPlatformMgmtService", [
      "ontologyGraph", "artifacts", "artifactContent", "kbNeighbors", "chat",
    ]);
    svcSpy.ontologyGraph.and.returnValue(of(STUB_GRAPH));
    svcSpy.artifacts.and.returnValue(of({ items: [STUB_ARTIFACT] } as any));
    svcSpy.artifactContent.and.returnValue(of({ artifact_id: "art-1", files: [{ filename: "prd.md", content: "# PRD\nKey requirements..." }] }));
    svcSpy.kbNeighbors.and.returnValue(of({ nodes: [STUB_GRAPH.nodes[1]], edges: STUB_GRAPH.edges }));
    svcSpy.chat.and.returnValue(of({ answer: "The authentication requirement is OAuth 2.0. [1]" }));

    // Build a minimal WorkspaceStore spy that returns signals
    storeSpy = jasmine.createSpyObj("WorkspaceStore", [], {
      selectedId:      signal<string | null>("ws-1"),
      selectedProject: signal<any>({ kb_application_id: "uw-cr", name: "uw-credit-risk-enhancement-v1" }),
      stages:          signal<any[]>([{ key: "prd", name: "Product Requirement Document", state: "approved", ready: true, agent_group: "BA" }]),
      projects:        signal<any[]>([]),
      loading:         signal(false),
    });

    await TestBed.configureTestingModule({
      declarations: [KnowledgeOntologyComponent],
      imports: [CommonModule, FormsModule, RouterTestingModule, BrowserModule],
      providers: [
        { provide: AidlcPlatformMgmtService, useValue: svcSpy },
        { provide: WorkspaceStore, useValue: storeSpy },
      ],
    }).compileComponents();

    fixture = TestBed.createComponent(KnowledgeOntologyComponent);
    component = fixture.componentInstance;
    fixture.detectChanges();
  });

  // ── 1. Component creation ──────────────────────────────────────────────
  it("should create", () => {
    expect(component).toBeTruthy();
  });

  // ── 2. Default view is Documents ───────────────────────────────────────
  it("defaults to Documents view", () => {
    expect(component.activeView()).toBe("documents");
  });

  // ── 3. Switching to Ontology view ──────────────────────────────────────
  it("switches to Ontology view and loads graph", () => {
    component.switchView("ontology");
    expect(component.activeView()).toBe("ontology");
  });

  it("does not re-fetch ontology if graph is already cached", () => {
    // Simulate graph already loaded
    component["graph"].set(STUB_GRAPH);
    svcSpy.ontologyGraph.calls.reset();
    component.switchView("ontology");
    // Should not call ontologyGraph again (graph is cached)
    expect(svcSpy.ontologyGraph).not.toHaveBeenCalled();
  });

  // ── 4. Switching back to Documents ────────────────────────────────────
  it("switches back to Documents view", () => {
    component.switchView("ontology");
    component.switchView("documents");
    expect(component.activeView()).toBe("documents");
  });

  // ── 5. Artifact list loads on init ────────────────────────────────────
  it("calls artifacts API with selected workspace id", fakeAsync(() => {
    TestBed.flushEffects();
    tick();
    expect(svcSpy.artifacts).toHaveBeenCalledWith("ws-1");
  }));

  it("artifacts signal is populated when set directly", () => {
    component.artifacts.set([STUB_ARTIFACT]);
    expect(component.artifacts().length).toBe(1);
    expect(component.artifacts()[0].artifact_type).toBe("Product Requirement Document");
  });

  // ── 6. Selecting an artifact loads its content ────────────────────────
  it("loads artifact content when an artifact is selected", () => {
    component.selectArtifact(STUB_ARTIFACT);
    expect(svcSpy.artifactContent).toHaveBeenCalledWith("art-1");
    expect(component.selectedArtifact()?.id).toBe("art-1");
  });

  // ── 7. Chat opens from Documents view ────────────────────────────────
  it("opens chat drawer from Documents view", () => {
    expect(component.chatOpen()).toBeFalse();
    component.chatOpen.set(true);
    expect(component.chatOpen()).toBeTrue();
  });

  // ── 8. Chat opens from Ontology view (same signal) ────────────────────
  it("same chatOpen signal controls chat in both views", () => {
    component.switchView("ontology");
    component.chatOpen.set(true);
    expect(component.chatOpen()).toBeTrue();
    // Switching back — chat is still open
    component.switchView("documents");
    expect(component.chatOpen()).toBeTrue();
  });

  // ── 9. Chat history persists across view switches ─────────────────────
  it("chat history persists when switching views", () => {
    component.chatHistory.set([
      { role: "user", text: "What are the requirements?", ts: Date.now() },
      { role: "kb",   text: "The requirements are...",   ts: Date.now() },
    ]);
    component.switchView("ontology");
    expect(component.chatHistory().length).toBe(2);
    component.switchView("documents");
    expect(component.chatHistory().length).toBe(2);
  });

  // ── 10. Document context label ─────────────────────────────────────────
  it("returns document context label when an artifact is selected in Documents view", () => {
    component.activeView.set("documents");
    component.selectedArtifact.set(STUB_ARTIFACT);
    expect(component.chatContextLabel()).toContain("Product Requirement Document");
  });

  it("returns empty context label when no artifact is selected", () => {
    component.activeView.set("documents");
    component.selectedArtifact.set(null);
    expect(component.chatContextLabel()).toBe("");
  });

  // ── 11. Ontology node context label ──────────────────────────────────
  it("returns node context label when a graph node is selected in Ontology view", () => {
    component.activeView.set("ontology");
    component.selectedNode.set(STUB_GRAPH.nodes[0]);
    expect(component.chatContextLabel()).toContain("Credit Risk Score");
    expect(component.chatContextLabel()).toContain("Entity");
  });

  // ── 12. Chat enriches message with context ────────────────────────────
  it("prepends context to chat message when an artifact is selected", fakeAsync(() => {
    component.activeView.set("documents");
    component.selectedArtifact.set(STUB_ARTIFACT);
    component.question = "Summarize the security requirements";
    component.send();
    tick();

    const enrichedArg = svcSpy.chat.calls.mostRecent().args[1] as string;
    expect(enrichedArg).toContain("[Context:");
    expect(enrichedArg).toContain("Product Requirement Document");
    expect(enrichedArg).toContain("Summarize the security requirements");
  }));

  it("sends question without context prefix when no document or node is selected", fakeAsync(() => {
    component.activeView.set("documents");
    component.selectedArtifact.set(null);
    component.question = "What is the system?";
    component.send();
    tick();

    const enrichedArg = svcSpy.chat.calls.mostRecent().args[1] as string;
    expect(enrichedArg).toBe("What is the system?");
  }));

  // ── 13. Chat response is added to history ─────────────────────────────
  it("adds user message and KB response to chat history", fakeAsync(() => {
    component.question = "What are the key requirements?";
    component.send();
    tick();

    const history = component.chatHistory();
    expect(history.length).toBe(2);
    expect(history[0].role).toBe("user");
    expect(history[0].text).toBe("What are the key requirements?");
    expect(history[1].role).toBe("kb");
    expect(history[1].text).toContain("OAuth 2.0");
  }));

  // ── 14. Chat API failure adds error message ───────────────────────────
  it("adds error message to history on chat API failure", fakeAsync(() => {
    svcSpy.chat.and.returnValue(throwError(() => new Error("Network error")));
    component.question = "Will this fail?";
    component.send();
    tick();

    const history = component.chatHistory();
    expect(history.length).toBe(2);
    expect(history[1].role).toBe("kb");
    expect(history[1].text).toContain("Error");
    expect(component.sending()).toBeFalse();
  }));

  // ── 15. Graph node selection loads neighbors ──────────────────────────
  it("loads neighbors when a graph node is selected", () => {
    component.selectGraphNode(STUB_GRAPH.nodes[0]);
    expect(svcSpy.kbNeighbors).toHaveBeenCalledWith("uw-cr", "n1");
    expect(component.selectedNode()?.id).toBe("n1");
  });

  it("updates neighbor panel with connected nodes", fakeAsync(() => {
    component.selectGraphNode(STUB_GRAPH.nodes[0]);
    tick();
    const nb = component.neighbor();
    expect(nb).not.toBeNull();
    expect(nb!.loading).toBeFalse();
    expect(nb!.nodes.length).toBe(1);
    expect(nb!.nodes[0].label).toBe("Authentication");
  }));

  // ── 16. Empty graph result ────────────────────────────────────────────
  it("handles empty graph gracefully", () => {
    const emptyGraph: OntologyGraph = { nodes: [], edges: [], kinds: [], layers: [], kb_version: "", category: null };
    svcSpy.ontologyGraph.and.returnValue(of(emptyGraph));
    component.loadOntology();
    expect(component.graph()?.nodes.length).toBe(0);
    expect(component.graphError()).toBeNull();
  });

  // ── 17. Ontology API failure sets error ──────────────────────────────
  it("sets graphError on ontology API failure", () => {
    svcSpy.ontologyGraph.and.returnValue(throwError(() => ({ message: "Graph unavailable" })));
    component.loadOntology();
    expect(component.graphError()).toContain("Graph unavailable");
    expect(component.graphLoading()).toBeFalse();
  });

  // ── 18. Document → Ontology navigation ──────────────────────────────
  it("switches to Ontology view when viewOntologyForArtifact is called", () => {
    component.activeView.set("documents");
    component.viewOntologyForArtifact(STUB_ARTIFACT);
    expect(component.activeView()).toBe("ontology");
  });

  // ── 19. Ontology → Document navigation ──────────────────────────────
  it("switches to Documents view when switchToDocForNode is called", () => {
    component.activeView.set("ontology");
    component.switchToDocForNode(STUB_GRAPH.nodes[0]);
    expect(component.activeView()).toBe("documents");
  });

  // ── 20. askAboutDoc pre-fills question and opens chat ─────────────────
  it("askAboutDoc opens chat and pre-fills a question with doc name", () => {
    component.askAboutDoc(STUB_ARTIFACT);
    expect(component.chatOpen()).toBeTrue();
    expect(component.question).toContain("Product Requirement Document");
  });

  // ── 21. askAboutNode pre-fills question with node label ──────────────
  it("askAboutNode opens chat and pre-fills a question with node label", () => {
    component.askAboutNode(STUB_GRAPH.nodes[0]);
    expect(component.chatOpen()).toBeTrue();
    expect(component.question).toContain("Credit Risk Score");
  });

  // ── 22. Kind filter filters nodes ────────────────────────────────────
  it("filteredNodes returns all nodes when no kind filter is active", () => {
    component["graph"].set(STUB_GRAPH);
    component.activeKind.set(null);
    expect(component.filteredNodes().length).toBe(2);
  });

  it("filteredNodes filters by kind", () => {
    component["graph"].set(STUB_GRAPH);
    component.activeKind.set("ENT");
    const result = component.filteredNodes();
    expect(result.length).toBe(1);
    expect(result[0].kind).toBe("ENT");
  });

  // ── 23. Search filters nodes ─────────────────────────────────────────
  it("filteredNodes filters by search term", () => {
    component["graph"].set(STUB_GRAPH);
    component.activeKind.set(null);
    component.searchTerm = "auth";
    const result = component.filteredNodes();
    expect(result.length).toBe(1);
    expect(result[0].label).toBe("Authentication");
  });

  // ── 24. artifactsFor returns correct artifacts for a stage ────────────
  it("artifactsFor returns artifacts matching stage key", () => {
    component.artifacts.set([STUB_ARTIFACT]);
    const stage = { key: "prd", name: "PRD", state: "approved", ready: true, agent_group: "BA" } as any;
    expect(component.artifactsFor(stage).length).toBe(1);
  });

  it("artifactsFor returns empty array for unmatched stage", () => {
    component.artifacts.set([STUB_ARTIFACT]);
    const stage = { key: "frd", name: "FRD", state: "pending", ready: false, agent_group: "BA" } as any;
    expect(component.artifactsFor(stage).length).toBe(0);
  });

  // ── 25. send() is a no-op when question is blank ──────────────────────
  it("send does nothing when question is empty", () => {
    component.question = "   ";
    component.send();
    expect(svcSpy.chat).not.toHaveBeenCalled();
    expect(component.chatHistory().length).toBe(0);
  });

  // ── 26. send() is a no-op while already sending ──────────────────────
  it("send does nothing when already sending", () => {
    component.sending.set(true);
    component.question = "hello";
    component.send();
    expect(svcSpy.chat).not.toHaveBeenCalled();
  });
});
