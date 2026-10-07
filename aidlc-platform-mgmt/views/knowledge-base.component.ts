import { Component, OnDestroy, effect, signal } from "@angular/core";
import { DomSanitizer, SafeHtml } from "@angular/platform-browser";

import { AidlcPlatformMgmtService, Artifact, Stage } from "../services/aidlc-platform-mgmt.service";
import { WorkspaceStore } from "../services/workspace.store";

interface ChatMessage { role: "user" | "kb"; text: string; ts: number; }

/**
 * Knowledge Base view — shows what has been chunked and embedded per pipeline stage,
 * and provides an inline Q&A chat drawer with markdown rendering and HTML export.
 */
@Component({
  selector: "app-apm-knowledge-base",
  template: `
    <ng-container *ngIf="store.selectedId(); else pickWs">
      <div class="apm-crumb">{{ store.selectedProject()?.kb_application_id }} / Knowledge base</div>
      <div class="apm-hdr">
        <div class="apm-hdr-icon">🗄</div>
        <div>
          <h2>Knowledge Base</h2>
          <p class="sub">Indexed content by stage · grounded · cite-or-abstain</p>
        </div>
        <button class="apm-btn" style="margin-left:auto" (click)="chatOpen.set(true)">
          💬 Ask the knowledge base
        </button>
      </div>

      <!-- no stages loaded yet -->
      <div class="apm-card" *ngIf="!stages().length">
        <div class="apm-card-body sub">No pipeline loaded — select a workspace from the sidebar first.</div>
      </div>

      <!-- per-stage KB content cards -->
      <div class="apm-card" *ngFor="let s of stages()">
        <div class="apm-card-head">
          <h4>{{ dot(s) }} {{ s.name }}</h4>
          <span style="display:flex;gap:8px;align-items:center">
            <span class="apm-chip" [ngClass]="tone(s)">{{ label(s) }}</span>
            <span class="sub" *ngIf="artifactsFor(s).length">
              {{ artifactsFor(s).length }}&nbsp;doc{{ artifactsFor(s).length !== 1 ? 's' : '' }}&nbsp;indexed
            </span>
          </span>
        </div>
        <div class="apm-card-body">
          <ng-container *ngIf="artifactsFor(s).length; else noContent">
            <div class="apm-files">
              <div class="apm-file" *ngFor="let a of artifactsFor(s)">
                <div class="apm-file-top">
                  <span class="apm-fi" [class.ok]="a.status === 'APPROVED' || a.status === 'approved'">▤</span>
                  <div>
                    <div class="apm-fn">{{ a.artifact_type }}</div>
                    <div class="apm-fm">
                      <span class="apm-chip" [ngClass]="a.status === 'APPROVED' ? 'ok' : ''">{{ a.status }}</span>
                      v{{ a.version }}
                      <span *ngIf="a.produced_by_persona"> · {{ a.produced_by_persona }}</span>
                    </div>
                  </div>
                </div>
              </div>
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
    </ng-container>

    <ng-template #pickWs>
      <div class="apm-card">
        <div class="apm-card-body sub">Pick a project on the Dashboard to view its knowledge base.</div>
      </div>
    </ng-template>

    <!-- ── inline chat drawer ── -->
    <ng-container *ngIf="chatOpen()">
      <div class="apm-modal-bg" (click)="chatOpen.set(false)"></div>
      <aside class="apm-drawer apm-kb-chat-drawer">

        <!-- drawer header -->
        <div class="apm-kb-chat-header">
          <div class="apm-kb-chat-header-left">
            <div class="apm-kb-chat-logo">🗄</div>
            <div>
              <div class="apm-kb-chat-title">Knowledge Base</div>
              <div class="apm-kb-chat-sub">grounded · cite-or-abstain</div>
            </div>
          </div>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="apm-btn sm outline" *ngIf="chatHistory().length"
                    (click)="downloadAll()" title="Download full chat history as HTML">
              ⬇ Export all
            </button>
            <button class="apm-x" (click)="chatOpen.set(false)">×</button>
          </div>
        </div>

        <!-- scrollable message history -->
        <div class="apm-chat-history">
          <!-- empty state -->
          <div class="apm-chat-empty" *ngIf="!chatHistory().length">
            <div class="apm-chat-empty-icon">💬</div>
            <p>Ask anything grounded in the indexed stage documents.</p>
            <small>The model will cite a source or abstain if the answer isn't in the KB.</small>
          </div>

          <!-- messages -->
          <ng-container *ngFor="let m of chatHistory(); let i = index">

            <!-- user message -->
            <div class="apm-chat-row user" *ngIf="m.role === 'user'">
              <div class="apm-chat-body">
                <div class="apm-chat-meta">You · {{ formatTime(m.ts) }}</div>
                <div class="apm-chat-bubble user">{{ m.text }}</div>
              </div>
              <div class="apm-chat-avatar user">👤</div>
            </div>

            <!-- KB response -->
            <div class="apm-chat-row kb" *ngIf="m.role === 'kb'">
              <div class="apm-chat-avatar kb">🗄</div>
              <div class="apm-chat-body">
                <div class="apm-chat-meta">Knowledge Base · {{ formatTime(m.ts) }}</div>
                <div class="apm-chat-bubble kb" [innerHTML]="render(m.text)"></div>
                <div class="apm-chat-actions">
                  <button class="apm-chat-action-btn" (click)="downloadMessage(m, i)"
                          title="Download this response as HTML">
                    ⬇ Download response
                  </button>
                </div>
              </div>
            </div>
          </ng-container>

          <!-- thinking indicator -->
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

        <!-- fixed bottom input bar -->
        <div class="apm-chat-input-bar">
          <textarea class="apm-ta" rows="2"
            style="flex:1;min-height:auto;resize:none;background:var(--panel-bg)"
            [(ngModel)]="question"
            placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
            (keydown.enter)="onChatKey($event)">
          </textarea>
          <button class="apm-btn green"
            style="align-self:flex-end;white-space:nowrap;gap:6px"
            [disabled]="sending() || !question.trim()"
            (click)="send()">
            {{ sending() ? '…' : '↑ Send' }}
          </button>
        </div>
      </aside>
    </ng-container>
  `,
})
export class KnowledgeBaseComponent implements OnDestroy {
  readonly artifacts   = signal<Artifact[]>([]);
  readonly chatOpen    = signal(false);
  readonly chatHistory = signal<ChatMessage[]>([]);
  readonly sending     = signal(false);
  question = "";

