"""Unit tests for the RED graph derivation (no database).

    set PYTHONPATH=.
    python -m pytest app/agentic_platform/tests/test_red_graph_derive.py -q
"""
from __future__ import annotations

import json

import pytest

from app.agentic_platform.fe_core.red_graph.derive import (
    REL_CALLS,
    REL_USES,
    derive_from_rows,
    module_of,
    record_from_row,
)

ROOT = r"C:\x\active_files"


def row(name, folder, deps=(), triggers=(), tables=(), **extra):
    r = {
        "file_name": name,
        "file_path": rf"{ROOT}\{folder}\App\{name}",
        "layer": "UI",
        "risk_level": "low",
        "loc": 10,
        "internal_dependencies": list(deps),
        "entry_points": [{"name": "main", "triggers": list(triggers)}],
        "database_tables": list(tables),
        "business_rules": [{"rule_id": "BR_001"}],
        "security_concerns": [{"severity": "high"}, {"severity": "critical"}],
    }
    r.update(extra)
    return r


def test_module_of_handles_both_path_styles():
    assert module_of(rf"{ROOT}\03-business-line\App\a.asp") == "03-business-line"
    assert module_of(r"C:\CTS_Git\Source_Code\banking-asp-demo\login.asp") == "banking-asp-demo"
    assert module_of("a.asp") == "(root)"


def test_record_from_row_accepts_json_strings_like_the_csv_dump():
    r = record_from_row(row("a.asp", "m", internal_dependencies=json.dumps(["b.asp"]),
                            entry_points=json.dumps([{"triggers": ["POST from c.asp"]}])))
    assert r.internal_dependencies == ["b.asp"]
    assert r.triggers == ["POST from c.asp"]
    assert r.business_rule_count == 1
    assert r.security_counts == {"high": 1, "critical": 1}


def test_dependency_becomes_uses_edge_and_trigger_becomes_calls_edge():
    g = derive_from_rows([
        row("a.asp", "m1", deps=["b.asp (navigation)", "Datastore2.inc (provides DBPath)", "stylesRM.css"]),
        row("b.asp", "m1", triggers=["HTTP POST from c.asp form submission"]),
        row("c.asp", "m2"),
    ])
    kinds = {(g.nodes[e.source_key].name, g.nodes[e.target_key].name, e.rel_type) for e in g.edges}
    assert ("a", "b", REL_USES) in kinds
    assert ("c", "b", REL_CALLS) in kinds           # c posts to b  =>  c -> b
    assert len(g.edges) == 2
    a = next(n for n in g.nodes.values() if n.name == "a")
    assert a.includes == ["Datastore2.inc", "stylesRM.css"]   # not analysed pages -> kept as includes
    assert g.includes["Datastore2.inc"] == 1
    assert g.summary()["modules"] == 2


def test_same_module_wins_when_a_file_name_is_ambiguous():
    g = derive_from_rows([
        row("maintain.asp", "m1"),
        row("maintain.asp", "m2"),
        row("caller.asp", "m2", deps=["maintain.asp"]),
    ])
    assert len(g.edges) == 1
    e = g.edges[0]
    assert g.nodes[e.target_key].module == "m2"
    assert e.confidence == pytest.approx(0.9) and not e.ambiguous


def test_ambiguous_name_with_no_module_match_links_all_candidates_flagged():
    g = derive_from_rows([
        row("maintain.asp", "m1"),
        row("maintain.asp", "m2"),
        row("caller.asp", "m3", deps=["maintain.asp"]),
    ])
    assert len(g.edges) == 2
    assert all(e.ambiguous and e.confidence == pytest.approx(0.5) for e in g.edges)


def test_self_references_and_duplicates_collapse():
    g = derive_from_rows([
        row("a.asp", "m", deps=["a.asp", "b.asp", "b.asp (again)"]),
        row("b.asp", "m"),
    ])
    assert len(g.edges) == 1


def test_unresolved_page_references_are_counted_not_edged():
    g = derive_from_rows([row("a.asp", "m", deps=["ghost.asp"])])
    assert g.edges == []
    assert g.unresolved_pages["ghost.asp"] == 1
    assert g.summary()["unresolved_page_refs"] == 1


def test_node_properties_carry_what_the_ui_needs():
    g = derive_from_rows([row("a.asp", "07-security-collateral", tables=["tblUser", "Security"], loc=1793, purpose="Creates a line.")])
    props = next(iter(g.nodes.values())).properties()
    assert props["module"] == "07-security-collateral"
    assert props["database_tables"] == ["Security", "tblUser"]
    assert props["loc"] == 1793 and props["purpose"] == "Creates a line."
