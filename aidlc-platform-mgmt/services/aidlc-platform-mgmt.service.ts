import { HttpClient, HttpHeaders, HttpParams } from "@angular/common/http";
import { Injectable, Optional } from "@angular/core";
import { Observable, of } from "rxjs";
import { catchError, map } from "rxjs/operators";

import { RuntimeConfigService } from "../../../services/runtime-config.service";

/**
 * AIDLC Platform Management — data service.
 *
 * Single client for the agentic-platform (forward-engineering) pipeline API vendored on the agents
 * service under `/agentic_platform/api/v1`. Base URL comes from runtime config
 * (`apiBaseUrls.agenticPlatformBaseUrl`), never hardcoded. Reads degrade to an empty result on error
 * or when no backend is configured, so the console still renders (house pattern).
 */

export interface Principal {
  subject?: string;
  email?: string;
  personas?: string[];
  can_see_code?: boolean;
  auth_mode?: string;
}

export interface Progress {
  stages: number;
  completed: number;
  waiting_for_approval?: number;
  gaps?: number;
}

export interface Project {
  kb_application_id: string;
  name: string;
  onboarded?: boolean;
  stage?: string;
  business_domain?: string | null;
  global_progress?: Progress;
  mini_workspace_count?: number;
  open_mini_count?: number;
  created_at?: string | null;
  gear_id?: string | null;
}

export interface Workspace {
  id: string;
  tier?: "global" | "architecture" | "mini";
  kb_application_id?: string;
  pipeline?: string;
  epic_id?: string | null;
  epic_title?: string | null;
  state?: string;
  label?: string;
  progress?: Progress;
}

export interface Stage {
  tier?: string;
  seq: number;
  key: string;
  name: string;
  deliverable?: string;
  state: string;
  ready?: boolean;
  capability_status?: string;
  approval_persona?: string;
  artifact_tier?: string | null;
  permission_policy?: string;
  allowed_tools?: string[];
  run_id?: string | null;
  reason?: string | null;
  unmet_prerequisites?: string[];
  agent_group?: string | null;
}

export interface ArtifactVersion {
  version: number;
  created_at?: string | null;
  created_by?: string | null;
  status?: string;
  checksum?: string | null;
  s3_uri?: string | null;
}

export interface Artifact {
  id: string;
  artifact_type?: string;
  stage_key?: string;
  version?: number | string;
  current_version?: number;
  versions?: ArtifactVersion[];
  status?: string;
  produced_by_persona?: string | null;
  storage_kind?: string | null;
  git_repo?: string | null;
  git_commit?: string | null;
  pr_url?: string | null;
  created_by?: string | null;
  approval_feedback?: string | null;
  parent_id?: string | null;
  is_dry_run?: boolean;
}

export interface ArtifactContent {
  artifact_id: string;
  artifact_type?: string;
  version?: number | string;
  files: { filename: string; content: string }[];
}

export interface Run {
  run_id: string;
  stage_key?: string;
  workspace_id?: string | null;  // which workspace this run was executed for
  state?: string;
  queued_at?: string | null;
  started_at?: string | null;
  finished_at?: string | null;   // end time — backend field name
  created_at?: string | null;   // fallback if started_at absent
  num_turns?: number | null;
  max_turns?: number | null;
  cost_usd?: number | null;
  output_tokens?: number | null;
  log?: string[] | null;
}

export interface StagesResponse {
  pipeline?: string;
  tier?: string;
  workspace?: { id: string; label?: string; gear_id?: string | null };
  summary?: Record<string, number>;
  stages: Stage[];
}

export interface Pipeline {
  name: string;
  title?: string;
  tier?: string;
}

export interface OnboardRequest {
  kb_application: string;
  category: "greenfield" | "brownfield";
  intake_source: "requirements" | "reverse-engineering" | "git-repository" | "existing-kb" | "re-graph";
  source_language?: string | null;
  target_framework: string;
  gear_id?: string;
}

export interface LibraryCorpusModule {
  module_key: string;
  module?: string;
  app?: string;
  stored_at?: string | null;
}

export interface LibraryStatus {
  corpus: {
    modules: LibraryCorpusModule[];
    screens: { catalog_version?: number; pages?: number; stored_at?: string | null }[];
  };
  dirty: boolean;
  latest_build?: { run_id: string; state: string; built_at?: string | null } | null;
  approved?: { artifact_id: string; version: number; approved_at?: string | null } | null;
}

export interface LibraryDocumentResult {
  module_key?: string;
  format?: string;
  parsed_by?: string;
  pages?: number;
  cards_total?: number;
  cards?: Record<string, number>;
  edges?: number;
  warnings?: string[];
  stored?: boolean;
  catalog_version?: number;
  images_bound?: number;
  images_unbound?: number;
  report?: string[];
  fallback?: { mapping?: unknown; notes?: string } | null;
}

