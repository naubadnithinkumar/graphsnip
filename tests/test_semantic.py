import shutil

import pytest

pytest.importorskip("numpy")

from graphsnip import semantic
from graphsnip.engine import Engine
from graphsnip.project import Project

ALIASES = {"mailbox": "email", "legitimate": "validate", "genuine": "validate", "inbox": "email"}


@pytest.fixture()
def proj(repo, tmp_path, monkeypatch):
    dst = tmp_path / "copy"
    shutil.copytree(repo, dst, ignore=shutil.ignore_patterns("graphify-out", ".graphsnip"))
    Project(dst).build()  # a fresh graph rooted here: Graphify's update state is keyed by path
    monkeypatch.setenv("GRAPHSNIP_EMBEDDER", "hash")
    semantic._EMBEDDERS.clear()
    semantic._EMBEDDERS["hash"] = semantic.HashEmbedder(aliases=ALIASES)
    return Project(dst)


def test_index_layout_and_incremental_refresh(proj):
    r1 = semantic.refresh(proj)
    d = semantic.sem_dir(proj)
    assert r1["total"] > 0 and r1["embedded"] == r1["total"]
    assert {p.name for p in d.iterdir()} >= {"vectors.npy", "ids.json", "meta.json", ".gitignore"}
    r2 = semantic.refresh(proj)  # nothing changed -> nothing re-embedded
    assert r2["embedded"] == 0 and r2["reused"] == r2["total"]
    f = proj.root / "app" / "validators.py"
    f.write_text(f.read_text() + "\n\ndef validate_phone(phone):\n    return phone\n")
    r3 = proj.build()["semantic"]  # the graph refresh catches the index up by itself
    assert 0 < r3["embedded"] < r3["total"]  # the new symbol and its neighbours, not everything
    assert r3["total"] == r1["total"] + 1


def test_deleted_symbols_leave_the_index(proj):
    r1 = semantic.refresh(proj)
    (proj.root / "app" / "repo.py").unlink()
    r2 = proj.build()["semantic"]
    assert r2["total"] < r1["total"] and r2["removed"] > 0
    _meta, ids, _h, _m = semantic._load(proj)
    assert all("repo" not in str(proj.graph().nodes[n].get("source_file")) for n in ids)


def test_model_change_rebuilds_everything(proj):
    semantic.refresh(proj)
    other = semantic.HashEmbedder(dim=128, aliases=ALIASES)
    r = semantic.refresh(proj, embedder=other)
    assert r["embedded"] == r["total"] and r["model"] == "hash-128"


def test_semantic_search_finds_symbol_with_no_shared_words(proj):
    semantic.refresh(proj)
    hits = semantic.search(proj, "is this a genuine mailbox", k=5)
    labels = [str(proj.graph().nodes[n].get("label")) for n, _s in hits]
    assert any("validate_email" in lab for lab in labels[:3]), labels


def test_hybrid_pack_beats_keyword_on_a_vague_question(proj):
    q = "is this a genuine mailbox"
    kw = Engine(proj.root, source="test", auto_refresh=False).pack(q)
    assert not kw.ok or "validate_email" not in kw.text  # keyword search alone cannot get there
    semantic.refresh(proj)
    eng = Engine(proj.root, source="test", auto_refresh=False)
    hy = eng.pack(q)
    assert hy.ok and "validate_email" in hy.text and eng.last_retrieval == "hybrid"


def test_hybrid_does_not_hurt_a_keyword_question(proj):
    q = "validate name"
    before = Engine(proj.root, source="test", auto_refresh=False).pack(q)
    semantic.refresh(proj)
    after = Engine(proj.root, source="test", auto_refresh=False).pack(q)
    assert "validate_name" in before.text and "validate_name" in after.text


def test_find_uses_semantic_expansion(proj):
    semantic.refresh(proj)
    eng = Engine(proj.root, source="test", auto_refresh=False)
    r = eng.find("is this a genuine mailbox")
    assert r.ok and "validate_email" in r.text and eng.last_retrieval == "hybrid"


def test_no_index_means_plain_keyword_behaviour(proj):
    assert semantic.search(proj, "anything") == []
    eng = Engine(proj.root, source="test", auto_refresh=False)
    eng.pack("validate name")
    assert eng.last_retrieval == "keyword"


def test_graph_refresh_keeps_an_existing_index_current(proj):
    semantic.refresh(proj)
    f = proj.root / "app" / "validators.py"
    f.write_text(f.read_text() + "\n\ndef validate_zip(z):\n    return z\n")
    proj.build()  # _post_build() catches the index up
    assert semantic.status(proj)["pending"] == 0


def test_fusion_prefers_items_both_lists_agree_on():
    fused = [n for n, _ in semantic.rrf(["a", "b", "c"], ["c", "d", "a"])]
    assert fused[0] == "a" and set(fused) == {"a", "b", "c", "d"}


def test_eval_reports_gain_and_does_not_touch_savings(proj, tmp_path):
    import json

    from graphsnip import retrieval

    semantic.refresh(proj)
    tasks = tmp_path / "t.json"
    tasks.write_text(json.dumps({"tasks": [
        {"id": "v1", "question": "is this a genuine mailbox", "expect": ["validate_email"]},
        {"id": "k1", "question": "validate name", "expect": ["validate_name"]}]}))
    res = retrieval.run_eval(str(proj.root), str(tasks))
    t = res["totals"]
    assert t["n"] == 2 and t["gained"] == 1 and t["lost"] == 0 and t["hybrid_hits"] == 2 and t["keyword_hits"] == 1
    assert "MISSED" in retrieval.format_table(res)
    from graphsnip import ledger

    recs = ledger.Ledger(proj.gs_dir).read()
    assert recs and all(r.get("source") == "bench" for r in recs)  # eval calls stay out of the savings totals


def test_dashboard_shows_retrieval_card(proj, tmp_path):
    import json

    from graphsnip import dashboard, retrieval

    semantic.refresh(proj)
    tasks = tmp_path / "t.json"
    tasks.write_text(json.dumps({"tasks": [{"id": "v1", "question": "is this a genuine mailbox", "expect": ["validate_email"]}]}))
    retrieval.run_eval(str(proj.root), str(tasks))
    html = dashboard.build_html(proj, "x.html")
    assert "Semantic search: keyword vs hybrid" in html and "found 0/1 → 1/1" in html
