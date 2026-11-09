#!/usr/bin/env python3
"""
DocuPad - Document Parsers, Outline Extractor, and PDF Renderer
Provides high-DPI PDF page rasterization via pdftoppm, PDF text extraction via pdftotext,
ODT/DOCX XML paragraph extraction, CSV/TSV table formatting, and TOC heading extraction.
"""

import csv
import ctypes
import html
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import cairo
import gi
try:
    gi.require_version('Gdk', '3.0')
    gi.require_version('GdkPixbuf', '2.0')
    from gi.repository import Gdk, GdkPixbuf, GLib
except Exception:
    pass


class PycairoContext(ctypes.Structure):
    _fields_ = [
        ('PyObject_HEAD', ctypes.c_byte * object.__basicsize__),
        ('ctx', ctypes.c_void_p),
    ]


class PopplerBridge:
    """Direct ctypes bridge to libpoppler-glib for 75x faster in-memory Cairo PDF rasterization."""

    _lib = None
    _libgobject = None
    _libglib = None
    _loaded = False
    _available = False
    _lock = threading.Lock()

    @classmethod
    def is_available(cls) -> bool:
        cls._ensure_loaded()
        return cls._available

    @classmethod
    def _ensure_loaded(cls):
        if cls._loaded:
            return
        cls._loaded = True
        for name in ("libpoppler-glib.so.8", "libpoppler-glib.so"):
            try:
                cls._lib = ctypes.CDLL(name)
                break
            except OSError:
                pass

        if not cls._lib:
            return

        try:
            cls._libgobject = ctypes.CDLL("libgobject-2.0.so.0")
            cls._libglib = ctypes.CDLL("libglib-2.0.so.0")

            cls._lib.poppler_document_new_from_file.restype = ctypes.c_void_p
            cls._lib.poppler_document_new_from_file.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]

            cls._lib.poppler_document_get_n_pages.restype = ctypes.c_int
            cls._lib.poppler_document_get_n_pages.argtypes = [ctypes.c_void_p]

            cls._lib.poppler_document_get_page.restype = ctypes.c_void_p
            cls._lib.poppler_document_get_page.argtypes = [ctypes.c_void_p, ctypes.c_int]

            cls._lib.poppler_page_get_size.restype = None
            cls._lib.poppler_page_get_size.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_double), ctypes.POINTER(ctypes.c_double)]

            cls._lib.poppler_page_render.restype = None
            cls._lib.poppler_page_render.argtypes = [ctypes.c_void_p, ctypes.c_void_p]

            cls._lib.poppler_page_get_text.restype = ctypes.c_void_p
            cls._lib.poppler_page_get_text.argtypes = [ctypes.c_void_p]

            cls._libgobject.g_object_unref.argtypes = [ctypes.c_void_p]
            cls._libglib.g_free.argtypes = [ctypes.c_void_p]

            cls._available = True
        except Exception:
            cls._available = False

    @classmethod
    def open_document(cls, file_path: str):
        if not cls.is_available():
            return None
        uri = "file://" + os.path.abspath(file_path)
        with cls._lock:
            doc = cls._lib.poppler_document_new_from_file(uri.encode("utf-8"), None, None)
        return doc

    @classmethod
    def get_page_count(cls, doc_handle) -> int:
        if not doc_handle or not cls.is_available():
            return 0
        with cls._lock:
            return cls._lib.poppler_document_get_n_pages(doc_handle)

    @classmethod
    def get_page_size(cls, doc_handle, page_index: int) -> Tuple[float, float]:
        if not doc_handle or not cls.is_available():
            return (595.0, 842.0)
        with cls._lock:
            page = cls._lib.poppler_document_get_page(doc_handle, page_index)
            if not page:
                return (595.0, 842.0)
            pw = ctypes.c_double()
            ph = ctypes.c_double()
            cls._lib.poppler_page_get_size(page, ctypes.byref(pw), ctypes.byref(ph))
            cls._libgobject.g_object_unref(page)
            return (pw.value, ph.value)

    @classmethod
    def render_page(cls, doc_handle, page_index: int, scale: float = 1.0) -> Optional[cairo.ImageSurface]:
        if not doc_handle or not cls.is_available():
            return None
        with cls._lock:
            page = cls._lib.poppler_document_get_page(doc_handle, page_index)
            if not page:
                return None
            try:
                pw = ctypes.c_double()
                ph = ctypes.c_double()
                cls._lib.poppler_page_get_size(page, ctypes.byref(pw), ctypes.byref(ph))
                target_w = max(1, int(round(pw.value * scale)))
                target_h = max(1, int(round(ph.value * scale)))

                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, target_w, target_h)
                cr = cairo.Context(surf)
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.paint()
                cr.scale(scale, scale)

                c_cr = PycairoContext.from_address(id(cr)).ctx
                cls._lib.poppler_page_render(page, c_cr)
                return surf
            finally:
                cls._libgobject.g_object_unref(page)

    @classmethod
    def extract_text(cls, doc_handle, max_pages: int = 200) -> str:
        if not doc_handle or not cls.is_available():
            return ""
        pages_out = []
        with cls._lock:
            n_pages = cls._lib.poppler_document_get_n_pages(doc_handle)
            limit = min(n_pages, max_pages)
            for i in range(limit):
                page = cls._lib.poppler_document_get_page(doc_handle, i)
                if not page:
                    continue
                txt_ptr = cls._lib.poppler_page_get_text(page)
                if txt_ptr:
                    txt = ctypes.string_at(txt_ptr).decode("utf-8", errors="replace")
                    cls._libglib.g_free(txt_ptr)
                    pages_out.append(txt)
                else:
                    pages_out.append("")
                cls._libgobject.g_object_unref(page)
        return "\x0c".join(pages_out)

    @classmethod
    def close_document(cls, doc_handle):
        if doc_handle and cls._libgobject:
            with cls._lock:
                cls._libgobject.g_object_unref(doc_handle)