// ── Library dependency graph (fe_kb_* tables, one version per Gear ID) ──────

/** One ASP page of a Gear ID's dependency graph (a Component node in fe_kb_nodes). */
export interface LibraryGraphNode {
  id: string;
  file: string;
  module: string;
  path?: string;
  layer?: string | null;
  risk?: string | null;
  loc: number;
  complexity?: number;
  cyclomatic?: number;
  domain?: string | null;
  rules?: number;
  security?: Record<string, number>;
  depth?: number;
  parent_id?: string | null;
  via_edge_id?: number | null;
}

export interface LibraryGraphEdge {
  id: number;
  source: string;
  target: string;
  /** DEPENDS_ON = include / redirect / link; TRIGGERS = form post / navigation. */
  label: string;
  tag?: string | null;
  confidence?: number | null;
  evidence?: string;
  ambiguous?: boolean;
}

/** One version of a Gear ID's dependency graph (a fe_kb_versions row of the `_library-<gear>-deps-v<N>` series). */
export interface LibraryGraphVersion {
  kb_version: string;
  version: number | null;
  gear_id?: string;
  status: "STAGING" | "ACTIVE" | "SUPERSEDED" | string;
  is_current: boolean;
  built_at?: string | null;
  pages?: number;
  edges?: number;
  modules?: number;
  source?: string;
}

export interface LibraryVersions {
  gear_id: string;
  cards: { current: string | null; items: LibraryGraphVersion[]; note?: string };
  dependency_graph: { current: string | null; items: LibraryGraphVersion[] };
}

export interface LibraryGraph {
  gear_id: string;
  kb_version: string;
  version?: number | null;
  is_current?: boolean;
  current?: string | null;
  status?: string;
  built_at?: string | null;
  stats?: Record<string, unknown>;
  modules: { module: string; files: number; loc: number }[];
  pairs: { source: string; target: string; count: number }[];
  nodes: LibraryGraphNode[];
  edges: LibraryGraphEdge[];
}

export interface LibraryGraphPair {
  source_module: string;
  target_module: string;
  count: number;
  edges: { id: number; label: string; evidence: string; confidence?: number | null;
           source: { id: string; file: string }; target: { id: string; file: string } }[];
}

/** A Global Library card as shown against an ASP page (BR-UWCR-NNN, SCR-UWCR-NNN, ...). */
export interface LibraryPageCard {
  id: string;
  kind: string;
  label: string;
  summary?: string;
  confidence?: number | null;
}

/** The Global Library's cards for one ASP page, from the approved build, the latest build, or the uploaded module JSONs. */
export interface LibraryPageCards {
  source: "approved" | "latest" | "corpus" | string;
  version?: number | string | null;
  found: boolean;
  page?: string;
  module_key?: string;
  module?: string;
  submodule?: string | null;
  domain?: string | null;
  layer?: string | null;
  cards: Record<string, LibraryPageCard[]>;
  counts: Record<string, number>;
  shared_entities: LibraryPageCard[];
  screenshots?: number;
}

export interface LibraryGraphNodeDetail {
  node: LibraryGraphNode & { purpose: string; tables: string[]; includes: string[]; submodule?: string };
  outgoing: (LibraryGraphEdge & { other: { id: string; kind: string; file: string; module: string } })[];
  incoming: (LibraryGraphEdge & { other: { id: string; kind: string; file: string; module: string } })[];
  /** null when the Gear ID has no library inputs yet. */
  library?: LibraryPageCards | null;
}

/** A project's uploaded ASP files placed on the Gear ID's dependency graph (GET /projects/{id}/asp-source/graph). */
export interface ProjectAspGraph {
  kb_application_id: string;
  gear_id: string;
  kb_version: string;
  version?: number | null;
  is_current?: boolean;
  built_at?: string | null;
  files: { name: string; size?: number }[];
  matched?: Record<string, string[]>;
  pages: (LibraryGraphNode & {
    in_project: true; submodule?: string; cards?: Record<string, number>; cards_found?: boolean; module_key?: string;
    library_module?: string | null; library_submodule?: string | null; library_domain?: string | null; shared_entities?: number;
  })[];
  neighbours: (LibraryGraphNode & { in_project: false; submodule?: string })[];
  edges: LibraryGraphEdge[];
  modules: { module: string; files: number; loc: number }[];
  pairs: { source: string; target: string; count: number }[];
  unmatched: string[];
  ignored: string[];
  library: { source: string; version?: number | string | null; available: boolean };
}

export interface LibraryGraphWalk {
  start: string;
  direction: "down" | "up" | "both";
  depth: number;
  nodes: LibraryGraphNode[];
  edges: LibraryGraphEdge[];
}

export interface LibraryGraphPath {
  from: string;
  to: string;
  found: boolean;
  direction: "down" | "up" | "both" | null;
  hops: number;
  nodes: LibraryGraphNode[];
  edges: LibraryGraphEdge[];
}

