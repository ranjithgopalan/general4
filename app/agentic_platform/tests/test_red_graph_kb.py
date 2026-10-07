"""Tests for the dependency graph in the platform KB tables (fe_core.red_graph.kb_graph).

The row-mapping tests need no database. The round-trip tests write and read a small graph
under kb_version '_library-test-deps-v1' and are skipped unless RED_GRAPH_TEST_KB_URL is set:

    set PYTHONPATH=.
    set RED_GRAPH_TEST_KB_URL=postgresql://postgres:admin@localhost:5435/lmod
    python -m pytest app/agentic_platform/tests/test_red_graph_kb.py -q
"""
from __future__ import annotations

import os

import pytest

from app.agentic_platform.fe_core.red_graph import kb_graph
from app.agentic_platform.fe_core.red_graph.derive import derive_from_rows

ROOT = r"C:\x\active_files"
KB_URL = os.environ.get("RED_GRAPH_TEST_KB_URL")
KB_SCHEMA = os.environ.get("RED_GRAPH_TEST_KB_SCHEMA", "form_rationalization_anh")
GEAR = "test"


def row(name, folder, deps=(), triggers=(), tables=(), loc=10):
    return {"file_name": name, "file_path": rf"{ROOT}\{folder}\App\{name}", "layer": "UI", "risk_level": "low",
            "loc": loc, "purpose": f"Purpose of {name}", "internal_dependencies": list(deps),
            "entry_points": [{"triggers": list(triggers)}], "database_tables": list(tables)}


def sample_graph():
    return derive_from_rows([
        row("a.asp", "m1", deps=["b.asp", "Datastore2.inc"], tables=["tblUser"]),
        row("b.asp", "m2", deps=["c.asp"], triggers=["HTTP POST from a.asp"]),
        row("c.asp", "m2", tables=["tblUser", "Security"]),
    ])


def test_naming_follows_the_library_convention():
    assert kb_graph.library_gear_id("1429") == "_library-1429"
    assert kb_graph.library_deps_gear_id("1429") == "_library-1429-deps"
    assert kb_graph.library_deps_version("1429") == "_library-1429-deps-v1"
    assert kb_graph.library_deps_version("1429", 7) == "_library-1429-deps-v7"
    assert kb_graph.version_number("_library-1429-deps-v12") == 12
    assert kb_graph.version_number("_library-re-v3", kb_graph.CARDS_VERSION_RE) == 3
    assert kb_graph.version_number("nope") is None
    assert kb_graph.library_gear_id("") == "_library"
    assert kb_graph.code_for("1429") == "L1429" and kb_graph.code_for("proj_0059") == "P0059"


def test_rows_from_derived_maps_pages_tables_and_edge_labels():
    rows = kb_graph.rows_from_derived(sample_graph(), kb_version="v", code="T", source="x.json")
    ids = {r[0]: r for r in rows["nodes"]}
    assert sorted(i for i in ids if i.startswith("CMP-")) == ["CMP-T-0001", "CMP-T-0002", "CMP-T-0003"]
    assert sorted(i for i in ids if i.startswith("ENT-")) == ["ENT-T-T0001", "ENT-T-T0002"]
    a = ids["CMP-T-0001"]
    assert a[2] == "Component" and a[3] == "a.asp" and a[4] == "m1"           # kind, label, category = module
    labels = sorted((r[3], r[1], r[2]) for r in rows["edges"])
    assert ("DEPENDS_ON", "CMP-T-0001", "CMP-T-0002") in labels               # a USES b
    assert ("TRIGGERS", "CMP-T-0001", "CMP-T-0002") in labels                 # a posts to b
    assert ("DEPENDS_ON", "CMP-T-0002", "CMP-T-0003") in labels               # b USES c
    assert sum(1 for l in labels if l[0] == "REFERENCES") == 3                # a->tblUser, c->tblUser, c->Security
    assert all(r[4] == "stated" for r in rows["edges"])
    assert rows["stats"]["pages"] == 3 and rows["stats"]["tables"] == 2 and rows["stats"]["modules"] == 2
    assert len(rows["cards"]) == 3


def test_persist_version_files_writes_upload_graph_summary_and_manifest(tmp_path):
    from app.agentic_platform.fe_core.artifacts.store import LocalArtifactStore

    rows = kb_graph.rows_from_derived(sample_graph(), kb_version="_library-test-deps-v3", code="T", source="red.json")
    store = LocalArtifactStore(tmp_path)
    rec = kb_graph.persist_version_files(raw=b'{"result": {"files": []}}', rows=rows, kb_version="_library-test-deps-v3",
                                         gear="test", number=3, source_name="red.json", store=store, key_root="fe")
    assert rec["ok"] is True and rec["store"] == "local"
    assert rec["key_prefix"].startswith("fe/_library/test/deps/v3/deps-")
    assert sorted(rec["files"]) == ["graph.json", "red.json", "summary.json"]
    folder = tmp_path / rec["key_prefix"]
    assert (folder / "manifest.json").is_file() and (folder / "red.json").read_bytes() == b'{"result": {"files": []}}'
    import json
    exported = json.loads((folder / "graph.json").read_text(encoding="utf-8"))
    assert len(exported["nodes"]) == 5 and len(exported["edges"]) == 6
    assert json.loads((folder / "summary.json").read_text(encoding="utf-8"))["version"] == 3


def test_persist_version_files_reports_failure_instead_of_raising(tmp_path):
    class Broken:
        kind = "s3"
        def put_tree(self, *a, **k): raise RuntimeError("no credentials")
        def uri(self, k): return k
    rows = kb_graph.rows_from_derived(sample_graph(), kb_version="v", code="T", source="x.json")
    rec = kb_graph.persist_version_files(raw=b"{}", rows=rows, kb_version="v", gear="g", number=1, source_name="x.json", store=Broken())
    assert rec["ok"] is False and rec["store"] == "s3" and "no credentials" in rec["error"]


