"""Integration test for the RED graph against a real lmod Postgres.

Skipped unless RED_GRAPH_TEST_DB_URL is set, because it writes graph_edges rows (for the
extractor 'red_file_analyses' only) for the project named in RED_GRAPH_TEST_PROJECT
(default proj_0001, the banking demo that ships in the local lmod container).

    set PYTHONPATH=.
    set RED_GRAPH_TEST_DB_URL=postgresql://postgres:admin@localhost:5435/lmod
    python -m pytest app/agentic_platform/tests/test_red_graph_db.py -q
"""
from __future__ import annotations

import os

import pytest

URL = os.environ.get("RED_GRAPH_TEST_DB_URL")
PROJECT = os.environ.get("RED_GRAPH_TEST_PROJECT", "proj_0001")
pytestmark = pytest.mark.skipif(not URL, reason="RED_GRAPH_TEST_DB_URL not set")


@pytest.fixture(scope="module")
def dao():
    from app.agentic_platform.fe_core.red_graph.dao import RedGraphDao

    return RedGraphDao(URL, os.environ.get("RED_GRAPH_TEST_DB_SCHEMA", "lmod"))


@pytest.fixture(scope="module")
def built(dao):
    from app.agentic_platform.fe_core.red_graph.build import build

    return build(PROJECT, dao=dao)


def test_build_writes_pages_and_edges(dao, built):
    assert built["nodes"] >= 1
    nodes = dao.nodes(PROJECT)
    assert {n["label"] for n in nodes} == {"ASPPage"}
    assert len(nodes) == built["nodes"]
    edges = dao.edges(PROJECT)
    assert len(edges) == sum(built["by_rel_type"].values())
    assert all(e["extractor_id"] == "red_file_analyses" for e in edges)


def test_build_is_idempotent(dao, built):
    from app.agentic_platform.fe_core.red_graph.build import build

    again = build(PROJECT, dao=dao)
    assert again["nodes"] == built["nodes"] and again["edges"] == built["edges"]
    assert len(dao.edges(PROJECT)) == len(dao.edges(PROJECT))


def test_traverse_follows_edges_from_the_table(dao, built):
    edges = dao.edges(PROJECT)
    if not edges:
        pytest.skip("project has no page-to-page edges")
    e = edges[0]
    down = dao.traverse(PROJECT, e["source_id"], direction="down", max_depth=1)
    assert e["target_id"] in {n["id"] for n in down["nodes"]}
    assert next(n for n in down["nodes"] if n["id"] == e["target_id"])["depth"] == 1
    up = dao.traverse(PROJECT, e["target_id"], direction="up", max_depth=1)
    assert e["source_id"] in {n["id"] for n in up["nodes"]}


def test_shortest_path_between_directly_linked_pages(dao, built):
    edges = dao.edges(PROJECT)
    if not edges:
        pytest.skip("project has no page-to-page edges")
    e = edges[0]
    res = dao.shortest_path(PROJECT, e["source_id"], e["target_id"])
    assert res["found"] and res["hops"] == 1 and res["direction"] == "down"


def test_module_matrix_matches_edge_count(dao, built):
    m = dao.module_matrix(PROJECT)
    assert sum(r["files"] for r in m["modules"]) == built["nodes"]
    assert sum(r["count"] for r in m["pairs"]) == len(dao.edges(PROJECT))