export interface LibraryGraphUploadResult extends LibraryDocumentResult {
  kb_version?: string;
  gear_id?: string;
  version?: number;
  is_current?: boolean;
  previous_current?: string | null;
  status?: string;
  modules?: number;
  tables?: number;
  by_label?: Record<string, number>;
  ambiguous_edges?: number;
  shared_includes?: number;
  unresolved_page_refs?: number;
}

export interface AspSourceResult {
  matched: string[];
  unmatched: string[];
  ignored: string[];
  rejected: string[];
  received: string[];
  cards_selected?: number;
  library_version?: string | null;
  started?: boolean;
  stored?: boolean;
}

export interface AspSourceStatus {
  files: string[];
  matched?: string[];
  unmatched?: string[];
  pinned_library_version?: string | null;
  library_stale?: boolean;
  ready?: boolean;
}

// ── Traceability ────────────────────────────────────────────────────────────

export interface TraceabilityArtifact {
  artifact_id: string;
  workspace_id: string;
  kind: string;
  s3_uri?: string | null;           // S3 object key — fe_workspace_artifacts has NO status column
  grounding_score?: number | null;
  triggered_by_name?: string | null;
  triggered_by_persona?: string | null;
  reviewed_by_name?: string | null;
  reviewed_at?: string | null;
  created_at?: string | null;
}

export interface TraceabilityRelationship {
  source_artifact_id: string;
  target_artifact_id: string;
  relationship_type: string;
  description?: string | null;
  created_at?: string | null;
}

export interface TraceabilityKbGrounding {
  to_kb_card_id: string;
  stage?: string | null;
  coverage_count: number;
  last_linked_at?: string | null;
}

export interface TraceabilityCoverageCell {
  to_kb_card_id: string;
  stage: string;
  link_count: number;
}

export interface GraphNode {
  id: string;
  label: string;
  kind: string;
  kind_label: string;
  layer: string;
  category: string | null;
  summary: string | null;
  origin: string;
}

export interface GraphEdge {
  id: string | null;
  source: string;
  target: string;
  label: string;
  tag: string | null;
  origin: string;
}

export interface LayerBand {
  key: string;
  title: string;
  order: number;
  kinds: string[];
  shared: boolean;
}

export interface KindStat {
  kind: string;
  kind_label: string;
  count: number;
}

export interface OntologyGraph {
  kb_version: string;
  category: string | null;
  nodes: GraphNode[];
  edges: GraphEdge[];
  layers: LayerBand[];
  kinds: KindStat[];
}

export interface KbStats {
  project_id: string;
  exists: boolean;
  node_count: number;
  edge_count: number;
  chunk_count: number;
  [key: string]: unknown;
}

export interface TraceabilityResponse {
  workspace_id: string;
  artifacts: TraceabilityArtifact[];
  relationships: TraceabilityRelationship[];
  kb_grounding: TraceabilityKbGrounding[];
  coverage_matrix: TraceabilityCoverageCell[];
}

@Injectable({ providedIn: "root" })
export class AidlcPlatformMgmtService {
  /** Full API base incl. the /agentic_platform/api/v1 prefix (from runtime config). */
  private readonly apiBase: string;

  constructor(
    @Optional() private readonly http: HttpClient | null,
    config: RuntimeConfigService,
  ) {
    this.apiBase = config.getApiUrl("agenticPlatformBaseUrl") ?? "";
  }

  /** The API base — used by download links (built as plain hrefs). */
  get base(): string {
    return this.apiBase;
  }

  private get ready(): boolean {
    return !!this.http && !!this.apiBase;
  }

  private authHeaders(): HttpHeaders {
    const token = (typeof localStorage !== "undefined" && localStorage.getItem("access_token")) || "";
    return token ? new HttpHeaders({ Authorization: `Bearer ${token}` }) : new HttpHeaders();
  }

  private get<T>(path: string, fallback: T, params?: HttpParams): Observable<T> {
    if (!this.ready) return of(fallback);
    return this.http!.get<T>(`${this.apiBase}${path}`, { headers: this.authHeaders(), params }).pipe(
      catchError(() => of(fallback)),
    );
  }

  private post<T>(path: string, body: unknown, fallback: T): Observable<T> {
    if (!this.ready) return of(fallback);
    return this.http!.post<T>(`${this.apiBase}${path}`, body, { headers: this.authHeaders() }).pipe(
      catchError(() => of(fallback)),
    );
  }

  private del<T>(path: string, fallback: T): Observable<T> {
    if (!this.ready) return of(fallback);
    return this.http!.delete<T>(`${this.apiBase}${path}`, { headers: this.authHeaders() }).pipe(
      catchError(() => of(fallback)),
    );
  }

  private put<T>(path: string, body: unknown, fallback: T): Observable<T> {
    if (!this.ready) return of(fallback);
    return this.http!.put<T>(`${this.apiBase}${path}`, body, { headers: this.authHeaders() }).pipe(
      catchError(() => of(fallback)),
    );
  }

