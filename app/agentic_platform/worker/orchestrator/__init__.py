"""LangGraph orchestrator (FE_ORCHESTRATOR=langgraph).

One StateGraph per workspace, thread_id = workspace_id. Every stage is the same
node shape — intake → run → outtake → gate — implemented by reusing the exact
code the manual path uses (JobService.create_run / StageExecutor.execute /
JobService.approve). The graph adds only the *hand-off*: conditional edges from
stage eligibility, an `interrupt()` at every approval gate that the named
persona resumes from the console, reject → re-run (v+1), and, for the Global
tier, fan-out to one Mini thread per approved EPIC.

Nothing here talks to the laptop. The Mini graph runs its document stages
server-side and pauses at a hand-off gate while the developer / tester work
locally with Claude Code CLI; their git-ref artefacts, once approved in the
console, resume it (M8 security-opa, M12 docs-uat-deploy) to END.
"""

from app.agentic_platform.worker.orchestrator.service import OrchestratorService

__all__ = ["OrchestratorService"]
