"""Developer codegen ReAct agent — reads original source, writes changed files.

Replaces the former Claude CLI subprocess approach with a LangGraph-style manual
bind_tools() ReAct loop (same pattern used in agent/react.py after the P6 fix).
All data stays in AWS (ap-northeast-1 / us-east-1); no Anthropic-direct calls.

Architecture:
  - Two file-system tools: read_source_file / write_output_file
  - ReAct loop: reason → read original source → reason → write changed file → ... → return JSON
  - System prompt loaded from prompts/codegen.json (grounded, KB-cited, DEV-TODO discipline)

Config (from config.json / Settings):
  CODEBASE_ROOT         — root of the Japan Auto source code (default: input/Auto)
  CODEGEN_OUTPUT_ROOT   — root for output files (default: outputs/codegen)
  CODEGEN_RECURSION_LIMIT — max ReAct iterations (default: 15)

The function ``run_codegen`` is an async generator that yields (event_name, data_dict)
tuples — consumed by the SSE route handler as a streaming response. Calling code must pass
a live ChatBedrockConverse model (via DeveloperService.codegen_stream).

Cyclomatic ≤ 21: each function stays focused on one concern.
"""

from __future__ import annotations

import json
import textwrap
from functools import lru_cache
from pathlib import Path
from typing import Any, AsyncIterator

from app.lifecycle.stages.developer.schema import DevDocument, ImplTask
from app.services.agent_telemetry import AgentEvent, AgentExecutionContext, agent_execution

_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "codegen.json"

# _SAFE_EXTENSIONS: only read files with these extensions (prevent traversal to config/secrets)
_SAFE_EXTENSIONS = frozenset({
    ".ts", ".js", ".java", ".xml", ".xsd", ".wsdl", ".mermaid",
    ".sql", ".properties", ".json", ".yaml", ".yml", ".html", ".css",
    ".scss", ".md", ".txt",
})

# Max bytes to read per source file (prevent LLM context overflow)
_MAX_READ_BYTES = 32_768  # 32 KB


# ── Prompt ─────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def _system_template() -> str:
    return json.loads(_PROMPT_PATH.read_text(encoding="utf-8"))["system"]


# ── File-system tools (closures bound to workspace) ───────────────────────────