  // ── Discovery / overview ────────────────────────────────────────────────
  whoami(): Observable<Principal | null> {
    return this.get<Principal | null>("/whoami", null);
  }
  listProjects(lightweight = true): Observable<Project[]> {
    // API returns { count, onboarded, items: [...], degraded? } — unwrap items.
    // Pass lightweight=true on initial load to skip per-project progress queries (N+1 bottleneck).
    const params = lightweight ? new HttpParams().set("lightweight", "true") : new HttpParams();
    return this.get<{ items?: Project[] }>("/projects", {}, params).pipe(map((r) => r.items ?? []));
  }
  listPipelines(): Observable<Pipeline[]> {
    return this.get<{ items?: Pipeline[] }>("/pipelines", {}).pipe(map((r) => r.items ?? []));
  }
  listPlugins(): Observable<unknown[]> {
    return this.get<{ items?: unknown[] }>("/plugins", {}).pipe(map((r) => r.items ?? []));
  }
  /** Onboard a KB application (greenfield/brownfield). Mirrors forward_engineering POST /projects. */
  onboard(req: OnboardRequest): Observable<Project | null> {
    const body: Record<string, unknown> = {
      kb_application: req.kb_application,
      category: req.category,
      intake_source: req.intake_source,
      source_language: req.source_language ?? null,
      target_framework: req.target_framework,
    };
    if (req.gear_id) body['gear_id'] = req.gear_id;
    return this.post<Project | null>("/projects", body, null);
  }

  /** Fetch available module scopes for a given gear_id.
   *  Calls GET /re-graph/modules?gear_id=... on the FE API, which proxies to the
   *  RE Graph service and returns unique category names as module options. */
  listModules(gearId?: string): Observable<{ name: string; nodeCount?: number }[]> {
    let params = new HttpParams();
    if (gearId) params = params.set('gear_id', gearId);
    return this.get<{ name: string; nodeCount?: number }[]>('/re-graph/modules', [], params);
  }

  /** Delete a project and ALL its local state (workspaces, runs, artifacts, approvals,
   *  audit, outbox, RAG chunks). DELETE /projects/{id}. Idempotent from the UI's view. */
  deleteProject(kbApplicationId: string): Observable<{ deleted?: boolean } | null> {
    return this.del<{ deleted?: boolean } | null>(`/projects/${encodeURIComponent(kbApplicationId)}`, null);
  }

  /** Register a source repository against a project (brownfield · git). POST /projects/{id}/repository. */
  attachRepository(app: string, gitUrl: string, branch?: string): Observable<unknown> {
    return this.post(`/projects/${encodeURIComponent(app)}/repository`, { git_url: gitUrl, branch: branch ?? null }, null);
  }

  /** Upload a requirements / RED document for a project. POST /projects/{id}/documents (multipart). */
  uploadDocument(app: string, file: File, documentKind: string): Observable<unknown> {
    if (!this.ready) return of(null);
    const form = new FormData();
    form.append("file", file);
    form.append("document_kind", documentKind);
    return this.http!.post(`${this.apiBase}/projects/${encodeURIComponent(app)}/documents`, form, {
      headers: this.authHeaders(),
    }).pipe(catchError(() => of(null)));
  }

  // ── Workspaces & stages ─────────────────────────────────────────────────
  listWorkspaces(kbApplicationId?: string, includeProgress = false): Observable<Workspace[]> {
    // NOTE: include_progress fans out a per-workspace query set that is slow over a
    // remote RDS link, so the sidebar tree omits it (progress counts still come from
    // /projects). Pass includeProgress=true from views that need per-stage dot colours.
    let params = new HttpParams();
    if (kbApplicationId) params = params.set("kb_application_id", kbApplicationId);
    if (includeProgress) params = params.set("include_progress", "true");
    // API returns { count, global, mini, items } — items has both tiers.
    return this.get<{ items?: Workspace[] }>("/workspaces", {}, params).pipe(map((r) => r.items ?? []));
  }
  workspaceStages(workspaceId: string): Observable<StagesResponse | null> {
    return this.get<StagesResponse | null>(`/workspaces/${encodeURIComponent(workspaceId)}/stages`, null);
  }
  /** Open (or return) the singleton Architecture Workspace. POST /workspaces/architecture. */
  ensureArchitecture(kbApplicationId: string): Observable<Workspace | null> {
    return this.post<Workspace | null>("/workspaces/architecture", { kb_application_id: kbApplicationId }, null);
  }
  /** Open (or return) the Epic Assembler Workspace. POST /workspaces/assembler.
   *  Normally triggered automatically by the backend; call this for recovery or testing. */
  ensureAssembler(kbApplicationId: string): Observable<Workspace | null> {
    return this.post<Workspace | null>("/workspaces/assembler", { kb_application_id: kbApplicationId }, null);
  }
  orchestration(workspaceId: string): Observable<Record<string, unknown> | null> {
    return this.get<Record<string, unknown> | null>(
      `/workspaces/${encodeURIComponent(workspaceId)}/orchestration`,
      null,
    );
  }
  orchestrate(workspaceId: string, initiatedBy: string): Observable<unknown> {
    return this.post(`/workspaces/${encodeURIComponent(workspaceId)}/orchestrate`, { initiated_by: initiatedBy }, null);
  }
  promote(workspaceId: string, comment?: string): Observable<unknown> {
    return this.post(`/workspaces/${encodeURIComponent(workspaceId)}/promote`, { comment: comment ?? null }, null);
  }
  promoteKb(version: string, reviewer?: string): Observable<unknown> {
    return this.post(`/re/kb/${encodeURIComponent(version)}/promote`, { reviewer: reviewer ?? null }, null);
  }

