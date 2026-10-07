/**
 * Opens the grounded Q&A chat in a SEPARATE browser window so the pipeline view state is never
 * impacted. The window talks to the backend directly: POST {base}/projects/{projectId}/chat.
 */
export function openChatWindow(base: string, projectId: string, token: string): void {
  const w = window.open("", "apm_kb_chat", "width=470,height=660");
  if (!w) {
    alert("Popup blocked — allow popups to open the chat window.");
    return;
  }
  const endpoint = `${base}/projects/${encodeURIComponent(projectId)}/chat`;
  const pid = projectId || "project";
  // Split the closing script tag so the parent bundle's HTML isn't terminated early.
  const s = "scr" + "ipt";
  w.document.write(
    `<!doctype html><html><head><meta charset="utf-8"><title>Ask the KB — ${pid}</title>
<style>
body{margin:0;font-family:'Inter','Segoe UI',sans-serif;background:#f4f5f7;color:#1e293b;display:flex;flex-direction:column;height:100vh}
header{background:linear-gradient(135deg,#003865,#0077c8);color:#fff;padding:12px 16px;font-weight:700}
header small{display:block;font-weight:400;font-size:11px;opacity:.85}
#m{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:8px}
.b{max-width:88%;padding:8px 11px;border-radius:13px;font-size:13px;line-height:1.45}
.u{align-self:flex-end;background:linear-gradient(135deg,#003865,#0077c8);color:#fff}
.a{align-self:flex-start;background:#fff;border:1px solid #e5e7eb}
footer{display:flex;gap:8px;padding:10px;border-top:1px solid #e5e7eb;background:#fff}
input{flex:1;padding:9px 11px;border:1px solid #e5e7eb;border-radius:9px;font:inherit}
button{border:none;border-radius:9px;padding:9px 14px;background:linear-gradient(135deg,#003865,#0077c8);color:#fff;font-weight:600;cursor:pointer}
</style></head><body>
<header>Ask the knowledge base<small>${pid} &middot; grounded &middot; cite-or-abstain</small></header>
<div id="m"><div class="b a">Ask a question about this project. Answers cite KB cards or abstain.</div></div>
<footer><input id="q" placeholder="Ask a question…"><button id="s">Send</button></footer>
<${s}>
var EP=${JSON.stringify(endpoint)},TK=${JSON.stringify(token)};
function add(t,c){var d=document.createElement('div');d.className='b '+c;d.textContent=t;var m=document.getElementById('m');m.appendChild(d);m.scrollTop=1e9;return d;}
function send(){var i=document.getElementById('q'),q=(i.value||'').trim();if(!q)return;add(q,'u');i.value='';var w=add('…','a');
var h={'Content-Type':'application/json'};if(TK)h['Authorization']='Bearer '+TK;
fetch(EP,{method:'POST',headers:h,body:JSON.stringify({question:q})}).then(function(r){return r.json();})
.then(function(d){w.textContent=(d&&(d.answer||d.text))||'(no answer returned)';})
.catch(function(e){w.textContent='Request failed: '+e;});}
document.getElementById('s').onclick=send;
document.getElementById('q').addEventListener('keydown',function(e){if(e.key==='Enter')send();});
</${s}></body></html>`,
  );
  w.document.close();
}
