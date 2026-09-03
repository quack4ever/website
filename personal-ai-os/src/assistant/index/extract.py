"""Getting readable text out of files.

THE PROBLEM
-----------
A ``.txt`` file is easy - the text is just there.  A ``.docx`` is actually a
ZIP archive full of XML.  A ``.pdf`` is a page-layout format that does not
really contain "lines of text" at all.  To search your documents, we need
plain words out of each of them.

WHAT WE DO, HONESTLY
--------------------
    .txt .md .csv .json .py ...  -> read directly                  ALWAYS WORKS
    .docx .pptx .xlsx            -> unzip and pull the XML text    ALWAYS WORKS
    .html .htm                   -> strip the tags                 ALWAYS WORKS
    .rtf .doc                    -> macOS `textutil`               macOS ONLY
    .pdf                         -> `pdftotext` or the `pypdf`
                                    package, whichever is present  NEEDS ONE OF THEM
    everything else              -> filename and metadata only     STATED PLAINLY

When we cannot read a file we say so in the index (``error`` column) rather
than pretending it was empty.  ``assistant index status`` shows the counts.

The .docx/.pptx/.xlsx readers are written here with the standard library
(``zipfile`` + a small XML text extractor) so they work on a stock Mac with
nothing installed.
"""
from __future__ import annotations

import html
import re
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import NamedTuple, Optional

MAX_CHARS_DEFAULT = 200_000

TEXT_EXTENSIONS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv", ".json", ".yaml",
    ".yml", ".toml", ".ini", ".cfg", ".conf", ".log", ".xml", ".py", ".js",
    ".ts", ".tsx", ".jsx", ".swift", ".c", ".h", ".cpp", ".hpp", ".m", ".mm",
    ".java", ".rb", ".go", ".rs", ".sh", ".zsh", ".bash", ".sql", ".tex",
    ".bib", ".srt", ".vtt", ".env.example", ".gitignore", ".r", ".jl", ".lua",
}
OFFICE_EXTENSIONS = {".docx", ".pptx", ".xlsx"}
HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
TEXTUTIL_EXTENSIONS = {".rtf", ".rtfd", ".doc", ".webarchive"}


class Extraction(NamedTuple):
    text: str
    method: str
    ok: bool
    error: Optional[str] = None
    truncated: bool = False


def _strip_xml_tags(xml_text: str) -> str:
    """Turn XML into readable words.

    We insert a space where a tag was, so ``<w:t>Hello</w:t><w:t>World</w:t>``
    becomes "Hello World" rather than "HelloWorld".
    """
    # Paragraph and row ends become newlines so structure survives a little.
    text = re.sub(r"</(w:p|a:p|text:p|tr)>", "\n", xml_text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _from_office(path: Path, max_chars: int) -> Extraction:
    """A .docx/.pptx/.xlsx is a ZIP of XML files. Read the parts holding words."""
    wanted_prefixes = ("word/document", "word/footnotes", "word/endnotes",
                       "ppt/slides/slide", "ppt/notesSlides/notesSlide",
                       "xl/sharedStrings")
    chunks = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist()
                     if n.endswith(".xml") and n.startswith(wanted_prefixes)]
            # Slides sort as slide1, slide10, slide2 alphabetically - fix that
            # so a deck reads in the right order.
            def slide_number(name: str) -> int:
                match = re.search(r"(\d+)\.xml$", name)
                return int(match.group(1)) if match else 0
            for name in sorted(names, key=lambda n: (n.split("/")[0], slide_number(n), n)):
                with archive.open(name) as member:
                    chunks.append(_strip_xml_tags(
                        member.read().decode("utf-8", errors="replace")))
                if sum(len(c) for c in chunks) > max_chars:
                    break
    except (zipfile.BadZipFile, KeyError, OSError) as exc:
        return Extraction("", "office-zip", False,
                          "the file could not be opened as an Office document (%s)" % exc)
    text = "\n\n".join(c for c in chunks if c)
    return Extraction(text[:max_chars], "office-zip", True,
                      truncated=len(text) > max_chars)


def _from_html(path: Path, max_chars: int) -> Extraction:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Extraction("", "html", False, str(exc))
    # Drop script/style bodies entirely - they are not readable content.
    raw = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", raw)
    text = _strip_xml_tags(raw)
    return Extraction(text[:max_chars], "html", True, truncated=len(text) > max_chars)