  // ── Pipeline runs ───────────────────────────────────────────────────────
  runs(workspaceId: string): Observable<{ items?: Run[] }> {
    const params = new HttpParams().set("workspace_id", workspaceId);
    return this.get<{ items?: Run[] }>("/pipeline-runs", {}, params);
  }

  /** Fetch runs by pipeline name without workspace filter — catches legacy runs where workspace_id was null.
   *  Pass the pipeline name (e.g. "uw-cr-global") from the workspace object. */
  runsByPipeline(pipeline: string): Observable<{ items?: Run[] }> {
    const params = new HttpParams().set("pipeline", pipeline);
    return this.get<{ items?: Run[] }>("/pipeline-runs", {}, params);
  }

  /** Fetch all recent runs for a KB application — workaround for the broken workspace_id
   *  query filter. The `?kb_application_id=` parameter returns all runs for the app;
   *  callers should filter client-side by workspace_id to scope results. */
  runsByApp(kbAppId: string): Observable<{ items?: Run[] }> {
    const params = new HttpParams().set("kb_application_id", kbAppId).set("limit", "50");
    return this.get<{ items?: Run[] }>("/pipeline-runs", {}, params);
  }

  /** Fetch a single run by its run_id.
   *  Used as a fallback when list_runs misses legacy runs that have workspace_id=null. */
  getRun(runId: string): Observable<{ run?: Run } | null> {
    return this.get<{ run?: Run } | null>(
      `/pipeline-runs/${encodeURIComponent(runId)}`,
      null,
    );
  }

  startStage(
    stage: string,
    pipeline: string | null,
    workspaceId: string,
    force = false,
    epicWorkspaceId?: string,
    moduleScope?: string | null,
    businessArea?: string | null,
    role?: string | null,
  ): Observable<unknown> {
    if (!this.ready) return of(null);
    // Do NOT use the generic post() helper here — catchError would swallow the
    // 409 (tier mismatch / stage blocked) and make the caller think the run
    // succeeded.  Errors must propagate so the pipeline board can show them.
    const body: Record<string, unknown> = { stage, pipeline: pipeline || null, workspace_id: workspaceId, force };
    if (epicWorkspaceId) body['epic_workspace_id'] = epicWorkspaceId;
    if (moduleScope)   body['module_scope']   = moduleScope;
    if (businessArea)  body['business_area']  = businessArea;
    if (role)          body['role']           = role;
    return this.http!.post(
      `${this.apiBase}/pipeline-runs`,
      body,
      { headers: this.authHeaders() },
    );
  }
  approve(
    runId: string,
    approver: string,
    persona: string,
    decision: "approve" | "reject",
    comment?: string,
  ): Observable<unknown> {
    if (!this.ready) return of(null);
    // Do NOT use the generic post() helper — catchError would swallow backend
    // errors (e.g. 409 already decided, 404 run not found) so the caller's
    // error handler would never fire and the failure would go unnoticed.
    return this.http!.post(
      `${this.apiBase}/pipeline-runs/${encodeURIComponent(runId)}/approvals`,
      { approver, approver_persona: persona, decision, comment },
      { headers: this.authHeaders() },
    );
  }

  /** Approve a single artifact directly — fallback when the run-level approval
   *  returns 409 (e.g. run is in `completed` state due to a workspace mismatch). */
  approveArtifact(
    artifactId: string,
    approver: string,
    persona: string,
    decision: "approve" | "reject",
    comment?: string,
  ): Observable<unknown> {
    if (!this.ready) return of(null);
    return this.http!.post(
      `${this.apiBase}/artifacts/${encodeURIComponent(artifactId)}/approvals`,
      { approver, approver_persona: persona, decision, comment },
      { headers: this.authHeaders() },
    );
  }

  /** List all versions of an artifact for version history display. */
  listArtifactVersions(artifactId: string): Observable<{ versions?: ArtifactVersion[] }> {
    return this.get<{ versions?: ArtifactVersion[] }>(
      `/artifacts/${encodeURIComponent(artifactId)}/versions`,
      { versions: [] },
    );
  }

