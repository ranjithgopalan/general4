"""Unit tests for JobService.revise_artifact (human edit/upload before approval).

These exercise the guard clauses and the run-repoint logic directly. The
artefact-store write (`write_markdown_revision`, which needs S3/local storage) is
patched — its own behaviour is covered by the end-to-end verification against a
running agent. Run from the agent repo root:

    set PYTHONPATH=.
    python -m pytest app/agentic_platform/tests/test_revise_artifact.py -q
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agentic_platform.api.services.jobs import ApprovalError, JobService
from app.agentic_platform.fe_core.kb.models import ArtifactStatus
from app.agentic_platform.fe_core.pipeline.models import StageState

REVISION_FN = "app.agentic_platform.fe_core.artifacts.revision.write_markdown_revision"


class _FakeStore:
    """Minimal store surface used by revise_artifact."""

    def __init__(self, run):
        self._runs = {run.run_id: run} if run else {}
        self.saved: list = []

    def get_run(self, run_id):
        return self._runs.get(run_id)

    def save_run(self, run):
        self.saved.append(run)
        self._runs[run.run_id] = run
        return run


def _run(**kw):
    base = dict(run_id="r1", state=StageState.WAITING_FOR_APPROVAL, artifact_ids=["a1"])
    base.update(kw)
    return SimpleNamespace(**base)


def _artifact(**kw):
    base = dict(id="a1", run_id="r1", status=ArtifactStatus.DRAFT)
    base.update(kw)
    return SimpleNamespace(**base)


def _service(store):
    # All deps supplied so __init__ does no heavy discovery.
    return JobService(pipeline=object(), settings=object(), store=store, kb=object())


def _patch_revision(monkeypatch, revision=None):
    revision = revision or SimpleNamespace(id="a2", version=2)
    monkeypatch.setattr(REVISION_FN, lambda **_: revision)
    return revision


def test_revise_bumps_version_and_repoints_run(monkeypatch):
    run = _run(artifact_ids=["a1", "other"])
    store = _FakeStore(run)
    revision = _patch_revision(monkeypatch)

    out = _service(store).revise_artifact(
        _artifact(), content_md="# edited", editor="me@aig.com", comment="tightened")

    assert out is revision
    assert run.artifact_ids == ["a2", "other"]      # a1 -> a2, siblings untouched
    assert store.saved and store.saved[-1] is run    # run persisted


def test_revise_from_in_review_is_allowed(monkeypatch):
    run = _run()
    store = _FakeStore(run)
    _patch_revision(monkeypatch)
    out = _service(store).revise_artifact(
        _artifact(status=ArtifactStatus.IN_REVIEW), content_md="x", editor="me", comment=None)
    assert out.id == "a2"


def test_empty_content_rejected(monkeypatch):
    store = _FakeStore(_run())
    _patch_revision(monkeypatch)
    with pytest.raises(ApprovalError):
        _service(store).revise_artifact(_artifact(), content_md="   ", editor="me", comment=None)


def test_artifact_without_run_rejected(monkeypatch):
    store = _FakeStore(_run())
    _patch_revision(monkeypatch)
    with pytest.raises(ApprovalError):
        _service(store).revise_artifact(
            _artifact(run_id=None), content_md="x", editor="me", comment=None)


def test_run_not_awaiting_approval_rejected(monkeypatch):
    run = _run(state=StageState.COMPLETED)
    store = _FakeStore(run)
    _patch_revision(monkeypatch)
    with pytest.raises(ApprovalError):
        _service(store).revise_artifact(_artifact(), content_md="x", editor="me", comment=None)


def test_approved_artifact_cannot_be_revised(monkeypatch):
    store = _FakeStore(_run())
    _patch_revision(monkeypatch)
    with pytest.raises(ApprovalError):
        _service(store).revise_artifact(
            _artifact(status=ArtifactStatus.APPROVED), content_md="x", editor="me", comment=None)
