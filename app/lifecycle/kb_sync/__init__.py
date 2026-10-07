"""Central KB-refresh round-trip (docs/23).

Closes the forward→reverse loop: when a workspace is merged (DevOps · MERGE stage) its accepted,
grounded artifacts are transformed into a KB delta, merged onto the current KB export, and loaded as
a NEW immutable version (STAGING) via the central kb-indexer — awaiting human promotion to ACTIVE.

This is a BACKEND service (the ``/kb-refresh`` plugin is a dev-time authoring tool that the runtime
platform cannot invoke). It reuses the central kb-indexer via its CLI, in a subprocess.
"""

from app.lifecycle.kb_sync.service import KbRefreshResult, KbRefreshService

__all__ = ["KbRefreshService", "KbRefreshResult"]
