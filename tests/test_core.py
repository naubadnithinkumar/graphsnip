import json

from graphsnip import ledger as L
from graphsnip.baseline import query_terms
from graphsnip.engine import Engine
from graphsnip.shellfilter import compress
from graphsnip.slicer import shorten_docstring, slice_symbol
from graphsnip.tokens import count_tokens


# ------------------------------------------------------------------ slicer
def test_slice_python_function_exact():
    src = "import os\n\n\ndef a():\n    return 1\n\n\ndef b(x):\n    if x:\n        return 2\n    return 3\n\n\nZ = 1\n"
    sl = slice_symbol(src, ".py", 8)
    assert sl.exact
    assert sl.text.splitlines()[0] == "def b(x):"
    assert sl.text.rstrip().endswith("return 3")
    assert "Z = 1" not in sl.text


def test_slice_includes_decorator():
    src = "@decorator\n@other(1)\ndef f():\n    pass\n\nx = 1\n"
    sl = slice_symbol(src, ".py", 3)
    assert sl.text.startswith("@decorator")


def test_slice_js_braces_and_truncation():
    src = "function a() {\n  if (x) {\n    y();\n  }\n}\n\nfunction b() {}\n"
    sl = slice_symbol(src, ".js", 1)
    assert sl.end == 5
    big = "def f():\n" + "\n".join(f"    x{i} = {i}" for i in range(300)) + "\n"
    sl = slice_symbol(big, ".py", 1, max_lines=50)
    assert sl.truncated and sl.total_lines == 301 and len(sl.text.splitlines()) == 50


def test_shorten_docstring_keeps_code_and_short_docs():
    long = 'def f():\n    """Summary line.\n\n' + "\n".join(f"    detail {i}" for i in range(20)) + '\n    """\n    return 1\n'
    out, omitted = shorten_docstring(long, ".py")
    assert omitted > 10 and "return 1" in out and out.count('"""') == 2 and "detail 19" not in out
    short = 'def g():\n    """One liner."""\n    return 2\n'
    assert shorten_docstring(short, ".py") == (short, 0)
    assert shorten_docstring(long, ".js")[1] == 0


# ------------------------------------------------------------------ misc units
def test_query_terms_splits_identifiers_and_drops_stopwords():
    t = query_terms("how does the createUser flow handle_errors")
    assert "createuser" in t and "create" in t and "user" in t and "handle" in t and "how" not in t


def test_ledger_summary(tmp_path):
    led = L.Ledger(tmp_path)
    led.record(kind="call", tool="symbol", returned=100, baseline=1000, source="agent")
    led.record(kind="call", tool="pack", returned=300, baseline=2000, source="agent", deduped=True)
    led.record(kind="call", tool="pack", returned=50, baseline=60, source="bench")
    led.record(kind="hook", tool="Read", action="nudge", source="hook")
    s = L.summarize(led.read(), 3.0, {"agent"})
    assert (s["calls"], s["returned"], s["baseline"], s["saved"]) == (2, 400, 3000, 2600)
    assert s["pct"] == 86.7 and s["deduped"] == 1 and s["by_tool"]["pack"]["calls"] == 1
    assert L.summarize(led.read(), 3.0)["hooks"] == {"Read:nudge": 1}
    led.clear("bench")
    assert all(r.get("source") != "bench" for r in led.read())


def test_shell_compress_keeps_failures_and_shrinks():
    raw = "\n".join(f"collecting item {i} ... ok" for i in range(500)) + "\nFAILED test_x - AssertionError\n2 failed, 498 passed\n"
    out = compress(raw)
    assert "FAILED test_x" in out and "2 failed, 498 passed" in out
    assert count_tokens(out) < count_tokens(raw) / 5


# ------------------------------------------------------------------ engine
def test_symbol_returns_exact_code_and_neighbors(repo):
    e = Engine(repo, source="cli")
    r = e.symbol("create_user")
    assert r.ok
    assert "def create_user" in r.text and "def delete_user" not in r.text.split("calls/uses")[0]
    assert "validate_name" in r.text  # callee listed
    assert r.baseline > r.returned


def test_symbol_dedupes_within_a_session(repo):
    e = Engine(repo, source="cli")
    first = e.symbol("create_user")
    second = e.symbol("create_user")
    assert first.ok and not first.deduped
    assert second.deduped and second.returned < first.returned * 0.6


def test_symbol_unknown_is_not_ok_and_not_recorded(repo):
    e = Engine(repo, source="cli")
    before = len(e.ledger.read())
    r = e.symbol("definitely_not_here_zz")
    assert not r.ok and len(e.ledger.read()) == before


def test_docstring_is_shortened_but_full_on_request(repo):
    e = Engine(repo, source="cli")
    short = e.symbol("validate_name").text
    full = e.symbol("validate_name", docs="full").text
    assert "docstring shortened" in short and "no control characters" not in short
    assert "no control characters" in full  # asking for the full docstring after the short one re-sends it


def test_outline_lists_symbols(repo):
    r = Engine(repo, source="cli").outline("app/services.py")
    assert r.ok and "UserService" in r.text and "make_service" in r.text


def test_impact_finds_callers(repo):
    r = Engine(repo, source="cli").impact("validate_name")
    assert r.ok and ("create_user" in r.text or "delete_user" in r.text)


def test_find_and_pack(repo):
    e = Engine(repo, source="cli")
    f = e.find("how are users created and validated")
    assert f.ok and "gs_symbol" in f.text
    p = e.pack("how are users created and validated", budget=2500)
    assert p.ok and "```" in p.text and "create_user" in p.text
    assert p.returned <= 2500 * 1.25


def test_calls_are_written_to_the_ledger(repo):
    e = Engine(repo, source="agent")
    e.symbol("get_user")
    rows = [r for r in e.ledger.read() if r.get("source") == "agent" and r.get("tool") == "symbol"]
    assert rows and rows[-1]["returned"] > 0 and rows[-1]["baseline"] > 0


def test_bench_runs_and_checks_quality(repo):
    from graphsnip.bench import run_bench

    tasks = repo / "tasks.json"
    tasks.write_text(json.dumps([
        {"type": "understand", "question": "how does create_user validate input", "expect": ["create_user"]},
        {"type": "impact", "question": "what breaks if I change validate_name", "expect": ["validate_name"]},
    ]))
    res = run_bench(str(repo), str(tasks))
    t = res["totals"]
    assert t["tasks"] == 2 and t["answered"] == 2
    assert all(r["graphsnip_has_code"] for r in res["tasks"])
    assert (repo / ".graphsnip" / "bench.json").exists()