@dataclass
class HeadingItem:
    """Represents a navigable section heading or page marker in the document outline."""
    title: str
    line: int
    level: int = 1
    page: int = 0


class DocumentParser:
    """Parses structured and rich document formats into clean text and outlines for DocuPad."""

    PDF_EXTENSIONS = {".pdf"}
    MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}
    RST_EXTENSIONS = {".rst", ".rest"}
    HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
    JSON_EXTENSIONS = {".json", ".jsonl"}
    CSV_EXTENSIONS = {".csv", ".tsv"}
    ODT_EXTENSIONS = {".odt"}
    DOCX_EXTENSIONS = {".docx"}
    XML_EXTENSIONS = {".xml", ".svg", ".plist"}

    @classmethod
    def get_doc_type(cls, file_path: Optional[str]) -> str:
        if not file_path:
            return "markdown"
        ext = os.path.splitext(file_path)[1].lower()
        if ext in cls.PDF_EXTENSIONS:
            return "pdf"
        if ext in cls.MARKDOWN_EXTENSIONS:
            return "markdown"
        if ext in cls.RST_EXTENSIONS:
            return "rst"
        if ext in cls.HTML_EXTENSIONS:
            return "html"
        if ext in cls.JSON_EXTENSIONS:
            return "json"
        if ext in cls.CSV_EXTENSIONS:
            return "csv"
        if ext in cls.ODT_EXTENSIONS:
            return "odt"
        if ext in cls.DOCX_EXTENSIONS:
            return "docx"
        if ext in cls.XML_EXTENSIONS:
            return "xml"
        return "text"

    @classmethod
    def load_document_text(cls, file_path: str) -> Tuple[str, str, str]:
        """
        Loads a file into readable text.
        Returns (text_content, encoding_label, doc_type).
        """
        doc_type = cls.get_doc_type(file_path)

        if doc_type == "pdf":
            text = cls.extract_pdf_text(file_path)
            return text, "PDF", "pdf"

        if doc_type == "odt":
            text = cls.extract_odt_text(file_path)
            return text, "ODT (XML)", "odt"

        if doc_type == "docx":
            text = cls.extract_docx_text(file_path)
            return text, "DOCX (XML)", "docx"

        raw_bytes = b""
        with open(file_path, "rb") as f:
            raw_bytes = f.read()

        had_nulls = b"\x00" in raw_bytes
        encoding = "UTF-8"
        text = None
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                text = raw_bytes.decode(enc)
                encoding = enc.upper()
                break
            except UnicodeDecodeError:
                continue

        if text is None:
            text = raw_bytes.decode("utf-8", errors="replace")
            encoding = "UTF-8 (lossy)"

        if had_nulls:
            text = text.replace("\x00", "\ufffd")
            if "lossy" not in encoding.lower():
                encoding = f"{encoding} (binary)"

        text = text.replace("\r\n", "\n")

        if doc_type == "json" and file_path.lower().endswith(".json"):
            try:
                parsed = json.loads(text)
                text = json.dumps(parsed, indent=2, ensure_ascii=False)
            except Exception:
                pass

        return text, encoding, doc_type

    @staticmethod
    def extract_pdf_text(file_path: str) -> str:
        """Extract text from a PDF file using PopplerBridge (with pdftotext fallback)."""
        abs_path = os.path.abspath(file_path)
        if PopplerBridge.is_available():
            doc = PopplerBridge.open_document(abs_path)
            if doc:
                try:
                    txt = PopplerBridge.extract_text(doc)
                    if txt.strip():
                        return txt.replace("\x00", "\ufffd")
                finally:
                    PopplerBridge.close_document(doc)

        if shutil.which("pdftotext"):
            try:
                proc = subprocess.run(
                    ["pdftotext", "-layout", "--", abs_path, "-"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=20,
                    check=False
                )
                if proc.returncode == 0 and proc.stdout.strip():
                    return proc.stdout.replace("\x00", "\ufffd")
            except Exception:
                pass
        return f"[PDF Document: {os.path.basename(file_path)}]\nSwitch to Rendered View to inspect PDF pages."

    @staticmethod
    def extract_odt_text(file_path: str) -> str:
        """Extract readable paragraphs and headings from an OpenDocument Text (.odt) archive."""
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                if "content.xml" not in zf.namelist():
                    return f"[Invalid ODT file: {os.path.basename(file_path)}]"
                xml_bytes = zf.read("content.xml")
            root = ET.fromstring(xml_bytes)
            paragraphs = []
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag in ("h", "p"):
                    txt = "".join(elem.itertext()).strip()
                    if txt:
                        if tag == "h":
                            paragraphs.append(f"## {txt}")
                        else:
                            paragraphs.append(txt)
            return "\n\n".join(paragraphs) if paragraphs else "[Empty ODT Document]"
        except Exception as e:
            return f"[Error reading ODT document: {e}]"

    @staticmethod
    def extract_docx_text(file_path: str) -> str:
        """Extract readable paragraphs and headings from a Microsoft Word (.docx) archive."""
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                if "word/document.xml" not in zf.namelist():
                    return f"[Invalid DOCX file: {os.path.basename(file_path)}]"
                xml_bytes = zf.read("word/document.xml")
            root = ET.fromstring(xml_bytes)
            paragraphs = []
            for elem in root.iter():
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "p":
                    txt = "".join(elem.itertext()).strip()
                    if not txt:
                        continue
                    prefix = ""
                    for child in elem.iter():
                        ctag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if ctag == "pStyle":
                            for k, v in child.attrib.items():
                                if k.endswith("val"):
                                    low = v.lower()
                                    if low == "title":
                                        prefix = "# "
                                    elif low == "subtitle":
                                        prefix = "## "
                                    elif low.startswith("heading"):
                                        num = "".join(ch for ch in low if ch.isdigit())
                                        lvl = min(6, max(1, int(num))) if num else 2
                                        prefix = "#" * lvl + " "
                    paragraphs.append(f"{prefix}{txt}")
            return "\n\n".join(paragraphs) if paragraphs else "[Empty DOCX Document]"
        except Exception as e:
            return f"[Error reading DOCX document: {e}]"

    @classmethod
    def extract_outline(cls, text: str, doc_type: str = "markdown", pdf_page_count: int = 0) -> List[HeadingItem]:
        """Extract Table of Contents / Outline headings from document content."""
        items: List[HeadingItem] = []

        if doc_type == "pdf" and pdf_page_count > 0:
            pages = text.split("\x0c")
            for idx in range(pdf_page_count):
                snippet = ""
                if idx < len(pages):
                    for line in pages[idx].splitlines():
                        s = line.strip()
                        if len(s) >= 3:
                            snippet = f" — {s[:42]}"
                            break
                items.append(HeadingItem(title=f"Page {idx + 1}{snippet}", line=1, level=1, page=idx + 1))
            return items

        lines = text.splitlines()
        in_code_block = False

        for idx, raw_line in enumerate(lines, start=1):
            stripped = raw_line.strip()
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            # Markdown ATX headings (# Heading)
            m_md = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
            if m_md:
                level = len(m_md.group(1))
                title = m_md.group(2).strip()
                items.append(HeadingItem(title=title, line=idx, level=level))
                continue

            # reStructuredText / Setext underlined headings
            if idx > 1 and stripped and len(stripped) >= 3 and all(c in "=-~^\"`+*" for c in stripped) and len(set(stripped)) == 1:
                prev = lines[idx - 2].strip()
                if prev and not prev.startswith("#"):
                    ch = stripped[0]
                    level = 1 if ch in ("=", "*") else (2 if ch in ("-", "~") else 3)
                    items.append(HeadingItem(title=prev, line=idx - 1, level=level))
                    continue

            # HTML headings (<h1>...</h1>)
            if doc_type == "html":
                m_html = re.search(r"<h([1-6])[^>]*>(.*?)</h\1>", stripped, re.IGNORECASE)
                if m_html:
                    level = int(m_html.group(1))
                    clean_title = re.sub(r"<[^>]+>", "", m_html.group(2)).strip()
                    if clean_title:
                        items.append(HeadingItem(title=html.unescape(clean_title), line=idx, level=level))
                    continue

            # JSON top-level keys
            if doc_type == "json":
                m_json = re.match(r'^  "([^"]+)"\s*:', raw_line)
                if m_json:
                    items.append(HeadingItem(title=m_json.group(1), line=idx, level=1))

        # Fallback for plain text documents: detectnumbered or ALL-CAPS section headers
        if not items:
            for idx, raw_line in enumerate(lines, start=1):
                s = raw_line.strip()
                if re.match(r"^(\d+\.|\bSECTION\b|\bCHAPTER\b|\bPART\b)\s+[A-Z]", s):
                    items.append(HeadingItem(title=s[:60], line=idx, level=1))

        return items

    @classmethod
    def render_markdown_to_html(cls, text: str, doc_type: str = "markdown", file_path: Optional[str] = None) -> str:
        """Convert Markdown, RST, CSV, JSON, or plain text into styled dark-neon HTML for rich reading."""
        if doc_type == "html":
            return text

        if doc_type == "csv":
            body_html = cls._csv_to_html_table(text, file_path)
        elif doc_type == "json":
            body_html = f"<pre class='code-block'><code>{html.escape(text)}</code></pre>"
        else:
            body_html = cls._markdown_to_html_body(text)

        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  :root {{
    color-scheme: dark;
  }}
  body {{
    background-color: #0a0e1a;
    color: #f8fafc;
    font-family: 'Inter', 'Ubuntu', 'DejaVu Sans', sans-serif;
    font-size: 16px;
    line-height: 1.7;
    max-width: 900px;
    margin: 0 auto;
    padding: 32px 40px 64px 40px;
  }}
  h1, h2, h3, h4, h5, h6 {{
    color: #00f0ff;
    font-weight: 700;
    margin-top: 1.5em;
    margin-bottom: 0.5em;
    letter-spacing: 0.2px;
  }}
  h1 {{
    font-size: 2.0em;
    border-bottom: 1px solid rgba(0, 240, 255, 0.35);
    padding-bottom: 0.3em;
  }}
  h2 {{
    font-size: 1.5em;
    color: #38bdf8;
    border-bottom: 1px solid rgba(56, 189, 248, 0.22);
    padding-bottom: 0.25em;
  }}
  h3 {{
    font-size: 1.25em;
    color: #7dd3fc;
  }}
  p {{
    margin: 0.85em 0;
  }}
  a {{
    color: #00f0ff;
    text-decoration: none;
    border-bottom: 1px dashed rgba(0, 240, 255, 0.5);
  }}
  code {{
    font-family: 'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', monospace;
    background-color: #111c33;
    color: #38bdf8;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.92em;
  }}
  pre.code-block {{
    background-color: #070a14;
    border: 1px solid rgba(0, 240, 255, 0.28);
    border-left: 3px solid #00f0ff;
    border-radius: 8px;
    padding: 14px 18px;
    overflow-x: auto;
  }}
  pre.code-block code {{
    background: transparent;
    color: #f0f9ff;
    padding: 0;
  }}
  blockquote {{
    margin: 1em 0;
    padding: 10px 18px;
    background-color: rgba(17, 28, 51, 0.7);
    border-left: 4px solid #38bdf8;
    color: #bae6fd;
    border-radius: 0 6px 6px 0;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 1.2em 0;
    background-color: #0d1324;
    border: 1px solid rgba(0, 240, 255, 0.25);
  }}
  th, td {{
    border: 1px solid rgba(56, 189, 248, 0.22);
    padding: 8px 12px;
    text-align: left;
  }}
  th {{
    background-color: #111c33;
    color: #00f0ff;
    font-weight: 700;
  }}
  tr:nth-child(even) {{
    background-color: #0b1020;
  }}
  hr {{
    border: none;
    border-top: 1px solid rgba(0, 240, 255, 0.3);
    margin: 2em 0;
  }}
  ul, ol {{
    padding-left: 1.6em;
  }}
  li {{
    margin: 0.35em 0;
  }}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

    @classmethod
    def _csv_to_html_table(cls, text: str, file_path: Optional[str] = None) -> str:
        delimiter = "\t" if (file_path and file_path.lower().endswith(".tsv")) else ","
        reader = csv.reader(io.StringIO(text), delimiter=delimiter)
        rows = list(reader)
        if not rows:
            return "<p><em>Empty table</em></p>"
        out = ["<table>"]
        header = rows[0]
        out.append("<thead><tr>" + "".join(f"<th>{html.escape(c)}</th>" for c in header) + "</tr></thead>")
        out.append("<tbody>")
        for r in rows[1:500]:
            out.append("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>")
        out.append("</tbody></table>")
        return "\n".join(out)

    @classmethod
    def _markdown_to_html_body(cls, text: str) -> str:
        lines = text.splitlines()
        out: List[str] = []
        in_code = False
        in_ul = False
        in_ol = False
        in_table = False

        def close_lists():
            nonlocal in_ul, in_ol, in_table
            if in_ul:
                out.append("</ul>")
                in_ul = False
            if in_ol:
                out.append("</ol>")
                in_ol = False
            if in_table:
                out.append("</tbody></table>")
                in_table = False

        def format_inline(s: str) -> str:
            escaped = html.escape(s)
            escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
            escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
            escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
            escaped = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', escaped)
            return escaped

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("```"):
                close_lists()
                if not in_code:
                    in_code = True
                    out.append("<pre class='code-block'><code>")
                else:
                    in_code = False
                    out.append("</code></pre>")
                continue

            if in_code:
                out.append(html.escape(line))
                continue

            if not stripped:
                close_lists()
                continue

            # Horizontal rule
            if re.match(r"^([-*_])\1{2,}$", stripped):
                close_lists()
                out.append("<hr>")
                continue

            # Headings
            m_h = re.match(r"^(#{1,6})\s+(.+?)\s*#*$", stripped)
            if m_h:
                close_lists()
                lvl = len(m_h.group(1))
                out.append(f"<h{lvl}>{format_inline(m_h.group(2))}</h{lvl}>")
                continue

            # Markdown table row
            if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 2:
                cells = [c.strip() for c in stripped.strip("|").split("|")]
                if all(re.match(r"^:?-+:?$", c) for c in cells if c):
                    continue
                if not in_table:
                    close_lists()
                    in_table = True
                    out.append("<table><thead><tr>" + "".join(f"<th>{format_inline(c)}</th>" for c in cells) + "</tr></thead><tbody>")
                else:
                    out.append("<tr>" + "".join(f"<td>{format_inline(c)}</td>" for c in cells) + "</tr>")
                continue

            # Blockquote
            if stripped.startswith(">"):
                close_lists()
                q = stripped.lstrip(">").strip()
                out.append(f"<blockquote>{format_inline(q)}</blockquote>")
                continue

            # Unordered list
            m_ul = re.match(r"^[-*+]\s+(.+)$", stripped)
            if m_ul:
                if in_ol or in_table:
                    close_lists()
                if not in_ul:
                    in_ul = True
                    out.append("<ul>")
                out.append(f"<li>{format_inline(m_ul.group(1))}</li>")
                continue

            # Ordered list
            m_ol = re.match(r"^\d+\.\s+(.+)$", stripped)
            if m_ol:
                if in_ul or in_table:
                    close_lists()
                if not in_ol:
                    in_ol = True
                    out.append("<ol>")
                out.append(f"<li>{format_inline(m_ol.group(1))}</li>")
                continue

            close_lists()
            out.append(f"<p>{format_inline(stripped)}</p>")

        if in_code:
            out.append("</code></pre>")
        close_lists()
        return "\n".join(out)


class PDFPageRenderer:
    """
    Renders PDF pages asynchronously on-demand using PopplerBridge (in-memory Cairo)
    with a pdftoppm fallback.
    """

    def __init__(self, pdf_path: Optional[str] = None):
        self.pdf_path = None
        self.temp_dir = tempfile.mkdtemp(prefix="docupad_pdf_")
        self.page_count = 0
        self._doc_handle = None
        self._page_sizes = {}
        self._pixbuf_cache = {}  # (page_num, scale_key): GdkPixbuf.Pixbuf
        self._render_lock = threading.Lock()
        if pdf_path:
            self.load_pdf(pdf_path)

    def load_pdf(self, pdf_path: str) -> int:
        self.cleanup_doc()
        self.pdf_path = os.path.abspath(pdf_path)
        if PopplerBridge.is_available():
            self._doc_handle = PopplerBridge.open_document(self.pdf_path)
            if self._doc_handle:
                self.page_count = PopplerBridge.get_page_count(self._doc_handle)
                return self.page_count
        self.page_count = self._detect_page_count(self.pdf_path)
        return self.page_count

    def get_page_size(self, page_num: int) -> Tuple[float, float]:
        if page_num in self._page_sizes:
            return self._page_sizes[page_num]
        if self._doc_handle and PopplerBridge.is_available():
            sz = PopplerBridge.get_page_size(self._doc_handle, page_num - 1)
            self._page_sizes[page_num] = sz
            return sz
        return (595.0, 842.0)

    def render_page_pixbuf(self, page_num: int, scale: float = 1.0) -> Optional[GdkPixbuf.Pixbuf]:
        """Render page_num (1-based) to GdkPixbuf at scale factor without blocking disk operations."""
        if not self.pdf_path or self.page_count == 0:
            return None
        page_num = max(1, min(page_num, self.page_count))
        scale_key = round(scale, 2)
        cache_key = (page_num, scale_key)
        with self._render_lock:
            if cache_key in self._pixbuf_cache:
                return self._pixbuf_cache[cache_key]

        pix = None
        if self._doc_handle and PopplerBridge.is_available():
            surf = PopplerBridge.render_page(self._doc_handle, page_num - 1, scale=scale * 1.333)
            if surf is not None:
                w = surf.get_width()
                h = surf.get_height()
                pix = Gdk.pixbuf_get_from_surface(surf, 0, 0, w, h)

        if pix is None:
            # Fallback to pdftoppm
            dpi = int(round(120 * scale))
            png_path = self.render_page_png(page_num, dpi=dpi)
            if png_path and os.path.isfile(png_path):
                try:
                    pix = GdkPixbuf.Pixbuf.new_from_file(png_path)
                except Exception:
                    pass

        if pix is not None:
            with self._render_lock:
                if len(self._pixbuf_cache) > 16:
                    self._pixbuf_cache.pop(next(iter(self._pixbuf_cache)))
                self._pixbuf_cache[cache_key] = pix

        return pix

    def render_page_async(self, page_num: int, scale: float, callback: Callable[[int, float, Optional[GdkPixbuf.Pixbuf]], None]):
        """Asynchronously render page in a background thread and invoke callback(page_num, scale, pixbuf) on main loop."""
        def worker():
            pix = self.render_page_pixbuf(page_num, scale)
            GLib.idle_add(callback, page_num, scale, pix)

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    @staticmethod
    def _detect_page_count(pdf_path: str) -> int:
        if not os.path.isfile(pdf_path):
            return 0
        if shutil.which("pdfinfo"):
            try:
                proc = subprocess.run(
                    ["pdfinfo", "--", pdf_path],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=10,
                    check=False
                )
                for line in proc.stdout.splitlines():
                    if line.lower().startswith("pages:"):
                        return max(1, int(line.split(":", 1)[1].strip()))
            except Exception:
                pass
        # Fallback: count form-feeds in pdftotext
        if shutil.which("pdftotext"):
            try:
                proc = subprocess.run(
                    ["pdftotext", "--", pdf_path, "-"],
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=15,
                    check=False
                )
                if proc.returncode == 0:
                    return max(1, proc.stdout.count("\x0c"))
            except Exception:
                pass
        return 1

    def render_page_png(self, page_num: int, dpi: int = 150) -> Optional[str]:
        """Render 1-based page_num at the given DPI and return the PNG file path."""
        if not self.pdf_path or not os.path.isfile(self.pdf_path):
            return None
        page_num = max(1, min(page_num, max(1, self.page_count)))
        out_prefix = os.path.join(self.temp_dir, f"page_{page_num}_{dpi}")
        expected_png = f"{out_prefix}.png"
        if os.path.isfile(expected_png):
            return expected_png

        # Use PopplerBridge if available (75x faster than pdftoppm CLI)
        if self._doc_handle and PopplerBridge.is_available():
            scale = dpi / 72.0
            surf = PopplerBridge.render_page(self._doc_handle, page_num - 1, scale=scale)
            if surf is not None:
                try:
                    surf.write_to_png(expected_png)
                    return expected_png
                except Exception:
                    pass

        # pdftoppm CLI fallback
        if shutil.which("pdftoppm"):
            try:
                subprocess.run(
                    [
                        "pdftoppm",
                        "-png",
                        "-r", str(dpi),
                        "-f", str(page_num),
                        "-l", str(page_num),
                        "-singlefile",
                        "--",
                        self.pdf_path,
                        out_prefix,
                    ],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                if os.path.isfile(expected_png):
                    return expected_png
            except Exception:
                pass
        return None

    def cleanup_doc(self):
        with self._render_lock:
            self._pixbuf_cache.clear()
            self._page_sizes.clear()
            if self._doc_handle and PopplerBridge.is_available():
                PopplerBridge.close_document(self._doc_handle)
                self._doc_handle = None

    def cleanup(self):
        self.cleanup_doc()
        if self.temp_dir and os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