  /** Get a specific version of an artifact. */
  getArtifactVersionContent(artifactId: string, version: number): Observable<ArtifactContent | null> {
    return this.get<ArtifactContent | null>(
      `/artifacts/${encodeURIComponent(artifactId)}/version/${version}`,
      null,
    );
  }

  /** Get latest approved artifact for merge operations. */
  getLatestApprovedArtifact(
    workspaceId: string,
    artifactType: string,
  ): Observable<Artifact | null> {
    let params = new HttpParams()
      .set('workspace_id', workspaceId)
      .set('artifact_type', artifactType)
      .set('status', 'APPROVED')
      .set('latest_only', 'true');
    return this.get<Artifact | null>(
      `/artifacts/latest`,
      null,
      params,
    );
  }

  // ── Artifacts ───────────────────────────────────────────────────────────
  artifacts(workspaceId: string): Observable<{ items?: Artifact[] }> {
    const params = new HttpParams().set("workspace_id", workspaceId);
    return this.get<{ items?: Artifact[] }>("/artifacts", {}, params);
  }
  /** Text content of an artifact's files — used to prefill the inline editor. */
  artifactContent(id: string, version?: number | string): Observable<ArtifactContent | null> {
    const url = version ? `/artifacts/${encodeURIComponent(id)}/content?version=${version}` : `/artifacts/${encodeURIComponent(id)}/content`;
    return this.get<ArtifactContent | null>(url, null);
  }
  /** Save a reviewer's inline Markdown edit as the artifact's next version. PUT /artifacts/{id}/content. */
  reviseArtifact(id: string, content: string, comment?: string): Observable<{ artifact?: Artifact } | null> {
    return this.put<{ artifact?: Artifact } | null>(
      `/artifacts/${encodeURIComponent(id)}/content`,
      { content, comment: comment ?? null },
      null,
    );
  }
  /** Upload an edited file (.md/.txt/.html/.docx/.pdf) as the next version. POST /artifacts/{id}/revision (multipart). */
  uploadRevision(id: string, file: File, comment?: string): Observable<{ artifact?: Artifact } | null> {
    if (!this.ready) return of(null);
    const form = new FormData();
    form.append("file", file);
    if (comment) form.append("comment", comment);
    return this.http!.post<{ artifact?: Artifact }>(
      `${this.apiBase}/artifacts/${encodeURIComponent(id)}/revision`,
      form,
      { headers: this.authHeaders() },
    ).pipe(catchError(() => of(null)));
  }
  downloadArtifactUrl(id: string, version?: number | string): string {
    const base = `${this.apiBase}/artifacts/${encodeURIComponent(id)}/download`;
    return version ? `${base}?version=${version}` : base;
  }
  exportDocxUrl(id: string, version?: number | string): string {
    const base = `${this.apiBase}/artifacts/${encodeURIComponent(id)}/export.docx`;
    return version ? `${base}?version=${version}` : base;
  }
  exportHtmlUrl(id: string, version?: number | string): string {
    const base = `${this.apiBase}/artifacts/${encodeURIComponent(id)}/export.html`;
    return version ? `${base}?version=${version}` : base;
  }
  devExportMdUrl(workspaceId: string): string {
    return `${this.apiBase}/workspaces/${encodeURIComponent(workspaceId)}/dev/export.md`;
  }

  // ── Chat (opened in a separate window) ──────────────────────────────────
  chat(projectId: string, question: string, workspaceId?: string): Observable<Record<string, unknown> | null> {
    const body: Record<string, unknown> = { question };
    if (workspaceId) body["workspace_id"] = workspaceId;
    return this.post<Record<string, unknown> | null>(`/projects/${encodeURIComponent(projectId)}/chat`, body, null);
  }

  // ── Traceability ─────────────────────────────────────────────────────────
  /**
   * Full traceability data for a workspace (Timeline, Artifacts, Relationships,
   * Trace Graph, Coverage Matrix, KB Grounding).
   *
   * Pass `allWorkspaceIds` (all IDs from the workspace store) so the backend
   * queries the global, architecture, developer and tester workspaces in addition
   * to the clicked mini/EPIC workspace.  When omitted the backend falls back to
   * the old convention-based derivation which only works for global workspaces.
   */
  /** Base URL for the RE/graph endpoints — root of the agent host, no path prefix. */
  private get reGraphBase(): string {
    try { return new URL(this.apiBase).origin; } catch { return this.apiBase; }
  }

  ontologyGraph(category?: string): Observable<OntologyGraph> {
    const empty: OntologyGraph = { kb_version: "", category: null, nodes: [], edges: [], layers: [], kinds: [] };
    let params = new HttpParams();
    if (category) params = params.set("category", category);
    return this.http!.get<OntologyGraph>(`${this.reGraphBase}/re/graph/overview`, {
      headers: this.authHeaders(), params,
    }).pipe(catchError(() => of(empty)));
  }

