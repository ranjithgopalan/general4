"""Library cards per ASP page, derived from the uploaded module JSON without a build.

    set PYTHONPATH=.
    python -m pytest app/agentic_platform/tests/test_library_cards.py -q
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from app.agentic_platform.fe_core.red_graph import library_cards
from app.agentic_platform.fe_core.red_graph.kb_graph import submodule_of

SAMPLE = Path(__file__).resolve().parents[3] / "doc" / "RED-Company-Search.json"


class _Settings:
    def __init__(self, root: Path):
        self.root = root

    def corpus_root_for(self, app_id: str) -> Path:
        return self.root / app_id / "corpus"

    def kb_root_for(self, app_id: str) -> Path:
        return self.root / app_id / "kb"


@pytest.fixture
def settings(tmp_path):
    corpus = tmp_path / "_library" / "corpus" / "re-cards"
    corpus.mkdir(parents=True)
    shutil.copy(SAMPLE, corpus / "uwcr-credit-company-administration.json")
    library_cards._cache.clear()
    return _Settings(tmp_path)


@pytest.mark.skipif(not SAMPLE.is_file(), reason="sample RED JSON not present")
def test_cards_come_from_uploaded_module_json_when_nothing_is_built(settings):
    cards = library_cards.cards_for_page(settings, None, "CreditRiskDBMenu.asp")
    assert cards is not None and cards["found"] is True and cards["source"] == "corpus"
    assert cards["module"] == "UWCR - Credit Company Administration"
    assert cards["submodule"] == "AdminMenuRenderer" and cards["domain"] == "Credit Risk Administration"
    assert "BR-UWCR-001" in [c["id"] for c in cards["cards"]["BR"]]
    assert cards["counts"]["BR"] >= 2 and cards["counts"].get("FR", 0) >= 2
    assert all(c["label"] for kind in cards["cards"].values() for c in kind)
    # case and folder are ignored when matching the page
    assert library_cards.cards_for_page(settings, None, r"C:\x\CREDITRISKDBMENU.ASP")["found"] is True


@pytest.mark.skipif(not SAMPLE.is_file(), reason="sample RED JSON not present")
def test_unknown_page_is_reported_not_errored(settings):
    cards = library_cards.cards_for_page(settings, None, "nothing-here.asp")
    assert cards is not None and cards["found"] is False and cards["cards"] == {}


def test_no_library_inputs_gives_none(tmp_path):
    library_cards._cache.clear()
    assert library_cards.cards_for_page(_Settings(tmp_path), None, "a.asp") is None


@pytest.mark.skipif(not SAMPLE.is_file(), reason="sample RED JSON not present")
def test_summary_has_counts_only(settings):
    s = library_cards.cards_summary(settings, None, "CreditRiskDBMenu.asp")
    assert s["found"] and isinstance(s["counts"], dict) and isinstance(s["shared_entities"], int)
    assert "cards" not in s


KB_URL = __import__("os").environ.get("RED_GRAPH_TEST_KB_URL")
KB_SCHEMA = __import__("os").environ.get("RED_GRAPH_TEST_KB_SCHEMA", "form_rationalization_anh")


@pytest.mark.skipif(not KB_URL, reason="RED_GRAPH_TEST_KB_URL not set")
def test_cards_prefer_the_kb_tables_when_a_card_build_is_published(tmp_path):
    """A published card build (fe_kb_cards rows with source_locus = page) wins over files on disk."""
    import psycopg

    from app.agentic_platform.fe_core.red_graph.kb_graph import KbGraphDao

    kb_version = "_library-re-v999"
    dao = KbGraphDao(KB_URL, KB_SCHEMA)
    with psycopg.connect(KB_URL) as c:
        c.execute(f"DELETE FROM {KB_SCHEMA}.fe_kb_versions WHERE kb_version = %s", (kb_version,))
        c.execute(f"INSERT INTO {KB_SCHEMA}.fe_kb_versions (kb_version, gear_id, status) VALUES (%s, '_library', 'STAGING')", (kb_version,))
        rows = [("BR-T-001", "BR", "Rule one", "Credit Admin", "MenuPage.asp", "Rule one text", '{"business_domain": "Credit Admin", "module_key": "mod-a"}'),
                ("SCR-T-001", "SCR", "Menu screen", "Credit Admin", "MenuPage.asp", "Screen text", "{}"),
                ("ENT-T-001", "ENT", "tblUser", "Credit Admin", "ENT-T-001", "Table tblUser", "{}")]
        for r in rows:
            c.execute(f"INSERT INTO {KB_SCHEMA}.fe_kb_cards (id, kb_version, kind, label, category, source_locus, text_en, metadata) "
                      f"VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)", (r[0], kb_version, r[1], r[2], r[3], r[4], r[5], r[6]))
        c.execute(f"INSERT INTO {KB_SCHEMA}.fe_kb_edges (kb_version, from_id, to_id, label, tag) VALUES (%s, 'BR-T-001', 'ENT-T-001', 'REFERENCES', 'stated')", (kb_version,))
        c.commit()
    try:
        library_cards._cache.clear()
        cards = library_cards.cards_for_page(_Settings(tmp_path), None, "menupage.asp", dao)
        assert cards["source"] == "db" and cards["version"] == kb_version and cards["found"]
        assert [c["id"] for c in cards["cards"]["BR"]] == ["BR-T-001"] and cards["counts"] == {"BR": 1, "SCR": 1}
        assert cards["domain"] == "Credit Admin" and cards["module_key"] == "mod-a"
        assert [s["id"] for s in cards["shared_entities"]] == ["ENT-T-001"]
        assert library_cards.cards_for_page(_Settings(tmp_path), None, "other.asp", dao)["found"] is False
    finally:
        library_cards._cache.clear()
        with psycopg.connect(KB_URL) as c:
            for tbl in ("fe_kb_edges", "fe_kb_cards", "fe_kb_versions"):
                c.execute(f"DELETE FROM {KB_SCHEMA}.{tbl} WHERE kb_version = %s", (kb_version,))
            c.commit()


def test_submodule_is_the_folder_between_module_and_file():
    assert submodule_of(r"C:\x\active_files\03-business-line\App\a.asp", "03-business-line") == "App"
    assert submodule_of(r"C:\x\active_files\15-worldsource\WorldSource\Sub\a.asp", "15-worldsource") == "WorldSource/Sub"
    assert submodule_of(r"C:\x\03-business-line\a.asp", "03-business-line") == ""
    assert submodule_of(r"C:\x\other\a.asp", "03-business-line") == ""
