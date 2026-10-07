"""Bounded ReAct builtin -- structured, grounded JSON stages without Claude Code.

Some stages are not documents: an impact set, a story/epic backlog, a
traceability matrix. Their value is *which KB ids* they name and how they link,
not prose. For those a full Claude Code session (plugins, skills, 120 turns) is
the wrong tool: expensive, hard to bound, and its output cannot be verified id by
id. This builtin runs a **bounded reason -> tool -> observe loop** instead:

  * model: Bedrock Converse via `fe_core.rag.bedrock` (the estate's existing RAG
    answer path -- no new SDK, no API key), Sonnet by default, temperature 0;
  * tools: three in-process closures over the application's card KB
    (`kb_cards_search`, `kb_card`, `graph_neighbors`) -- zero HTTP, and every id
    they surface is recorded in a **whitelist**;
  * budget: `max_steps` model calls (default 24); two steps before the cap a
    stop message is injected so the model emits its JSON instead of dying mid
    tool call; the system prompt carries a cache point;
  * output: JSON matching the stage's schema, **filtered to whitelisted ids** --
    an id the model did not retrieve is dropped, never trusted; on empty or
    invalid JSON one retry with a reinforced instruction (retry-with-merge).

The executor writes the result as `<artifact_type>.json` plus a rendered
`<artifact_type>.md` into the run worktree, so the rest of the pipeline
(deliverable check, persistence, approval gates) is unchanged.

Selected per stage in the pipeline YAML::

    owner: {kind: builtin, handler: react}
    react:
      schema: {...}            # JSON schema the model must satisfy
      id_fields: [kb_ids]      # which fields hold KB ids (whitelist-filtered)
      max_steps: 16            # optional, default FE_REACT_MAX_STEPS
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.agentic_platform.fe_core.kb.cards import CARD_ID_RE, CardStore

logger = logging.getLogger(__name__)

_FORCE_JSON = (
    "STOP TOOL CALLS NOW. You have used most of your allowed steps. Output the final "
    "JSON immediately using ONLY the cards already shown above. Return ONLY valid JSON "
    "starting with '{' and ending with '}'. No markdown, no commentary."
)
_RETRY_SUFFIX = (
    "\n\nYour previous answer was not valid JSON for the schema. Return ONLY a JSON object "
    "matching the schema -- no fences, no prose -- with every required field filled and "
    "every KB id copied exactly from a tool result."
)


@dataclass
class ReactToolset:
    """In-process KB tools + the whitelist sink they write to."""

    store: CardStore
    collected: set[str] = field(default_factory=set)

    def specs(self) -> list[dict]:
        return [
            {"name": "kb_cards_search",
             "description": "Search the knowledge base for cards relevant to a query; returns "
                            "ids, kinds, labels and summaries. Cite ids exactly as returned.",
             "input_schema": {"type": "object", "properties": {
                 "query": {"type": "string"},
                 "top_k": {"type": "integer", "minimum": 1, "maximum": 20},
                 "kinds": {"type": "string", "description": "optional comma-separated kinds, e.g. BR,FR"}},
                 "required": ["query"]}},
            {"name": "kb_card",
             "description": "Read one card's body and evidence by id.",
             "input_schema": {"type": "object", "properties": {"card_id": {"type": "string"}},
                              "required": ["card_id"]}},
            {"name": "graph_neighbors",
             "description": "Typed-edge neighbours of a KB node (what governs / depends on / "
                            "is served by it).",
             "input_schema": {"type": "object", "properties": {
                 "node_id": {"type": "string"},
                 "depth": {"type": "integer", "minimum": 1, "maximum": 2}},
                 "required": ["node_id"]}},
        ]

    def call(self, name: str, args: dict[str, Any]) -> str:
        """Execute one tool. Never raises; errors come back as text."""
        try:
            if name == "kb_cards_search":
                kinds = [k for k in str(args.get("kinds") or "").split(",") if k] or None
                hits = self.store.search(str(args.get("query") or ""),
                                         top_k=int(args.get("top_k") or 8), kinds=kinds)
                if not hits:
                    return "no matching cards"
                lines = []
                for h in hits:
                    self.collected.add(h.card.id)
                    summary = (h.card.summary or h.card.body[:200]).replace("\n", " ")
                    lines.append(f"[{h.card.id}] {h.card.kind}: {h.card.label} -- {summary}")
                return "\n".join(lines)
            if name == "kb_card":
                cid = str(args.get("card_id") or "").strip()
                card = self.store.get(cid)
                if card is None:
                    return f"{cid}: not found"
                self.collected.add(card.id)
                ev = self.store.evidence_for(card.id)[:5]
                ev_txt = ("\nevidence: " + "; ".join(
                    f"{e.get('source_doc', '?')} {json.dumps(e.get('locus', {}))}" for e in ev)) if ev else ""
                return f"[{card.id}] {card.label} ({card.kind}): {card.summary}\n{card.body[:3000]}{ev_txt}"
            if name == "graph_neighbors":
                nid = str(args.get("node_id") or "").strip()
                nodes, edges = self.store.neighbors(nid, depth=int(args.get("depth") or 1))
                if not nodes:
                    return f"{nid}: not found"
                for n in nodes:
                    self.collected.add(n.id)
                e_txt = "; ".join(f"{e.source}-{e.label}->{e.target}" for e in edges[:30]) or "no edges"
                n_txt = ", ".join(f"[{n.id}] {n.label}" for n in nodes[:40])
                return f"{e_txt}\nnodes: {n_txt}"
            return f"unknown tool {name!r}"
        except Exception as exc:  # noqa: BLE001
            return f"tool error ({name}): {exc}"


@dataclass
class ReactOutcome:
    data: dict[str, Any] | None
    collected: set[str]
    steps: int
    dropped_ids: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    hit_limit: bool = False
    retried: bool = False
    tool_calls: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return (self.input_tokens + self.output_tokens
                + self.cache_read_input_tokens + self.cache_creation_input_tokens)


ConverseFn = Callable[..., dict]


def extract_json(text: str) -> dict | None:
    """First balanced JSON object in `text` (tolerates fences and preamble)."""
    if not text:
        return None
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.M)
    start = text.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                        return obj if isinstance(obj, dict) else None
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def filter_ids(data: Any, allowed: set[str], id_fields: list[str], dropped: list[str]) -> Any:
    """Recursively drop KB ids that were never surfaced by a tool.

    Applies to values of `id_fields` (strings or lists of strings) anywhere in
    the structure. Anything else is left alone.
    """
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if k in id_fields:
                if isinstance(v, str):
                    ok = _keep_id(v, allowed, dropped)
                    out[k] = v if ok else None
                elif isinstance(v, list):
                    out[k] = [x for x in v if not isinstance(x, str) or _keep_id(x, allowed, dropped)]
                else:
                    out[k] = v
            else:
                out[k] = filter_ids(v, allowed, id_fields, dropped)
        return out
    if isinstance(data, list):
        return [filter_ids(x, allowed, id_fields, dropped) for x in data]
    return data


def _keep_id(value: str, allowed: set[str], dropped: list[str]) -> bool:
    if not CARD_ID_RE.fullmatch(value.strip()):
        return True          # not a KB id -- not our business
    if value.strip() in allowed:
        return True
    dropped.append(value.strip())
    return False


def _text_of(message: dict) -> str:
    return "".join(b.get("text", "") for b in message.get("content", []) if "text" in b)


def _accumulate(usage: dict, outcome: ReactOutcome) -> None:
    outcome.input_tokens += int(usage.get("inputTokens") or 0)
    outcome.output_tokens += int(usage.get("outputTokens") or 0)
    outcome.cache_read_input_tokens += int(usage.get("cacheReadInputTokens") or 0)
    outcome.cache_creation_input_tokens += int(usage.get("cacheWriteInputTokens") or 0)


def run_loop(*, system: str, user: str, toolset: ReactToolset, converse: ConverseFn,
             model: str | None, max_steps: int, max_tokens: int,
             id_fields: list[str], required_key: str | None = None,
             on_step: Callable[[str, str], None] | None = None) -> ReactOutcome:
    """The bounded loop. Synchronous (Bedrock client is sync); wrap in a thread."""
    outcome = ReactOutcome(data=None, collected=toolset.collected, steps=0, dropped_ids=[])
    tools = toolset.specs()

    def attempt(user_text: str) -> dict | None:
        messages: list[dict] = [{"role": "user", "content": [{"text": user_text}]}]
        for step in range(max_steps):
            if step == max(0, max_steps - 2):
                messages.append({"role": "user", "content": [{"text": _FORCE_JSON}]})
            resp = converse(messages, system=system, tools=tools, model=model,
                            max_tokens=max_tokens, temperature=0.0)
            outcome.steps += 1
            _accumulate(resp.get("usage") or {}, outcome)
            msg = (resp.get("output") or {}).get("message") or {"role": "assistant", "content": []}
            messages.append(msg)
            tool_uses = [b["toolUse"] for b in msg.get("content", []) if "toolUse" in b]
            if not tool_uses or resp.get("stopReason") == "end_turn":
                parsed = extract_json(_text_of(msg))
                if parsed is not None or not tool_uses:
                    return parsed
            results = []
            for tu in tool_uses:
                name, args = tu.get("name", ""), tu.get("input") or {}
                text = toolset.call(name, args if isinstance(args, dict) else {})
                outcome.tool_calls.append(name)
                if on_step:
                    on_step(name, text[:120])
                results.append({"toolResult": {"toolUseId": tu.get("toolUseId"),
                                               "content": [{"text": text}]}})
            messages.append({"role": "user", "content": results})
        outcome.hit_limit = True
        # limit hit: salvage whatever the last assistant text holds
        for m in reversed(messages):
            if m.get("role") == "assistant":
                return extract_json(_text_of(m))
        return None

    data = attempt(user)
    if not _usable(data, required_key):
        outcome.retried = True
        logger.info("[react] first attempt yielded no usable JSON; retrying with reinforced instruction")
        data = attempt(user + _RETRY_SUFFIX)
    if data is not None:
        data = filter_ids(data, toolset.collected, id_fields, outcome.dropped_ids)
    outcome.data = data
    return outcome


def _usable(data: dict | None, required_key: str | None) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    if required_key and not data.get(required_key):
        return False
    return True


# -- executor entry point ---------------------------------------------------

@dataclass
class ReactSpec:
    schema: dict[str, Any]
    id_fields: list[str]
    max_steps: int
    required_key: str | None = None
    instructions: str = ""

    @classmethod
    def from_stage(cls, stage: Any, default_steps: int) -> "ReactSpec":
        cfg = getattr(stage, "react", None) or {}
        return cls(
            schema=cfg.get("schema") or {"type": "object"},
            id_fields=list(cfg.get("id_fields") or ["kb_ids", "card_ids", "citations"]),
            max_steps=int(cfg.get("max_steps") or default_steps),
            required_key=cfg.get("required_key"),
            instructions=str(cfg.get("instructions") or ""),
        )


def build_system_prompt(stage_name: str, deliverable: str, spec: ReactSpec) -> str:
    return (
        f"You are the {stage_name} agent of an SDLC pipeline. Produce: {deliverable}\n\n"
        "Work in a bounded loop: make a few targeted knowledge-base lookups with the tools, "
        "then answer. Every KB id you output must be copied exactly from a tool result -- "
        "ids you did not retrieve are removed automatically. Prefer 3-6 lookups; do not keep "
        "exploring.\n\n"
        f"{spec.instructions}\n\n"
        "Final answer: ONLY a JSON object matching this schema, no markdown fences:\n"
        f"{json.dumps(spec.schema, indent=2)}"
    )


async def run_stage(*, stage: Any, prompt: str, store: CardStore, settings: Any,
                    converse: ConverseFn | None = None,
                    on_step: Callable[[str, str], None] | None = None) -> ReactOutcome:
    """Run the builtin for `stage` with the executor's assembled `prompt` as the
    user message. Runs the sync Bedrock loop in a worker thread."""
    if converse is None:
        from app.agentic_platform.fe_core.rag.bedrock import converse as _bedrock_converse  # noqa: PLC0415
        converse = _bedrock_converse
    spec = ReactSpec.from_stage(stage, settings.fe_react_max_steps)
    toolset = ReactToolset(store=store)
    system = build_system_prompt(stage.name, stage.deliverable, spec)
    model = settings.fe_react_model or None
    return await asyncio.to_thread(
        run_loop, system=system, user=prompt, toolset=toolset, converse=converse,
        model=model, max_steps=spec.max_steps, max_tokens=settings.fe_react_max_tokens,
        id_fields=spec.id_fields, required_key=spec.required_key, on_step=on_step,
    )


def render_markdown(title: str, data: dict[str, Any] | None, outcome: ReactOutcome) -> str:
    """A reviewable Markdown rendering of the JSON (tables for lists of objects)."""
    lines = [f"# {title}", ""]
    if data is None:
        lines += ["_No valid result was produced._", ""]
    else:
        for key, value in data.items():
            lines.append(f"## {key}")
            lines.append("")
            if isinstance(value, list) and value and all(isinstance(x, dict) for x in value):
                cols: list[str] = []
                for row in value:
                    for k in row:
                        if k not in cols:
                            cols.append(k)
                lines.append("| " + " | ".join(cols) + " |")
                lines.append("|" + "---|" * len(cols))
                for row in value:
                    lines.append("| " + " | ".join(
                        _cell(row.get(c)) for c in cols) + " |")
            elif isinstance(value, list):
                lines += [f"- {_cell(v)}" for v in value] or ["_(empty)_"]
            elif isinstance(value, dict):
                lines.append("```json")
                lines.append(json.dumps(value, indent=2))
                lines.append("```")
            else:
                lines.append(str(value))
            lines.append("")
    lines += ["## Grounding", "",
              f"- KB ids retrieved: {len(outcome.collected)}",
              # count only: listing the dropped ids here would make the grounding
              # check read them as citations of unknown cards
              f"- ids dropped as unretrieved: {len(outcome.dropped_ids)} (see <type>.json `_grounding`)",
              f"- steps: {outcome.steps}{' (limit hit)' if outcome.hit_limit else ''}"
              f"{' (retried)' if outcome.retried else ''}",
              f"- tokens: in={outcome.input_tokens} cache_read={outcome.cache_read_input_tokens} "
              f"out={outcome.output_tokens}", ""]
    return "\n".join(lines)


def _cell(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v)
    return str(v if v is not None else "").replace("\n", " ").replace("|", "\\|")