def _make_read_tool(codebase_root: Path):
    """Create a read_source_file tool bound to the given codebase root."""

    def read_source_file(path: str) -> str:
        """Read a source file from the Japan Auto codebase.

        Returns the file content as a string (truncated to 32 KB).
        Returns an error string if the file is outside the codebase root,
        has an unsafe extension, or does not exist.
        """
        try:
            resolved = (codebase_root / path).resolve()
            # Safety: must stay under codebase_root
            resolved.relative_to(codebase_root.resolve())
        except (ValueError, TypeError):
            return f"ERROR: path '{path}' is outside the codebase root — access denied."

        if resolved.suffix.lower() not in _SAFE_EXTENSIONS:
            return f"ERROR: extension '{resolved.suffix}' is not allowed for reading."

        if not resolved.exists():
            return f"ERROR: file not found: {resolved}"
        if not resolved.is_file():
            return f"ERROR: not a file: {resolved}"

        try:
            raw = resolved.read_bytes()
            text = raw[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
            if len(raw) > _MAX_READ_BYTES:
                text += f"\n\n[... TRUNCATED — file is {len(raw)} bytes; first {_MAX_READ_BYTES} shown ...]"
            return text
        except Exception as exc:  # noqa: BLE001
            return f"ERROR reading {path}: {exc}"

    read_source_file.name = "read_source_file"
    read_source_file.__doc__ = (
        "Read an original source file. "
        "path: relative path from the codebase root (e.g. 'UI-NewBusiness/src/app/policy.service.ts'). "
        "Returns file content as a string. Always read before writing changes."
    )
    return read_source_file


def _make_write_tool(output_dir: Path):
    """Create a write_output_file tool bound to the workspace output directory."""

    written: list[str] = []

    def write_output_file(path: str, content: str) -> str:
        """Write a new or changed file to the codegen output directory.

        path: relative path within the output directory (e.g. 'policy.service.ts').
        content: the complete file content (UTF-8 string).
        Returns a confirmation string on success or an error string on failure.
        """
        try:
            target = (output_dir / path).resolve()
            target.relative_to(output_dir.resolve())  # safety: stay under output_dir
        except (ValueError, TypeError):
            return f"ERROR: path '{path}' escapes the output directory — write denied."

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(str(target.relative_to(output_dir)))
            return f"OK: written {len(content)} chars → {path}"
        except Exception as exc:  # noqa: BLE001
            return f"ERROR writing {path}: {exc}"

    write_output_file.name = "write_output_file"
    write_output_file.__doc__ = (
        "Write a new or changed file to the output directory. "
        "path: relative path inside the output dir. "
        "content: complete file content as a string. "
        "Call after reading the original file to understand the current implementation."
    )
    return write_output_file, written


# ── Prompt builder ─────────────────────────────────────────────────────────────

def _build_user_prompt(doc: DevDocument) -> str:
    """Build the one-shot user prompt injected into the codegen ReAct loop."""
    task_blocks = []
    for task in doc.implementation_plan:
        block = _format_impl_task(task, doc)
        task_blocks.append(block)

    stubs_block = "\n\n---\n\n".join(task_blocks) if task_blocks else "(no implementation tasks)"

    # D5 — lane change specs give each generated file a lane + KB card + real target file(s).
    lane_lines = [
        f"- [{c.lane}] {c.title} — KB {', '.join(c.kb_ids)} — files: "
        f"{', '.join(c.target_files) or 'NEW (DEV-TODO)'} — {c.gap_status}"
        for c in doc.change_specs
    ]
    lanes_block = "\n".join(lane_lines) if lane_lines else "(no change specs)"

    return textwrap.dedent(f"""
        ## WORKSPACE: {doc.workspace_id}
        ## REQUIREMENT: {doc.requirement}
        ## CHANGE CLASS: {doc.change_class}

        ## IMPLEMENTATION TASKS

        {stubs_block}

        ## CHANGE SPECS BY LANE (frontend · backend · integration · db)
        Each file you write must trace to one of these specs (its lane + KB card + target file):
        {lanes_block}

        ## INSTRUCTIONS
        For each task above:
        1. Use read_source_file to read the ORIGINAL file at the source_locus path.
        2. Implement the change guided by: as-is→to-be FR delta, Gherkin AC, business rules, code stub.
        3. Write the implemented file using write_output_file.
        4. Leave DEV-TODO stubs for anything with gap_status='gap' or 'partial'.

        When ALL files are written, return STRICT JSON only:
        {{"written_files": ["<path1>", "<path2>", ...]}}
    """).strip()


def _format_impl_task(task: ImplTask, doc: DevDocument) -> str:
    """Format one ImplTask as a text block for the codegen prompt."""
    lines = [
        f"### STORY: {task.story_id} — {task.title or task.story_id}",
        f"Priority: {task.story_priority} | Points: {task.story_points} | Status: {task.gap_status}",
    ]

    if task.story_text:
        lines.append(f"\nUser Story: {task.story_text}")

    if task.gherkin_ac:
        ac = task.gherkin_ac
        lines.append(
            f"\nGherkin AC:\n"
            f"  Given: {ac.get('given', '')}\n"
            f"  When:  {ac.get('when', '')}\n"
            f"  Then:  {ac.get('then', '')}"
        )

    if task.fr_deltas:
        lines.append("\nFR Deltas (as-is → to-be):")
        for delta in task.fr_deltas:
            lines.append(
                f"  [{delta.fr_id}] {delta.title}\n"
                f"    AS-IS: {delta.as_is or 'not documented'}\n"
                f"    TO-BE: {delta.to_be or 'DEV-TODO: determine from BA'}"
            )

    if task.business_rules_summary:
        lines.append("\nApplicable Business Rules:")
        for rule in task.business_rules_summary:
            lines.append(f"  {rule}")

    if task.definition_of_done:
        lines.append("\nDefinition of Done:")
        for item in task.definition_of_done:
            lines.append(f"  ☐ {item}")

    # Code stubs for this story
    story_stubs = [s for s in doc.code_stubs if s.story_id == task.story_id and s.filename]
    if story_stubs:
        lines.append("\nCode Stubs to Implement:")
        for stub in story_stubs:
            lines.append(
                f"\n  FILE: {stub.filename}\n"
                f"  KB card: [{stub.component_id}] | Stack: {stub.stack} | Language: {stub.language}\n"
                f"  Source locus: {stub.source_locus or 'UNKNOWN — read from codebase'}\n"
                f"  ```{stub.language}\n{stub.code}\n  ```"
            )

    if task.components_to_modify:
        lines.append(f"\nComponents to modify: {', '.join(task.components_to_modify)}")
    if task.integrations_to_wire:
        lines.append(f"Integrations to wire: {', '.join(task.integrations_to_wire)}")
    if task.screens_to_update:
        lines.append(f"Screens to update: {', '.join(task.screens_to_update)}")

    return "\n".join(lines)


# ── ReAct tool-use loop ────────────────────────────────────────────────────────

def _text_of(msg: object) -> str:
    """Extract text content from a LangChain message object."""
    if hasattr(msg, "content"):
        c = msg.content
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            parts = [p.get("text", "") if isinstance(p, dict) else str(p) for p in c]
            return " ".join(parts)
    return str(msg)


async def _run_codegen_react(
    *,
    model: object,
    tools: list,
    system_prompt: str,
    user_prompt: str,
    recursion_limit: int,
    tel: object | None = None,
) -> list[str]:
    """Manual ReAct bind_tools() loop — identical pattern to agent/react.py (P6).

    Returns a list of written file paths extracted from the agent's final JSON.
    Returns [] on failure or when model is None.
    """
    from app.utils.logging import log

    _tel_handle = tel  # make available to _dispatch_tool calls below

    if model is None:
        return []

    try:
        from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
    except ImportError as exc:
        log.warning(f"[codegen] langchain_core not available: {exc}")
        return []

    try:
        model_with_tools = model.bind_tools(tools) if tools else model
    except (AttributeError, TypeError):
        model_with_tools = model

    messages: list[Any] = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    for iteration in range(recursion_limit):
        try:
            response = await model_with_tools.ainvoke(messages)
        except Exception as exc:  # noqa: BLE001
            log.warning(f"[codegen] model invoke failed at iteration {iteration}: {exc}")
            return []

        messages.append(response)

        tool_calls: list[dict] = getattr(response, "tool_calls", []) or []

        if not tool_calls:
            # Final answer — extract written_files from JSON
            text = _text_of(response)
            return _extract_written_files(text)

        for tc in tool_calls:
            tool_call_id = tc.get("id") or f"tc-{iteration}"
            result_text = await _dispatch_tool(tc, tools, tel=_tel_handle)
            messages.append(ToolMessage(content=result_text, tool_call_id=tool_call_id))

    # Recursion limit reached — extract from last message
    last = messages[-1] if messages else None
    text = _text_of(last) if last else ""
    log.warning(f"[codegen] recursion limit {recursion_limit} reached")
    return _extract_written_files(text)


async def _dispatch_tool(tool_call: dict, tools: list, tel: object | None = None) -> str:
    """Execute one tool call and return the result string.

    Never raises — errors returned as descriptive strings for the model to handle.
    tel: optional AgentTelemetry handle for recording tool_call events.
    """
    import asyncio
    import time as _time

    tool_name = tool_call.get("name", "")
    tool_args = tool_call.get("args", {})
    t0 = _time.monotonic()

    result = f"Unknown tool: {tool_name!r}"
    status = "success"
    for tool in tools:
        name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
        if name != tool_name:
            continue
        try:
            if hasattr(tool, "ainvoke"):
                result = str(await tool.ainvoke(tool_args))
            elif hasattr(tool, "invoke"):
                result = str(tool.invoke(tool_args))
            elif callable(tool):
                if asyncio.iscoroutinefunction(tool):
                    result = str(await tool(**tool_args))
                else:
                    result = str(tool(**tool_args))
            else:
                result = f"Tool {tool_name!r} is not callable."
                status = "error"
        except Exception as exc:  # noqa: BLE001
            result = f"Tool error ({tool_name}): {exc}"
            status = "error"
        break

    if tel is not None:
        latency_ms = (_time.monotonic() - t0) * 1000
        try:
            tel.record_tool_call(tool_name=tool_name, latency_ms=latency_ms, status=status)
        except Exception:  # noqa: BLE001 — telemetry never breaks tool execution
            pass

    return result


def _extract_written_files(text: str) -> list[str]:
    """Extract the written_files list from a JSON response string."""
    if not text:
        return []
    start = text.rfind("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        return []
    try:
        obj = json.loads(text[start:end])
        files = obj.get("written_files", [])
        return [str(f) for f in files] if isinstance(files, list) else []
    except (json.JSONDecodeError, ValueError):
        return []


# ── Public API ─────────────────────────────────────────────────────────────────

async def run_codegen(
    doc: DevDocument,
    *,
    workspace_id: str,
    model: object,
) -> AsyncIterator[tuple[str, dict]]:
    """ReAct codegen agent — reads original source, implements changes, writes output files.

    Async generator yielding (event_name, data_dict) SSE tuples:
      status  — progress updates
      result  — final result with written_files list and output_dir
      error   — on failure (non-fatal; caller streams on)

    Requires a live ChatBedrockConverse model (passed from DeveloperService).
    When model is None (test/fixture mode): yields an empty result immediately.
    """
    from app.config import get_settings
    from app.utils.logging import log

    settings = get_settings()
    codebase_root = Path(settings.CODEBASE_ROOT).resolve()
    output_dir = Path(settings.CODEGEN_OUTPUT_ROOT) / workspace_id
    output_dir.mkdir(parents=True, exist_ok=True)
    recursion_limit: int = settings.CODEGEN_RECURSION_LIMIT

    if not doc.code_stubs and not doc.implementation_plan:
        yield ("status", {"message": "No code stubs or implementation tasks to generate."})
        yield ("result", {"written_files": [], "output_dir": str(output_dir)})
        return

    # Build tools
    read_tool = _make_read_tool(codebase_root)
    write_tool, written_list = _make_write_tool(output_dir)

    tools = [read_tool, write_tool]

    yield ("status", {
        "message": (
            f"Starting codegen ReAct agent: "
            f"{len(doc.implementation_plan)} task(s), "
            f"{len(doc.code_stubs)} stub(s). "
            f"Codebase: {codebase_root}"
        )
    })

    if model is None:
        log.info("[codegen] model is None — skipping LLM codegen (fixture mode)")
        yield ("status", {"message": "No model configured — codegen skipped (fixture mode)."})
        yield ("result", {"written_files": [], "output_dir": str(output_dir)})
        return

    system_prompt = _system_template()
    user_prompt = _build_user_prompt(doc)

    ctx = AgentExecutionContext(
        agent_id="codegen",
        agent_name="Codegen ReAct Agent",
        workflow_id=workspace_id,
    )

    yield ("status", {"message": "Running ReAct codegen loop (read → implement → write)…"})

    try:
        with agent_execution(ctx) as tel:
            tel.record_event(AgentEvent.CONTEXT_RETRIEVED, {
                "tasks": len(doc.implementation_plan),
                "stubs": len(doc.code_stubs),
                "codebase_root": str(codebase_root),
            })
            tel.prompt_built()
            written_files = await _run_codegen_react(
                model=model,
                tools=tools,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                recursion_limit=recursion_limit,
                tel=tel,
            )
            tel.output_generated(artifact_type="codegen_files")
            tel.record_event(AgentEvent.OUTPUT_GENERATED, {"files_written": len(written_files)})
    except Exception as exc:  # noqa: BLE001 — codegen is best-effort
        log.error(f"[codegen] ReAct loop error for {workspace_id}: {exc}", exc_info=True)
        yield ("error", {"detail": f"Codegen agent error: {exc}"})
        return

    # Merge: the written_list from the tool closures is authoritative (actual disk writes).
    # The JSON from the model is advisory; prefer disk-confirmed list when non-empty.
    final_written = written_list if written_list else written_files

    yield ("status", {"message": f"Codegen complete — {len(final_written)} file(s) written."})
    yield ("result", {
        "written_files": final_written,
        "output_dir": str(output_dir),
    })