  constructor(
    readonly store: WorkspaceStore,
    readonly svc: AidlcPlatformMgmtService,
    private readonly sanitizer: DomSanitizer,
  ) {
    effect(() => {
      const id = this.store.selectedId();
      if (id) {
        this.svc.artifacts(id).subscribe((r) => this.artifacts.set(r?.["items"] ?? []));
      }
    });
  }

  // ── stage helpers ──────────────────────────────────────────────────────────

  stages(): Stage[] { return this.store.stages(); }

  artifactsFor(s: Stage): Artifact[] {
    return this.artifacts().filter((a) => a.stage_key === s.key);
  }

  dot(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "✔";
    if (s.state === "waiting_for_approval") return "⚑";
    if (s.state === "running" || s.state === "queued") return "◔";
    if (s.state === "failed" || s.state === "cancelled") return "✕";
    if (s.ready) return "▶";
    return "○";
  }

  tone(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "good";
    if (s.state === "waiting_for_approval") return "warn";
    if (s.state === "running" || s.state === "queued") return "run";
    if (s.state === "failed" || s.state === "cancelled") return "err";
    return "idle";
  }

  label(s: Stage): string {
    if (s.state === "completed" || s.state === "approved") return "Indexed";
    if (s.state === "waiting_for_approval") return "Pending approval";
    if (s.state === "running" || s.state === "queued") return "Processing";
    if (s.state === "failed" || s.state === "cancelled") return "Failed";
    if (s.ready) return "Ready";
    return "—";
  }

  // ── chat ───────────────────────────────────────────────────────────────────

