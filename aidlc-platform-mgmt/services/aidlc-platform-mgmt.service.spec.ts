import { HttpClientTestingModule, HttpTestingController } from "@angular/common/http/testing";
import { TestBed } from "@angular/core/testing";

import { RuntimeConfigService } from "../../../services/runtime-config.service";
import { AidlcPlatformMgmtService } from "./aidlc-platform-mgmt.service";

describe("AidlcPlatformMgmtService", () => {
  let service: AidlcPlatformMgmtService;
  let httpMock: HttpTestingController;
  const base = "http://test/agentic_platform/api/v1";

  beforeEach(() => {
    const rcSpy = jasmine.createSpyObj("RuntimeConfigService", ["getApiUrl"]);
    rcSpy.getApiUrl.and.returnValue(base);
    TestBed.configureTestingModule({
      imports: [HttpClientTestingModule],
      providers: [AidlcPlatformMgmtService, { provide: RuntimeConfigService, useValue: rcSpy }],
    });
    service = TestBed.inject(AidlcPlatformMgmtService);
    httpMock = TestBed.inject(HttpTestingController);
  });

  afterEach(() => httpMock.verify());

  it("listProjects GETs /projects", () => {
    service.listProjects().subscribe();
    const req = httpMock.expectOne(`${base}/projects`);
    expect(req.request.method).toBe("GET");
    req.flush([]);
  });

  it("workspaceStages GETs the stages endpoint", () => {
    service.workspaceStages("ws-1").subscribe();
    const req = httpMock.expectOne(`${base}/workspaces/ws-1/stages`);
    expect(req.request.method).toBe("GET");
    req.flush({ stages: [] });
  });

  it("startStage POSTs stage + pipeline + workspace to /pipeline-runs", () => {
    service.startStage("prd", "uw-cr-global", "ws-1", true).subscribe();
    const req = httpMock.expectOne(`${base}/pipeline-runs`);
    expect(req.request.method).toBe("POST");
    expect(req.request.body).toEqual({ stage: "prd", pipeline: "uw-cr-global", workspace_id: "ws-1", force: true });
    req.flush({});
  });

  it("approve POSTs the decision to the run approvals endpoint", () => {
    service.approve("run-1", "me@aig.com", "product-owner", "approve", "ok").subscribe();
    const req = httpMock.expectOne(`${base}/pipeline-runs/run-1/approvals`);
    expect(req.request.method).toBe("POST");
    expect(req.request.body).toEqual({
      approver: "me@aig.com",
      approver_persona: "product-owner",
      decision: "approve",
      comment: "ok",
    });
    req.flush({});
  });

  it("ensureArchitecture POSTs to /workspaces/architecture", () => {
    service.ensureArchitecture("uw-credit-risk").subscribe();
    const req = httpMock.expectOne(`${base}/workspaces/architecture`);
    expect(req.request.method).toBe("POST");
    expect(req.request.body).toEqual({ kb_application_id: "uw-credit-risk" });
    req.flush({ id: "uw-credit-risk--architecture", tier: "architecture" });
  });

  it("chat POSTs the question to the project chat endpoint", () => {
    service.chat("uw-credit-risk", "hi").subscribe();
    const req = httpMock.expectOne(`${base}/projects/uw-credit-risk/chat`);
    expect(req.request.method).toBe("POST");
    expect(req.request.body).toEqual({ question: "hi" });
    req.flush({});
  });

  it("builds artifact download / export URLs", () => {
    expect(service.downloadArtifactUrl("a1")).toBe(`${base}/artifacts/a1/download`);
    expect(service.exportDocxUrl("a1")).toBe(`${base}/artifacts/a1/export.docx`);
    expect(service.exportHtmlUrl("a1")).toBe(`${base}/artifacts/a1/export.html`);
  });

  it("artifactContent GETs the content endpoint", () => {
    service.artifactContent("a1").subscribe();
    const req = httpMock.expectOne(`${base}/artifacts/a1/content`);
    expect(req.request.method).toBe("GET");
    req.flush({ artifact_id: "a1", files: [] });
  });

  it("reviseArtifact PUTs the edited content to /artifacts/{id}/content", () => {
    service.reviseArtifact("a1", "# edited", "tightened").subscribe();
    const req = httpMock.expectOne(`${base}/artifacts/a1/content`);
    expect(req.request.method).toBe("PUT");
    expect(req.request.body).toEqual({ content: "# edited", comment: "tightened" });
    req.flush({ artifact: { id: "a2" } });
  });

  it("uploadRevision POSTs multipart form to /artifacts/{id}/revision", () => {
    const file = new File(["x"], "prd.docx");
    service.uploadRevision("a1", file, "from word").subscribe();
    const req = httpMock.expectOne(`${base}/artifacts/a1/revision`);
    expect(req.request.method).toBe("POST");
    expect(req.request.body instanceof FormData).toBeTrue();
    expect((req.request.body as FormData).get("comment")).toBe("from word");
    req.flush({ artifact: { id: "a3" } });
  });

  it("degrades to a fallback when no HttpClient is available", (done) => {
    const rcSpy = jasmine.createSpyObj("RuntimeConfigService", ["getApiUrl"]);
    rcSpy.getApiUrl.and.returnValue(base);
    const offline = new AidlcPlatformMgmtService(null, rcSpy);
    offline.listProjects().subscribe((r) => {
      expect(r).toEqual([]);
      done();
    });
  });
});