def test_dtos_read_metadata():
    rows = kb_graph.rows_from_derived(sample_graph(), kb_version="v", code="T", source="x.json")
    import json
    n = rows["nodes"][0]
    dto = kb_graph.node_dto({"id": n[0], "kind": n[2], "label": n[3], "category": n[4], "source_locus": n[5], "metadata": json.loads(n[7])}, full=True)
    assert dto["file"] == "a.asp" and dto["module"] == "m1" and dto["tables"] == ["tblUser"] and dto["includes"] == ["Datastore2.inc"]
    assert dto["purpose"].startswith("Purpose of a.asp")


@pytest.mark.skipif(not KB_URL, reason="RED_GRAPH_TEST_KB_URL not set")
class TestRoundTrip:
    @pytest.fixture(scope="class")
    def published(self):
        stats = kb_graph.publish_derived(sample_graph(), kb_version=kb_graph.library_deps_version(GEAR),
                                         gear_id=kb_graph.library_gear_id(GEAR), code="T", source="test.json",
                                         url=KB_URL, schema=KB_SCHEMA)
        yield stats
        kb_graph.write_kb(url=KB_URL, schema=KB_SCHEMA, kb_version=stats["kb_version"], gear_id=stats["gear_id"],
                          node_rows=[], card_rows=[], edge_rows=[], stats={})
        import psycopg
        with psycopg.connect(KB_URL) as c:
            c.execute(f"DELETE FROM {KB_SCHEMA}.fe_kb_versions WHERE kb_version = %s", (stats["kb_version"],))
            c.commit()

    @pytest.fixture(scope="class")
    def dao(self):
        return kb_graph.KbGraphDao(KB_URL, KB_SCHEMA)

    def test_published_version_is_staging_and_counts_match(self, published, dao):
        v = dao.version(published["kb_version"])
        assert v["status"] == "STAGING" and v["gear_id"] == "_library-test"
        assert v["graph_node_count"] == 5 and v["graph_edge_count"] == 6

    def test_matrix_and_pair(self, published, dao):
        m = dao.module_matrix(published["kb_version"])
        assert {r["module"]: r["files"] for r in m["modules"]} == {"m1": 1, "m2": 2}
        pairs = {(r["source"], r["target"]): r["count"] for r in m["pairs"]}
        assert pairs[("m1", "m2")] == 2 and pairs[("m2", "m2")] == 1
        between = dao.edges_between(published["kb_version"], "m1", "m2")
        assert {r["label"] for r in between} == {"DEPENDS_ON", "TRIGGERS"}

    def test_traverse_and_path(self, published, dao):
        v = published["kb_version"]
        down = dao.traverse(v, "CMP-T-0001", direction="down", max_depth=2)
        assert {n["id"]: n["depth"] for n in down["nodes"]} == {"CMP-T-0001": 0, "CMP-T-0002": 1, "CMP-T-0003": 2}
        up = dao.traverse(v, "CMP-T-0003", direction="up", max_depth=1)
        assert {n["id"] for n in up["nodes"]} == {"CMP-T-0003", "CMP-T-0002"}
        path = dao.shortest_path(v, "CMP-T-0001", "CMP-T-0003")
        assert path["found"] and path["hops"] == 2 and path["direction"] == "down"
        assert not dao.shortest_path(v, "CMP-T-0003", "CMP-T-0001", exclude=["CMP-T-0002"])["found"]

    def test_node_detail_lists_tables_and_edges(self, published, dao):
        v = published["kb_version"]
        out, inc = dao.node_edges(v, "CMP-T-0003")
        assert {r["other_kind"] for r in out} == {"Entity"} and len(out) == 2
        assert [r["other_id"] for r in inc] == ["CMP-T-0002"]
        assert dao.search(v, "c.asp")[0]["id"] == "CMP-T-0003"

    def test_versions_count_up_and_promote_switches_current(self, published, dao):
        prefix = kb_graph.library_deps_prefix(GEAR)
        gear_id = kb_graph.library_deps_gear_id(GEAR)
        assert dao.active_version(prefix) is None                      # published as STAGING, nothing current yet
        assert dao.next_version_number(prefix) == 2
        v2 = kb_graph.library_deps_version(GEAR, 2)
        try:
            kb_graph.publish_derived(sample_graph(), kb_version=v2, gear_id=gear_id, code="T", source="test2.json",
                                     url=KB_URL, schema=KB_SCHEMA)
            assert [r["kb_version"] for r in dao.versions(prefix)] == [published["kb_version"], v2]
            assert dao.latest_version(prefix) == v2 and dao.next_version_number(prefix) == 3
            dao.promote(published["kb_version"], gear_id, prefix)
            assert dao.active_version(prefix) == published["kb_version"]
            dao.promote(v2, gear_id, prefix)                               # roll forward
            assert dao.active_version(prefix) == v2
            assert dao.version(published["kb_version"])["status"] == "SUPERSEDED"
            dao.promote(published["kb_version"], gear_id, prefix)          # roll back
            assert dao.active_version(prefix) == published["kb_version"]
            assert dao.version(v2)["status"] == "SUPERSEDED"
        finally:
            import psycopg
            with psycopg.connect(KB_URL) as c:
                for tbl in ("fe_kb_edges", "fe_kb_cards", "fe_kb_nodes", "fe_kb_versions"):
                    c.execute(f"DELETE FROM {KB_SCHEMA}.{tbl} WHERE kb_version = %s", (v2,))
                c.commit()