  kbStats(projectId: string): Observable<KbStats> {
    const empty: KbStats = { project_id: projectId, exists: false, node_count: 0, edge_count: 0, chunk_count: 0 };
    return this.get<KbStats>(`/projects/${encodeURIComponent(projectId)}/kb/stats`, empty);
  }

  kbReview(version: string): Observable<{ kb_version: string; status: string; promotable: boolean; blocking_open: number }> {
    const empty = { kb_version: '', status: '', promotable: false, blocking_open: 0 };
    return this.get<typeof empty>(`/re/kb/${encodeURIComponent(version)}/review`, empty);
  }

  kbNeighbors(projectId: string, nodeId: string, depth = 1): Observable<{ nodes: GraphNode[]; edges: GraphEdge[] }> {
    const empty = { nodes: [], edges: [] };
    return this.http!.get<{ nodes: GraphNode[]; edges: GraphEdge[] }>(
      `${this.reGraphBase}/re/graph/${encodeURIComponent(nodeId)}/neighbors`,
      { headers: this.authHeaders(), params: new HttpParams().set("depth", depth) },
    ).pipe(catchError(() => of(empty)));
  }

  // ── Global Library ──────────────────────────────────────────────────────
  /** GET /library?gear_id= — corpus state, dirty flag, latest build, approved artifact. */
  getLibrary(gearId?: string): Observable<LibraryStatus | null> {
    let params = new HttpParams();
    if (gearId) params = params.set("gear_id", gearId);
    return this.get<LibraryStatus | null>("/library", null, params);
  }

  /** POST /library/documents — upload re-cards JSON, screens Word doc, or the RED per-file analysis
   *  JSON (red-analysis: page dependencies written to the KB graph tables), scoped to gear_id. */
  uploadLibraryDocument(file: File, documentKind: "re-cards" | "screens" | "red-analysis", gearId?: string,
                        options: { activate?: boolean } = {}): Observable<LibraryDocumentResult | null> {
    if (!this.ready) return of(null);
    const form = new FormData();
    form.append("file", file);
    form.append("document_kind", documentKind);
    if (gearId) form.append("gear_id", gearId);
    if (options.activate !== undefined) form.append("activate", String(options.activate));
    return this.http!.post<LibraryDocumentResult>(`${this.apiBase}/library/documents`, form, {
      headers: this.authHeaders(),
    }).pipe(catchError(() => of(null)));
  }

  /** POST /library/build — trigger a library build scoped to gear_id (202 → run starts). */
  buildLibrary(gearId?: string): Observable<{ run?: unknown; events?: unknown; approve?: unknown } | null> {
    const body: Record<string, unknown> = {};
    if (gearId) body['gear_id'] = gearId;
    return this.post<{ run?: unknown; events?: unknown; approve?: unknown } | null>("/library/build", body, null);
  }

  /** GET /library/pages?gear_id=&source=approved|latest&q=&module_key= */
  getLibraryPages(gearId?: string, source: "approved" | "latest" = "approved", query?: string, moduleKey?: string): Observable<{ pages?: unknown[] } | null> {
    let params = new HttpParams().set("source", source);
    if (gearId) params = params.set("gear_id", gearId);
    if (query) params = params.set("q", query);
    if (moduleKey) params = params.set("module_key", moduleKey);
    return this.get<{ pages?: unknown[] } | null>("/library/pages", null, params);
  }

  // ── Library dependency graph (read from fe_kb_* for the Gear ID) ────────
  /** gear_id plus, when given, the version to read (a number, "v2" or a full kb_version); omitted = current. */
  private gearParams(gearId: string, version?: string | number | null): HttpParams {
    let params = gearId ? new HttpParams().set("gear_id", gearId) : new HttpParams();
    if (version !== undefined && version !== null && version !== "") params = params.set("version", String(version));
    return params;
  }

  /** GET /library/versions?gear_id= — the card-build versions and the dependency-graph versions. */
  libraryVersions(gearId: string): Observable<LibraryVersions | null> {
    return this.get<LibraryVersions | null>("/library/versions", null, this.gearParams(gearId));
  }

  /** GET /library/graph/gears — Gear IDs that have a dependency graph in the KB tables. */
  libraryGraphGears(): Observable<{ items: { gear_id: string; versions: number; current: string | null; latest: string | null }[] } | null> {
    return this.get<{ items: { gear_id: string; versions: number; current: string | null; latest: string | null }[] } | null>("/library/graph/gears", null);
  }

  /** GET /library/graph/versions?gear_id= — dependency-graph versions, oldest first, current flagged. */
  libraryGraphVersions(gearId: string): Observable<{ current: string | null; latest: string | null; items: LibraryGraphVersion[] } | null> {
    return this.get<{ current: string | null; latest: string | null; items: LibraryGraphVersion[] } | null>(
      "/library/graph/versions", null, this.gearParams(gearId));
  }