  formatTime(ts: number): string {
    return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  onChatKey(event: Event): void {
    if (!(event as KeyboardEvent).shiftKey) {
      event.preventDefault();
      this.send();
    }
  }

  send(): void {
    const q = this.question.trim();
    if (!q || this.sending()) return;
    const pid = this.store.selectedProject()?.kb_application_id ?? "";
    const wsId = this.store.selectedId() ?? undefined;
    this.chatHistory.update((h) => [...h, { role: "user", text: q, ts: Date.now() }]);
    this.question = "";
    this.sending.set(true);
    this.svc.chat(pid, q, wsId).subscribe({
      next: (r) => {
        const text = ((r?.["answer"] || r?.["text"] || "(no response)") as string);
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

  // ── markdown rendering ─────────────────────────────────────────────────────

  /** Render a KB response as sanitized HTML (basic markdown → HTML). */
  render(text: string): SafeHtml {
    return this.sanitizer.bypassSecurityTrustHtml(this.toHtml(text));
  }

  private toHtml(text: string): string {
    const lines = text.split("\n");
    const out: string[] = [];
    let inCode = false;
    let inList = false;

    for (const line of lines) {
      // ── fenced code block ──
      if (line.startsWith("```")) {
        if (inList) { out.push("</ul>"); inList = false; }
        if (inCode) { out.push("</code></pre>"); inCode = false; }
        else        { out.push('<pre class="apm-md-pre"><code>'); inCode = true; }
        continue;
      }
      if (inCode) { out.push(this.esc(line)); continue; }

      // ── headings ──
      if (line.startsWith("### ")) {
        if (inList) { out.push("</ul>"); inList = false; }
        out.push(`<h5 class="apm-md-h">${this.fmt(line.slice(4))}</h5>`);
        continue;
      }
      if (line.startsWith("## ")) {
        if (inList) { out.push("</ul>"); inList = false; }
        out.push(`<h4 class="apm-md-h">${this.fmt(line.slice(3))}</h4>`);
        continue;
      }
      if (line.startsWith("# ")) {
        if (inList) { out.push("</ul>"); inList = false; }
        out.push(`<h3 class="apm-md-h">${this.fmt(line.slice(2))}</h3>`);
        continue;
      }

      // ── unordered list ──
      if (/^[-*] /.test(line)) {
        if (!inList) { out.push('<ul class="apm-md-list">'); inList = true; }
        out.push(`<li>${this.fmt(line.slice(2))}</li>`);
        continue;
      }

      // ── close list on non-list line ──
      if (inList) { out.push("</ul>"); inList = false; }

      // ── blank line ──
      if (!line.trim()) { out.push('<div class="apm-md-gap"></div>'); continue; }

      // ── paragraph ──
      out.push(`<p class="apm-md-p">${this.fmt(line)}</p>`);
    }

    if (inList) out.push("</ul>");
    if (inCode)  out.push("</code></pre>");
    return out.join("");
  }

  /** Escape HTML special characters. */
  private esc(s: string): string {
    return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  /** Apply inline markdown (bold, italic, code, citations). */
  private fmt(s: string): string {
    s = this.esc(s);
    // inline code
    s = s.replace(/`([^`]+)`/g, '<code class="apm-md-code">$1</code>');
    // bold
    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    // italic (not bold)
    s = s.replace(/\*([^*\n]+)\*/g, "<em>$1</em>");
    // citations [1] [2] …
    s = s.replace(/\[(\d+)\]/g, '<sup class="apm-md-cite">$1</sup>');
    return s;
  }

  // ── downloads ──────────────────────────────────────────────────────────────

  /** Download the full chat history as a styled HTML file. */
  downloadAll(): void {
    const pid = this.store.selectedProject()?.kb_application_id ?? "project";
    const html = this.buildHtmlDoc(
      this.chatHistory(),
      `${pid} — Knowledge Base Chat`,
      `${this.chatHistory().length} message(s) · Exported ${new Date().toLocaleString()}`,
    );
    this.triggerDownload(html, `kb-chat-${pid}.html`);
  }

  /** Download a single KB response (plus the preceding user question) as HTML. */
  downloadMessage(m: ChatMessage, index: number): void {
    const history = this.chatHistory();
    const prev = index > 0 && history[index - 1]?.role === "user"
      ? [history[index - 1], m]
      : [m];
    const pid = this.store.selectedProject()?.kb_application_id ?? "project";
    const html = this.buildHtmlDoc(
      prev,
      `${pid} — KB Response`,
      `Exported ${new Date().toLocaleString()}`,
    );
    this.triggerDownload(html, `kb-response-${index + 1}.html`);
  }

  private buildHtmlDoc(messages: ChatMessage[], title: string, subtitle: string): string {
    const rows = messages.map((m) => {
      const time = this.formatTime(m.ts);
      if (m.role === "user") {
        return `
          <div class="msg user">
            <div class="msg-body">
              <div class="meta">You · ${time}</div>
              <div class="bubble user">${this.esc(m.text)}</div>
            </div>
            <div class="avatar user">👤</div>
          </div>`;
      }
      return `
        <div class="msg kb">
          <div class="avatar kb">🗄</div>
          <div class="msg-body">
            <div class="meta">Knowledge Base · ${time}</div>
            <div class="bubble kb">${this.toHtml(m.text)}</div>
          </div>
        </div>`;
    }).join("\n");

    return `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>${this.esc(title)}</title>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Inter', 'Segoe UI', Arial, sans-serif; background: #0b1220; color: #e2e8f0;
         min-height: 100vh; padding: 40px 20px; }
  .container { max-width: 820px; margin: 0 auto; }
  header { display: flex; align-items: center; gap: 16px; padding-bottom: 24px;
           border-bottom: 1px solid #1e3a5f; margin-bottom: 36px; }
  .logo { width: 48px; height: 48px; border-radius: 14px; display: flex; align-items: center;
          justify-content: center; font-size: 22px;
          background: linear-gradient(135deg, #003865, #0077c8); }
  .hdr-text h1 { font-size: 20px; font-weight: 700; color: #fff; }
  .hdr-text p  { font-size: 12.5px; color: #64748b; margin-top: 3px; }
  .msg { display: flex; gap: 14px; margin-bottom: 28px; align-items: flex-start; }
  .msg.user { flex-direction: row-reverse; }
  .avatar { width: 38px; height: 38px; flex: 0 0 38px; border-radius: 50%;
             display: flex; align-items: center; justify-content: center; font-size: 18px;
             background: #1e293b; }
  .avatar.user { background: linear-gradient(135deg, #003865, #0077c8); }
  .msg-body { flex: 1; min-width: 0; display: flex; flex-direction: column; }
  .msg.user .msg-body { align-items: flex-end; }
  .meta { font-size: 11px; font-weight: 700; letter-spacing: .04em; color: #64748b;
          margin-bottom: 7px; }
  .bubble { max-width: 88%; padding: 14px 18px; border-radius: 16px; font-size: 14px;
            line-height: 1.7; }
  .bubble.user { background: linear-gradient(135deg, #003865, #0077c8); color: #fff;
                 border-radius: 16px 16px 4px 16px; white-space: pre-wrap; }
  .bubble.kb   { background: #1a2740; color: #e2e8f0; border: 1px solid #2a4a72;
                 border-radius: 16px 16px 16px 4px; }
  /* markdown */
  .bubble.kb .apm-md-h { color: #93c5fd; font-size: 15px; margin: 14px 0 6px; }
  .bubble.kb .apm-md-p { margin: 0 0 10px; }
  .bubble.kb .apm-md-p:last-child { margin-bottom: 0; }
  .bubble.kb .apm-md-gap { height: 6px; }
  .bubble.kb .apm-md-list { padding-left: 20px; margin: 6px 0 10px; }
  .bubble.kb .apm-md-list li { margin-bottom: 5px; }
  .bubble.kb .apm-md-pre { background: #0b1220; padding: 14px 16px; border-radius: 10px;
                            overflow-x: auto; margin: 10px 0; border: 1px solid #1e3a5f; }
  .bubble.kb .apm-md-pre code { font-family: 'Fira Code','Cascadia Code',Consolas,monospace;
                                  font-size: 12.5px; color: #7dd3fc; line-height: 1.6; }
  .bubble.kb .apm-md-code { background: rgba(0,119,200,.15); color: #7dd3fc; padding: 2px 6px;
                              border-radius: 4px; font-family: monospace; font-size: 12.5px; }
  .bubble.kb .apm-md-cite { display: inline-flex; align-items: center; justify-content: center;
                              min-width: 18px; height: 18px; padding: 0 4px;
                              background: rgba(0,119,200,.25); color: #7cc0f2;
                              border-radius: 999px; font-size: 10px; font-weight: 700;
                              margin: 0 2px; vertical-align: super; }
  footer { border-top: 1px solid #1e3a5f; padding-top: 16px; margin-top: 40px;
           font-size: 12px; color: #475569; text-align: center; }
</style>
</head>
<body>
<div class="container">
  <header>
    <div class="logo">🗄</div>
    <div class="hdr-text">
      <h1>${this.esc(title)}</h1>
      <p>${this.esc(subtitle)}</p>
    </div>
  </header>
  <main>${rows}</main>
  <footer>AIDLC Platform Management · Knowledge Base · grounded · cite-or-abstain</footer>
</div>
</body>
</html>`;
  }

  private triggerDownload(html: string, filename: string): void {
    const blob = new Blob([html], { type: "text/html;charset=utf-8" });
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement("a");
    a.href     = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  }

  ngOnDestroy(): void { /* nothing to clean */ }
}
