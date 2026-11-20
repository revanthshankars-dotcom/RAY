"""Text extraction for the formats RAY supports locally.

Everything here runs on this machine, with no network calls:

    PDF  (pypdf)   DOCX/XLSX/PPTX (zip + XML, no Office needed)
    TXT/MD/CSV/JSON/log   images (metadata)   audio/video (metadata)

Formats that genuinely need more (legacy .doc, .xls) return a clear explanation
instead of garbage text.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree
from xml.sax.saxutils import unescape

from ..core.logging_setup import get_logger

log = get_logger("knowledge.extract")

MAX_PDF_PAGES = 300
MAX_CHARS = 400_000
TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".log", ".ini", ".cfg", ".json", ".yaml", ".yml",
    ".csv", ".tsv", ".py", ".js", ".ts", ".html", ".css", ".xml", ".ps1", ".sh",
}
SUPPORTED = TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx", ".pptx", ".doc", ".xls"}

def extract(path: str | Path, max_chars: int = MAX_CHARS) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"{target} does not exist")
    suffix = target.suffix.lower()
    result: dict[str, Any] = {
        "path": str(target),
        "name": target.name,
        "ext": suffix,
        "size_bytes": target.stat().st_size,
        "text": "",
        "chars": 0,
        "pages": None,
        "format": suffix.lstrip("."),
        "supported": True,
        "note": "",
    }

    if suffix == ".pdf":
        text, pages, note = _extract_pdf(target, max_chars)
        result.update(text=text, pages=pages, note=note)
    elif suffix == ".docx":
        result["text"] = _extract_docx(target, max_chars)
    elif suffix == ".pptx":
        result["text"] = _extract_pptx(target, max_chars)
    elif suffix == ".xlsx":
        result["text"] = _extract_xlsx(target, max_chars)
    elif suffix in TEXT_EXTENSIONS:
        result["text"] = _read_text_file(target, max_chars)
    elif suffix in (".doc", ".xls"):
        result.update(
            supported=False,
            note="Legacy binary Office files are not readable locally. "
                 "Save as .docx/.xlsx (or export to PDF) and RAY can read it.",
        )
    else:
        meta = metadata_for(target)
        result.update(supported=False, note="No text extraction for this file type.", metadata=meta)

    result["chars"] = len(result["text"])
    result["text"] = result["text"][:max_chars]
    if not result.get("metadata"):
        result["metadata"] = metadata_for(target)
    return result


# ----------------------------------------------------------------------- PDF
def _extract_pdf(path: Path, max_chars: int) -> tuple[str, int | None, str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return "", None, "pypdf is not installed; PDF text extraction is unavailable (pip install pypdf)"
    try:
        reader = PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:
                return "", None, "this PDF is password protected"
        pages = min(len(reader.pages), MAX_PDF_PAGES)
        chunks: list[str] = []
        total = 0
        for index in range(pages):
            try:
                text = reader.pages[index].extract_text() or ""
            except Exception as exc:  # a broken page should not kill the document
                log.warning("page %s of %s failed: %s", index + 1, path.name, exc)
                continue
            chunks.append(text)
            total += len(text)
            if total >= max_chars:
                break
        text = "\n\n".join(chunk.strip() for chunk in chunks if chunk.strip())
        note = ""
        if len(reader.pages) > pages:
            note = f"only the first {pages} of {len(reader.pages)} pages were read"
        if not text:
            note = "no embedded text found - this looks like a scanned PDF (OCR is not included)"
        return text, len(reader.pages), note
    except Exception as exc:
        return "", None, f"could not read PDF: {exc}"


# ---------------------------------------------------------------------- DOCX
_XML_RUN_RE = re.compile(
    r"<(?P<tag>(?:\w+:)?(?:t|instrText))(?:\s[^>]*)?>(?P<body>.*?)</(?P=tag)>|</(?:\w+:)?p>",
    re.S,
)


def _xml_text_fallback(xml_bytes: bytes) -> str:
    """Bare regex reader for OOXML fragments with undeclared prefixes.

    Real Office files always declare their namespaces, but documents produced by
    lightweight generators sometimes carry bare ``w:`` prefixes which
    ElementTree refuses to parse. Falling back keeps those documents readable
    instead of silently returning an empty string.
    """
    raw = xml_bytes.decode("utf-8", "replace")
    chunks: list[str] = []
    for match in _XML_RUN_RE.finditer(raw):
        body = match.group("body")
        if body is None:            # paragraph close
            chunks.append("\n")
        else:
            chunks.append(unescape(body))
    return "".join(chunks)


def _xml_text(xml_bytes: bytes) -> str:
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError:
        return _xml_text_fallback(xml_bytes)
    parts: list[str] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1]
        if tag in ("t", "instrText") and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in ("br", "cr"):
            parts.append("\n")
        elif tag == "p":
            parts.append("\n")
    return "".join(parts)


def _extract_docx(path: Path, max_chars: int) -> str:
    try:
        with zipfile.ZipFile(path) as bundle:
            xml = bundle.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as exc:
        return f"[could not read docx: {exc}]"
    text = _xml_text(xml)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def _extract_pptx(path: Path, max_chars: int) -> str:
    try:
        with zipfile.ZipFile(path) as bundle:
            slides = sorted(
                name for name in bundle.namelist()
                if name.startswith("ppt/slides/slide") and name.endswith(".xml")
            )
            chunks = []
            for index, slide in enumerate(slides, start=1):
                text = _xml_text(bundle.read(slide)).strip()
                if text:
                    chunks.append(f"--- slide {index} ---\n{text}")
            return "\n\n".join(chunks)[:max_chars]
    except zipfile.BadZipFile as exc:
        return f"[could not read pptx: {exc}]"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _extract_xlsx(path: Path, max_chars: int) -> str:
    """Read the shared strings and cell values of a spreadsheet.

    Traversal is by local tag name so documents with unusual (or missing)
    namespace declarations still yield their contents.
    """
    try:
        with zipfile.ZipFile(path) as bundle:
            names = bundle.namelist()
            shared: list[str] = []
            if "xl/sharedStrings.xml" in names:
                root = ElementTree.fromstring(bundle.read("xl/sharedStrings.xml"))
                for item in root.iter():
                    if _local(item.tag) == "si":
                        shared.append("".join(
                            node.text or "" for node in item.iter() if _local(node.tag) == "t"
                        ))
            sheets = sorted(
                name for name in names
                if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")
            )
            output: list[str] = []
            for sheet in sheets:
                root = ElementTree.fromstring(bundle.read(sheet))
                output.append(f"--- {Path(sheet).stem} ---")
                for row in root.iter():
                    if _local(row.tag) != "row":
                        continue
                    cells: list[str] = []
                    for cell in row:
                        if _local(cell.tag) != "c":
                            continue
                        raw = ""
                        for node in cell.iter():
                            if _local(node.tag) == "v" and node.text:
                                raw = node.text
                                break
                        if not raw:
                            inline = "".join(
                                node.text or "" for node in cell.iter() if _local(node.tag) == "t"
                            )
                            raw = inline
                        if cell.get("t") == "s" and raw.isdigit():
                            index = int(raw)
                            raw = shared[index] if 0 <= index < len(shared) else raw
                        cells.append(raw)
                    if any(cells):
                        output.append(" | ".join(cells))
                    if sum(len(part) for part in output) > max_chars:
                        break
            return "\n".join(output)[:max_chars]
    except (zipfile.BadZipFile, ElementTree.ParseError) as exc:
        return f"[could not read xlsx: {exc}]"


# ------------------------------------------------------------------ plain text
def _read_text_file(path: Path, max_chars: int) -> str:
    if path.suffix.lower() == ".csv":
        return _read_csv(path, max_chars)
    for encoding in ("utf-8", "utf-16", "cp1252", "latin-1"):
        try:
            with path.open("r", encoding=encoding, errors="strict") as handle:
                return handle.read(max_chars)
        except (UnicodeDecodeError, OSError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")[:max_chars]


def _read_csv(path: Path, max_chars: int) -> str:
    try:
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            sample = handle.read(4096)
            handle.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            rows = list(csv.reader(handle, dialect))
    except OSError:
        return ""
    lines = [" | ".join(row) for row in rows[:2000]]
    return "\n".join(lines)[:max_chars]


# ------------------------------------------------------------------- metadata
def metadata_for(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    meta: dict[str, Any] = {"ext": suffix, "size_bytes": path.stat().st_size}
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
        try:
            from PIL import Image  # type: ignore

            with Image.open(path) as image:
                meta.update(width=image.width, height=image.height, mode=image.mode, format=image.format)
        except Exception:
            dimensions = _image_dimensions(path)
            if dimensions:
                meta.update(width=dimensions[0], height=dimensions[1])
            else:
                meta["note"] = "install Pillow for full image metadata"
    elif suffix in {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".mp4", ".mkv", ".mov", ".webm"}:
        try:
            from mutagen import File as MutagenFile  # type: ignore

            audio = MutagenFile(path)
            if audio and audio.tags:
                for key in ("title", "artist", "album", "date", "genre"):
                    values = audio.tags.get(key) or audio.tags.get(key.upper())
                    if values:
                        meta[key] = str(values[0] if isinstance(values, list) else values)
            if audio and getattr(audio.info, "length", None):
                meta["duration_seconds"] = round(audio.info.length, 1)
        except Exception:
            meta["note"] = "install 'mutagen' for audio/video tags; file details still available"
    if suffix == ".pdf":
        try:
            from pypdf import PdfReader

            reader = PdfReader(str(path))
            meta["pages"] = len(reader.pages)
            info = reader.metadata
            if info:
                for key in ("/Title", "/Author", "/CreationDate"):
                    if info.get(key):
                        meta[key.lstrip("/").lower()] = str(info.get(key))
        except Exception:
            pass
    return meta


def _image_dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as handle:
            header = handle.read(26)
        if header[:8] == b"\x89PNG\r\n\x1a\n":
            import struct

            return struct.unpack(">II", header[16:24])
        if header[:2] == b"\xff\xd8":
            import struct

            with path.open("rb") as handle:
                data = handle.read(200000)
            index = 2
            while index < len(data) - 9:
                if data[index] != 0xFF:
                    index += 1
                    continue
                marker = data[index + 1]
                if marker in (0xC0, 0xC1, 0xC2):
                    height, width = struct.unpack(">HH", data[index + 5:index + 9])
                    return width, height
                length = struct.unpack(">H", data[index + 2:index + 4])[0]
                index += 2 + length
    except Exception:
        return None
    return None


def summarise_locally(text: str, max_sentences: int = 6) -> str:
    """Extractive summary that works with no AI provider at all.

    Word-frequency scoring over sentences - crude but honest, and clearly
    labelled in the UI as a local extractive summary.
    """
    text = re.sub(r"\s+", " ", text or "").strip()
    if not text:
        return ""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= max_sentences:
        return text
    stop = set(
        "the a an and or but if then than that this these those of to in on for with without "
        "is are was were be been being it its as at by from not no do does did have has had "
        "you your we our they their he she his her i me my will would can could should".split()
    )
    frequency: dict[str, int] = {}
    for word in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", text.lower()):
        if word in stop:
            continue
        frequency[word] = frequency.get(word, 0) + 1
    scores = []
    for index, sentence in enumerate(sentences):
        words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", sentence.lower())
        if not words:
            continue
        score = sum(frequency.get(word, 0) for word in words) / (len(words) ** 0.5)
        scores.append((score + (1 if index < 3 else 0), index, sentence))
    top = sorted(scores, reverse=True)[:max_sentences]
    ordered = [sentence for _, _, sentence in sorted(top, key=lambda item: item[1])]
    return " ".join(ordered).strip()


def chunk_text(text: str, size: int = 3000, overlap: int = 200) -> list[str]:
    """Split long documents for provider calls that have input limits."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            boundary = text.rfind("\n", start + int(size * 0.6), end)
            if boundary > start:
                end = boundary
        chunks.append(text[start:end])
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return chunks