  /** POST /library/graph/versions/{v}/activate — make that version current (also rollback). */
  activateLibraryGraphVersion(gearId: string, version: string | number): Observable<{ current: string; version: number | null; previous_current: string | null; changed: boolean } | null> {
    if (!this.ready) return of(null);
    const q = gearId ? `?gear_id=${encodeURIComponent(gearId)}` : "";
    return this.http!.post<{ current: string; version: number | null; previous_current: string | null; changed: boolean }>(
      `${this.apiBase}/library/graph/versions/${encodeURIComponent(String(version))}/activate${q}`, {}, { headers: this.authHeaders() },
    ).pipe(catchError(() => of(null)));
  }

  /** GET /library/graph?gear_id=[&version=] — pages, page-to-page edges and the module matrix. null = not built yet. */
  libraryGraph(gearId: string, version?: string | number | null): Observable<LibraryGraph | null> {
    return this.get<LibraryGraph | null>("/library/graph", null, this.gearParams(gearId, version));
  }

  /** GET /library/graph/modules/{a}/{b} — the page edges behind one module pair. */
  libraryGraphPair(gearId: string, a: string, b: string, version?: string | number | null): Observable<LibraryGraphPair | null> {
    return this.get<LibraryGraphPair | null>(
      `/library/graph/modules/${encodeURIComponent(a)}/${encodeURIComponent(b)}`, null, this.gearParams(gearId, version));
  }

  /** GET /library/graph/nodes/{id} — one page with purpose, tables, includes and its in/out edges. */
  libraryGraphNode(gearId: string, nodeId: string, version?: string | number | null): Observable<LibraryGraphNodeDetail | null> {
    return this.get<LibraryGraphNodeDetail | null>(
      `/library/graph/nodes/${encodeURIComponent(nodeId)}`, null, this.gearParams(gearId, version));
  }

  /** GET /library/graph/traverse — hop-by-hop walk over the edge table. */
  libraryGraphTraverse(gearId: string, start: string, direction: "down" | "up" | "both", depth: number,
                       exclude: string[] = [], version?: string | number | null): Observable<LibraryGraphWalk | null> {
    let params = this.gearParams(gearId, version).set("start", start).set("direction", direction).set("depth", depth);
    if (exclude.length) params = params.set("exclude", exclude.join(","));
    return this.get<LibraryGraphWalk | null>("/library/graph/traverse", null, params);
  }

  /** GET /library/graph/path — shortest reference chain between two pages. */
  libraryGraphPath(gearId: string, from: string, to: string, exclude: string[] = [], version?: string | number | null): Observable<LibraryGraphPath | null> {
    let params = this.gearParams(gearId, version).set("from", from).set("to", to);
    if (exclude.length) params = params.set("exclude", exclude.join(","));
    return this.get<LibraryGraphPath | null>("/library/graph/path", null, params);
  }

  /** GET /projects/{id}/asp-source/graph — the project's uploaded ASP files on the Gear ID's dependency
   *  graph, with their direct neighbours and the library cards of each page. */
  projectAspGraph(projectId: string, gearId?: string | null, version?: string | number | null): Observable<ProjectAspGraph | null> {
    return this.get<ProjectAspGraph | null>(
      `/projects/${encodeURIComponent(projectId)}/asp-source/graph`, null, this.gearParams(gearId ?? "", version));
  }

  // ── Project ASP source ───────────────────────────────────────────────────
  /** POST /projects/{id}/asp-source — upload ASP files; start=true triggers G1. */
  uploadAspSource(app: string, files: File[], start = true): Observable<AspSourceResult | null> {
    if (!this.ready) return of(null);
    const form = new FormData();
    for (const f of files) form.append("files", f);
    form.append("start", String(start));
    return this.http!.post<AspSourceResult>(
      `${this.apiBase}/projects/${encodeURIComponent(app)}/asp-source`, form,
      { headers: this.authHeaders() },
    ).pipe(catchError(() => of(null)));
  }

  /** GET /projects/{id}/asp-source — current ASP upload status and library match. */
  getAspSource(app: string): Observable<AspSourceStatus | null> {
    return this.get<AspSourceStatus | null>(`/projects/${encodeURIComponent(app)}/asp-source`, null);
  }

  traceability(workspaceId: string, allWorkspaceIds?: string[]): Observable<TraceabilityResponse> {
    const empty: TraceabilityResponse = {
      workspace_id: workspaceId,
      artifacts: [],
      relationships: [],
      kb_grounding: [],
      coverage_matrix: [],
    };
    let params = new HttpParams();
    if (allWorkspaceIds?.length) {
      params = params.set("workspace_ids", allWorkspaceIds.join(","));
    }
    return this.get<TraceabilityResponse>(
      `/workspaces/${encodeURIComponent(workspaceId)}/traceability`,
      empty,
      params,
    );
  }
}
