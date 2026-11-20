"""Long-term memory and document indexing/extraction."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from ray.knowledge.extract import chunk_text, extract, summarise_locally
from ray.knowledge.indexer import FileIndexer, IndexSearch, safe_fts_query


def test_remember_and_recall(app):
    app.memory.remember("Project", "RAY is a personal AI operating system", category="projects")
    hits = app.memory.recall("operating system")
    assert hits and hits[0]["title"] == "Project"
    assert app.memory.stats()["total"] == 1


def test_memory_refuses_credentials(app):
    with pytest.raises(ValueError):
        app.memory.remember("key", "xai-abcdefghijklmnopqrstuvwxyz123456")
    with pytest.raises(ValueError):
        app.memory.remember("note", "password: hunter2")


def test_memory_update_forget_clear(app):
    memory = app.memory.remember("Preference", "Short answers", category="preferences")
    updated = app.memory.update(memory["id"], content="Very short answers")
    assert "Very short" in updated["content"]
    assert app.memory.forget(memory["id"]) is True
    app.memory.remember("A", "one")
    app.memory.remember("B", "two")
    assert app.memory.forget_all() == 2
    assert app.memory.stats()["total"] == 0


def test_memory_export_import(app, tmp_path):
    app.memory.remember("Export me", "some content")
    destination = app.memory.export_json(tmp_path / "memories.json")
    assert destination.exists()
    app.memory.forget_all()
    result = app.memory.import_json(destination)
    assert result["imported"] == 1
    assert app.memory.stats()["total"] == 1


def test_memory_prune(app):
    memory = app.memory.remember("Old news", "stale")
    app.db.execute("UPDATE memories SET created_at = '2020-01-01T00:00:00Z' WHERE id = ?", (memory["id"],))
    assert app.memory.prune(retention_days=30) == 1


def test_memory_context_block(app):
    app.memory.remember("Name", "The user is Revanth", category="identity")
    block = app.memory.context_block()
    assert "Revanth" in block


def test_memory_can_be_disabled(app):
    app.config.set("memory.enabled", False)
    with pytest.raises(RuntimeError):
        app.memory.remember("Nope", "disabled")
    app.config.set("memory.enabled", True)


def _write_docx(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")


def _write_pptx(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("ppt/_rels/presentation.xml.rels", "<Relationships/>")
        archive.writestr("ppt/slides/slide1.xml", f"<p:sld><a:p><a:r><a:t>{text}</a:t></a:r></a:p></p:sld>")


def _write_xlsx(path: Path, text: str) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("xl/workbook.xml", "<workbook><sheets><sheet name='Data'/></sheets></workbook>")
        archive.writestr("xl/sharedStrings.xml", f"<sst><si><t>{text}</t></si></sst>")
        archive.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData><row><c t='s'><v>0</v></c></row></sheetData></worksheet>")


def test_extract_text_and_markdown(tmp_path):
    plain = tmp_path / "notes.txt"
    plain.write_text("Borrowing powers of a company.", encoding="utf-8")
    result = extract(plain)
    assert "Borrowing powers" in result["text"]
    assert result["format"] == "txt"

    markdown = tmp_path / "readme.md"
    markdown.write_text("# Title\n\nBody text", encoding="utf-8")
    assert "Body text" in extract(markdown)["text"]


def test_extract_csv(tmp_path):
    csv_file = tmp_path / "contacts.csv"
    csv_file.write_text("name,phone\nReva,12345\n", encoding="utf-8")
    text = extract(csv_file)["text"]
    assert "Reva" in text and "phone" in text


def test_extract_docx_pptx_xlsx(tmp_path):
    docx = tmp_path / "contract.docx"
    _write_docx(docx, "Indemnity clause applies.")
    assert "Indemnity" in extract(docx)["text"]

    pptx = tmp_path / "deck.pptx"
    _write_pptx(pptx, "Quarterly review")
    assert "Quarterly" in extract(pptx)["text"]

    xlsx = tmp_path / "sheet.xlsx"
    _write_xlsx(xlsx, "Invoice total")
    assert "Invoice total" in extract(xlsx)["text"]


def test_image_metadata_is_honest(tmp_path):
    image = tmp_path / "photo.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    result = extract(image)
    assert result["supported"] is False        # no fake text for unreadable PNGs
    assert (result.get("metadata") or {})["ext"] == ".png"


def test_extract_missing_file():
    with pytest.raises(FileNotFoundError):
        extract("/definitely/not/here.txt")


def test_extract_marks_unsupported_binary(tmp_path):
    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02")
    result = extract(binary)
    assert result["supported"] is False
    assert result["text"] == ""


def test_summarise_locally_is_extractive():
    text = (
        "The company may borrow money. Section 180 requires a special resolution. "
        "Directors must disclose interests. The auditor reports to members. "
        "Borrowing limits are set by the board. This is another sentence. And more."
    )
    summary = summarise_locally(text, max_sentences=2)
    assert summary.count(".") <= 2
    assert summary.split(".")[0].strip() in text


def test_chunk_text():
    text = "a" * 250
    chunks = chunk_text(text, size=100, overlap=10)
    assert len(chunks) > 1
    assert all(len(chunk) <= 110 for chunk in chunks)


def test_fts_query_escaping():
    assert safe_fts_query("") == ""
    assert safe_fts_query('!!!') == ""
    assert safe_fts_query("borrow") == '"borrow"*'
    sanitised = safe_fts_query('drop table; -- "quoted"')
    assert sanitised.count('"') % 2 == 0
    assert "drop" in sanitised and "quoted" in sanitised
    assert safe_fts_query("a" * 3 + " " + "b" * 3) == '"aaa"* "bbb"*'



def test_index_and_search(tmp_path, app):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "companies-act.txt").write_text(
        "Section 180 covers the borrowing powers of a company and requires approval.", encoding="utf-8"
    )
    (root / "recipe.md").write_text("Mix flour and water.", encoding="utf-8")

    indexer = app.indexer
    indexer.add_root(root)
    result = indexer.index()
    assert result["ok"] is True
    assert result["indexed"] >= 2
    assert indexer.stats()["files"] >= 2

    hits = IndexSearch(app.db).search("borrowing")
    assert hits["count"] >= 1
    assert hits["results"][0]["name"] == "companies-act.txt"
    assert "borrowing" in (hits["results"][0]["snippet"] or "").lower()
    assert IndexSearch(app.db).search("zzz-not-present")["count"] == 0


def test_index_respects_excludes_and_cancel(tmp_path, app):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "keep.txt").write_text("keep me", encoding="utf-8")
    (root / "skip.tmp").write_text("temp", encoding="utf-8")
    indexer = app.indexer
    indexer.add_root(root)
    indexer.index()
    names = {row["name"] for row in app.db.query("SELECT name FROM files")}
    assert "keep.txt" in names
    assert "skip.tmp" not in names

    indexer.cancel()
    assert indexer.running is False


def test_index_roots_management(tmp_path, app):
    indexer = app.indexer
    root = tmp_path / "watched"
    root.mkdir()
    added = indexer.add_root(root)
    assert added["path"]
    assert any(item["path"] == added["path"] for item in indexer.roots())
    assert indexer.remove_root(added["path"]) is True