def _from_textutil(path: Path, max_chars: int) -> Extraction:
    """macOS ships `textutil`, which converts RTF/DOC/webarchive to text."""
    if not shutil.which("textutil"):
        return Extraction("", "textutil", False,
                          "textutil is only available on macOS, so this format "
                          "cannot be read here")
    try:
        result = subprocess.run(
            ["textutil", "-convert", "txt", "-stdout", str(path)],
            capture_output=True, timeout=30, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return Extraction("", "textutil", False, str(exc))
    if result.returncode != 0:
        return Extraction("", "textutil", False,
                          result.stderr.decode("utf-8", "replace")[:200])
    text = result.stdout.decode("utf-8", errors="replace")
    return Extraction(text[:max_chars], "textutil", True, truncated=len(text) > max_chars)


def _from_pdf(path: Path, max_chars: int) -> Extraction:
    """PDF needs a helper. We try the two most common, then say so plainly."""
    if shutil.which("pdftotext"):
        try:
            result = subprocess.run(
                ["pdftotext", "-q", "-enc", "UTF-8", str(path), "-"],
                capture_output=True, timeout=60, check=False)
            if result.returncode == 0:
                text = result.stdout.decode("utf-8", errors="replace")
                return Extraction(text[:max_chars], "pdftotext", True,
                                  truncated=len(text) > max_chars)
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        import pypdf  # type: ignore
        reader = pypdf.PdfReader(str(path))
        parts = []
        for page in reader.pages[:200]:
            parts.append(page.extract_text() or "")
            if sum(len(p) for p in parts) > max_chars:
                break
        text = "\n".join(parts)
        return Extraction(text[:max_chars], "pypdf", True,
                          truncated=len(text) > max_chars)
    except ImportError:
        return Extraction(
            "", "pdf", False,
            "PDF text extraction needs a helper that is not installed. "
            "Install either poppler ('brew install poppler', gives pdftotext) "
            "or the pypdf package. Until then this PDF is indexed by filename "
            "only.")
    except Exception as exc:
        return Extraction("", "pypdf", False,
                          "the PDF could not be read (%s)" % exc)


def _from_text(path: Path, max_chars: int) -> Extraction:
    try:
        with open(path, "rb") as handle:
            raw = handle.read(max_chars * 4)
    except OSError as exc:
        return Extraction("", "text", False, str(exc))
    if b"\x00" in raw[:8192]:
        return Extraction("", "text", False,
                          "this looks like a binary file, not text")
    text = raw.decode("utf-8", errors="replace")
    return Extraction(text[:max_chars], "text", True, truncated=len(text) > max_chars)


def extract(path: Path, max_chars: int = MAX_CHARS_DEFAULT) -> Extraction:
    """Best-effort text for one file.  Never raises."""
    suffix = path.suffix.lower()
    try:
        if suffix in OFFICE_EXTENSIONS:
            return _from_office(path, max_chars)
        if suffix in HTML_EXTENSIONS:
            return _from_html(path, max_chars)
        if suffix in TEXTUTIL_EXTENSIONS:
            return _from_textutil(path, max_chars)
        if suffix == ".pdf":
            return _from_pdf(path, max_chars)
        if suffix in TEXT_EXTENSIONS or suffix == "":
            return _from_text(path, max_chars)
        return Extraction("", "skipped", False,
                          "'%s' files are indexed by name and date only - "
                          "there is no text reader for this format" % (suffix or "extensionless"))
    except Exception as exc:  # never let one odd file stop a whole scan
        return Extraction("", "error", False, "%s: %s" % (type(exc).__name__, exc))


def classify(path: Path) -> str:
    """A rough human category, used for grouping in search results."""
    suffix = path.suffix.lower()
    if suffix in {".pdf", ".docx", ".doc", ".rtf", ".pages", ".odt", ".txt", ".md"}:
        return "document"
    if suffix in {".xlsx", ".xls", ".csv", ".tsv", ".numbers"}:
        return "spreadsheet"
    if suffix in {".pptx", ".ppt", ".key"}:
        return "presentation"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".heic", ".tiff", ".webp", ".svg"}:
        return "image"
    if suffix in {".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".m4a", ".aac"}:
        return "media"
    if suffix in {".py", ".js", ".ts", ".swift", ".c", ".h", ".java", ".go",
                  ".rs", ".rb", ".sh", ".sql", ".html", ".css"}:
        return "code"
    if suffix in {".zip", ".tar", ".gz", ".dmg", ".pkg", ".7z"}:
        return "archive"
    return "other"
