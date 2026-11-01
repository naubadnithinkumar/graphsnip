import os
import shutil
import threading
import time

import pytest

from graphsnip.project import Project


@pytest.fixture()
def proj(repo, tmp_path):
    dst = tmp_path / "copy"
    shutil.copytree(repo, dst, ignore=shutil.ignore_patterns("graphify-out", ".graphsnip"))
    p = Project(dst)
    p.build()
    p.write_manifest()
    return p


def _fresh(p):
    p._last_stale_check = 0.0
    return p.changes()


def test_unchanged_repo_is_not_stale(proj):
    ch = _fresh(proj)
    assert ch == {"added": [], "modified": [], "deleted": []}
    assert proj.is_stale(0) is False


def test_edit_add_and_delete_are_detected(proj):
    f = proj.root / "app" / "validators.py"
    f.write_text(f.read_text() + "\n\ndef validate_phone(p):\n    return p\n")
    (proj.root / "app" / "new_mod.py").write_text("def brand_new():\n    return 1\n")
    (proj.root / "app" / "repo.py").unlink()
    ch = _fresh(proj)
    assert "app/validators.py" in ch["modified"]
    assert "app/new_mod.py" in ch["added"]
    assert "app/repo.py" in ch["deleted"]
    assert proj.is_stale(0) is True


def test_touch_without_content_change_is_not_stale(proj):
    f = proj.root / "app" / "validators.py"
    future = time.time() + 120
    os.utime(f, (future, future))
    assert _fresh(proj) == {"added": [], "modified": [], "deleted": []}
    assert proj.is_stale(0) is False


def test_lock_is_single_flight_and_stale_locks_are_reclaimed(proj):
    assert proj.try_lock() is True
    assert proj.try_lock() is False and proj.refreshing() is True
    proj.unlock()
    assert proj.refreshing() is False
    proj.gs_dir.mkdir(exist_ok=True)
    proj.lock_path.write_text("999999999 %f" % time.time())  # a pid that does not exist
    assert proj.try_lock() is True
    proj.unlock()
    proj.lock_path.write_text("%d %f" % (os.getpid(), time.time() - 4000))  # older than 30 minutes
    assert proj.try_lock() is True
    proj.unlock()


def test_background_refresh_does_not_block_and_picks_up_the_edit(proj, monkeypatch):
    calls = []
    gate = threading.Event()

    def slow_build(self, force=False):
        calls.append(1)
        gate.wait(5)
        self.write_manifest()
        return {}

    monkeypatch.setattr(Project, "build", slow_build)
    f = proj.root / "app" / "validators.py"
    f.write_text(f.read_text() + "\n\ndef validate_zip(z):\n    return z\n")
    proj._last_stale_check = 0.0
    t0 = time.time()
    proj.ensure_graph(True, background=True)
    assert time.time() - t0 < 2  # returned while the build is still blocked
    assert proj.refreshing() is True
    proj.ensure_graph(True, background=True)  # a second request must not start a second build
    gate.set()
    for _ in range(50):
        if not proj.refreshing():
            break
        time.sleep(0.1)
    assert proj.refreshing() is False
    assert len(calls) == 1


def test_second_process_waits_for_first_graph(tmp_path, monkeypatch):
    p = Project(tmp_path)
    (tmp_path / "a.py").write_text("def f():\n    return 1\n")
    assert p.try_lock()
    monkeypatch.setattr(Project, "wait_unlocked", lambda self, timeout=0: True)
    with pytest.raises(Exception):
        p.ensure_graph(True)
    p.unlock()
