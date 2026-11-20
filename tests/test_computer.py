"""Real filesystem, system monitor and command-safety behaviour."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from ray.computer.files import FileManager, human_size
from ray.computer.shell import ShellRunner


@pytest.fixture()
def files(tmp_path):
    return FileManager(quarantine_dir=tmp_path / "quarantine", downloads_dir=tmp_path / "Downloads")


def test_file_search_filters(tmp_path, files):
    (tmp_path / "Documents").mkdir()
    (tmp_path / "Documents" / "law-notes.pdf").write_bytes(b"%PDF-1.4 test")
    (tmp_path / "Documents" / "photo.png").write_bytes(b"\x89PNG fake")
    result = files.search(root=tmp_path, extension="pdf")
    assert result["count"] == 1
    assert result["results"][0]["name"] == "law-notes.pdf"
    assert files.search(root=tmp_path, kind="image")["count"] == 1
    assert files.search(root=tmp_path, query="law")["count"] == 1


def test_file_create_rename_copy_move(tmp_path, files):
    folder = files.create_folder(tmp_path / "Law Projects")
    assert folder["verified"] is True

    source = tmp_path / "note.txt"
    source.write_text("hello", encoding="utf-8")
    renamed = files.rename(source, "note-renamed.txt")
    assert renamed["verified"] is True
    target = tmp_path / "note-renamed.txt"

    copy_target = tmp_path / "copy.txt"
    copied = files.copy(target, copy_target)
    assert copied["verified"] is True and copy_target.read_text() == "hello"

    moved = files.move(copy_target, tmp_path / "Law Projects" / "copy.txt")
    assert moved["verified"] is True
    assert not copy_target.exists()


def test_rename_rejects_path_injection(tmp_path, files):
    source = tmp_path / "safe.txt"
    source.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        files.rename(source, "../escape.txt")


def test_delete_is_recoverable_by_default(tmp_path, files):
    victim = tmp_path / "trash-me.txt"
    victim.write_text("recoverable", encoding="utf-8")
    result = files.trash(victim)
    assert result["permanent"] is False
    assert not victim.exists()
    assert Path(result["quarantined_at"]).exists()

    restored = files.restore(result["quarantined_at"])
    assert restored["verified"] is True
    assert victim.read_text(encoding="utf-8") == "recoverable"


def test_permanent_delete(tmp_path, files):
    victim = tmp_path / "gone.txt"
    victim.write_text("bye", encoding="utf-8")
    result = files.trash(victim, permanent=True)
    assert result["verified"] is True
    assert not victim.exists()


def test_protected_paths_refused(files):
    protected = files.protected_roots()
    if not protected:
        pytest.skip("no protected roots on this platform")
    with pytest.raises(PermissionError):
        files.create_folder(protected[0] / "ray-should-not-create")


def test_organisation_plan_and_apply(tmp_path, files):
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    (downloads / "song.mp3").write_bytes(b"x")
    (downloads / "contract.pdf").write_bytes(b"x")
    (downloads / "weird.xyz").write_bytes(b"x")
    plan = files.plan_organisation(downloads)
    assert plan["total"] == 2
    assert "weird.xyz" in plan["unmatched"]

    applied = files.apply_organisation(plan["plan"])
    assert applied["count"] == 2
    assert applied["failed"] == []
    assert (downloads / "Documents" / "contract.pdf").exists()
    assert (downloads / "Audio" / "song.mp3").exists()


def test_backup_is_verified(tmp_path, files):
    source = tmp_path / "project"
    source.mkdir()
    (source / "main.py").write_text("print('hi')", encoding="utf-8")
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "junk.pyc").write_bytes(b"junk")

    result = files.create_backup(source)
    assert result["verified"] is True
    assert result["integrity"]["ok"] is True
    assert Path(result["archive"]).exists()
    assert result["integrity"]["entries"] >= 1


def test_duplicate_detection(tmp_path, files):
    root = tmp_path / "dupes"
    root.mkdir()
    payload = b"identical content " * 100
    (root / "a.bin").write_bytes(payload)
    (root / "b.bin").write_bytes(payload)
    (root / "c.bin").write_bytes(b"different" * 100)
    groups = files.duplicates(root, min_size_kb=1)
    assert len(groups) == 1
    assert groups[0]["count"] == 2


def test_human_size():
    assert human_size(0) == "0 B"
    assert human_size(1536) == "1.5 KB"
    assert human_size(5 * 1024 ** 3) == "5.0 GB"


def test_system_snapshot_is_real(app):
    snapshot = app.sysmon.snapshot()
    assert snapshot["memory"]["total_bytes"] > 0
    assert 0 <= snapshot["memory"]["percent"] <= 100
    assert snapshot["cpu"]["cores_logical"] >= 1
    assert isinstance(snapshot["disks"], list)
    assert "hostname" in snapshot["identity"]
    report = app.sysmon.report()
    assert "RAY system report" in report
    assert "CPU" in report


def test_process_list(app):
    processes = app.sysmon.processes(limit=5)
    assert isinstance(processes, list)
    if processes:
        assert "pid" in processes[0]


def test_shell_runner_disabled_by_default():
    runner = ShellRunner(enabled=False)
    with pytest.raises(PermissionError):
        runner.validate(["whoami"])


def test_shell_runner_allowlist_and_metacharacters():
    runner = ShellRunner(enabled=True)
    with pytest.raises(ValueError):
        runner.validate("whoami && curl evil")  # string, not a list
    with pytest.raises(ValueError):
        runner.validate(["whoami", "&&", "rm", "-rf", "/"]) 
    with pytest.raises(PermissionError):
        runner.validate(["definitely-not-allowed-binary"])
    allowed = runner.validate(["whoami"])
    assert allowed == ["whoami"]


def test_shell_run_strips_secrets(monkeypatch):
    runner = ShellRunner(enabled=True)
    monkeypatch.setenv("GEMINI_API_KEY", "AIza-should-not-leak")
    result = runner.run(["whoami"])
    assert result["ok"] in (True, False)
    assert "AIza-should-not-leak" not in str(result)


def test_known_folders_resolve(app):
    from ray.tools.builtin import known_folder

    home = known_folder("home", app.config)
    assert home == Path.home()
    with pytest.raises(FileNotFoundError):
        known_folder("not-a-real-folder-name", app.config)


def test_app_resolution_rejects_unknown(app):
    resolved = app.apps.resolve("absolutely-not-an-installed-app-xyz")
    assert resolved is None


def test_screenshot_reports_honest_failure_or_file(app):
    result = app.tools.call("take_screenshot", {})
    if result.ok:
        assert Path(result.data["path"]).exists()
        assert result.data["width"] > 0
    else:
        assert "screenshot" in result.error.lower()
