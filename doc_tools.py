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


class PopplerRectangle(ctypes.Structure):
    _fields_ = [
        ("x1", ctypes.c_double),
        ("y1", ctypes.c_double),
        ("x2", ctypes.c_double),
        ("y2", ctypes.c_double),
    ]


class PopplerLinkMapping(ctypes.Structure):
    _fields_ = [
        ("area", PopplerRectangle),
        ("action", ctypes.c_void_p),
    ]


class GList(ctypes.Structure):
    pass


GList._fields_ = [
    ("data", ctypes.c_void_p),
    ("next", ctypes.POINTER(GList)),
    ("prev", ctypes.POINTER(GList)),
]


class PopplerActionAny(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("title", ctypes.c_char_p),
    ]


class PopplerActionUri(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("title", ctypes.c_char_p),
        ("uri", ctypes.c_char_p),
    ]


class PopplerDest(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("page_num", ctypes.c_int),
        ("left", ctypes.c_double),
        ("bottom", ctypes.c_double),
        ("right", ctypes.c_double),
        ("top", ctypes.c_double),
        ("zoom", ctypes.c_double),
        ("named_dest", ctypes.c_char_p),
        ("change_left", ctypes.c_uint),
        ("change_top", ctypes.c_uint),
        ("change_zoom", ctypes.c_uint),
    ]


class PopplerActionGotoDest(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("title", ctypes.c_char_p),
        ("dest", ctypes.POINTER(PopplerDest)),
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

            cls._lib.poppler_page_get_text_layout.restype = ctypes.c_int
            cls._lib.poppler_page_get_text_layout.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.POINTER(PopplerRectangle)),
                ctypes.POINTER(ctypes.c_uint)
            ]

            cls._lib.poppler_page_get_link_mapping.restype = ctypes.POINTER(GList)
            cls._lib.poppler_page_get_link_mapping.argtypes = [ctypes.c_void_p]

            cls._lib.poppler_page_free_link_mapping.restype = None
            cls._lib.poppler_page_free_link_mapping.argtypes = [ctypes.POINTER(GList)]

            cls._lib.poppler_document_find_dest.restype = ctypes.POINTER(PopplerDest)
            cls._lib.poppler_document_find_dest.argtypes = [ctypes.c_void_p, ctypes.c_char_p]

            cls._lib.poppler_dest_free.restype = None
            cls._lib.poppler_dest_free.argtypes = [ctypes.POINTER(PopplerDest)]

            cls._lib.poppler_index_iter_new.restype = ctypes.c_void_p
            cls._lib.poppler_index_iter_new.argtypes = [ctypes.c_void_p]

            cls._lib.poppler_index_iter_get_action.restype = ctypes.c_void_p
            cls._lib.poppler_index_iter_get_action.argtypes = [ctypes.c_void_p]

            cls._lib.poppler_index_iter_get_child.restype = ctypes.c_void_p
            cls._lib.poppler_index_iter_get_child.argtypes = [ctypes.c_void_p]

            cls._lib.poppler_index_iter_next.restype = ctypes.c_int
            cls._lib.poppler_index_iter_next.argtypes = [ctypes.c_void_p]

            cls._lib.poppler_index_iter_free.restype = None
            cls._lib.poppler_index_iter_free.argtypes = [ctypes.c_void_p]

            cls._lib.poppler_action_free.restype = None
            cls._lib.poppler_action_free.argtypes = [ctypes.c_void_p]

            cls._libgobject.g_object_unref.argtypes = [ctypes.c_void_p]
            cls._libglib.g_free.argtypes = [ctypes.c_void_p]

            cls._available = True
        except Exception:
            cls._available = False

    @classmethod
    def open_document(cls, file_path: str):
        if not cls.is_available():
            return None
        abs_p = os.path.abspath(file_path)
        try:
            uri = GLib.filename_to_uri(abs_p, None)
        except Exception:
            import urllib.parse
            uri = "file://" + urllib.parse.quote(abs_p)
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
    def get_page_text_layout(cls, doc_handle, page_index: int) -> Tuple[str, List[Tuple[float, float, float, float]]]:
        """Extract page text and exact per-character bounding rectangles (x1, y1, x2, y2)."""
        if not doc_handle or not cls.is_available():
            return "", []
        with cls._lock:
            page = cls._lib.poppler_document_get_page(doc_handle, page_index)
            if not page:
                return "", []
            try:
                txt_ptr = cls._lib.poppler_page_get_text(page)
                text = ""
                if txt_ptr:
                    try:
                        text = ctypes.string_at(txt_ptr).decode("utf-8", errors="replace")
                    finally:
                        cls._libglib.g_free(txt_ptr)

                rects_ptr = ctypes.POINTER(PopplerRectangle)()
                n_rects = ctypes.c_uint(0)
                char_rects = []
                ok = cls._lib.poppler_page_get_text_layout(page, ctypes.byref(rects_ptr), ctypes.byref(n_rects))
                if ok and rects_ptr and n_rects.value > 0:
                    try:
                        for i in range(n_rects.value):
                            r = rects_ptr[i]
                            char_rects.append((r.x1, r.y1, r.x2, r.y2))
                    finally:
                        cls._libglib.g_free(rects_ptr)

                return text, char_rects
            finally:
                cls._libgobject.g_object_unref(page)

    @classmethod
    def get_page_links(cls, doc_handle, page_index: int) -> List[dict]:
        """Extract interactive hyperlinks (URIs and internal page jumps) on the page in top-origin coordinates."""
        if not doc_handle or not cls.is_available():
            return []
        with cls._lock:
            page = cls._lib.poppler_document_get_page(doc_handle, page_index)
            if not page:
                return []
            try:
                w_c = ctypes.c_double()
                h_c = ctypes.c_double()
                cls._lib.poppler_page_get_size(page, ctypes.byref(w_c), ctypes.byref(h_c))
                page_h = h_c.value

                glist = cls._lib.poppler_page_get_link_mapping(page)
                if not glist:
                    return []
                links = []
                cur = glist
                while cur:
                    m = ctypes.cast(cur.contents.data, ctypes.POINTER(PopplerLinkMapping)).contents
                    x1 = min(m.area.x1, m.area.x2)
                    x2 = max(m.area.x1, m.area.x2)
                    y1 = min(page_h - m.area.y1, page_h - m.area.y2)
                    y2 = max(page_h - m.area.y1, page_h - m.area.y2)
                    area = (x1, y1, x2, y2)
                    if m.action:
                        act_any = ctypes.cast(m.action, ctypes.POINTER(PopplerActionAny)).contents
                        title = act_any.title.decode("utf-8", "ignore") if act_any.title else ""
                        if act_any.type == 5:  # POPPLER_ACTION_URI
                            act_uri = ctypes.cast(m.action, ctypes.POINTER(PopplerActionUri)).contents
                            uri = act_uri.uri.decode("utf-8", "ignore") if act_uri.uri else ""
                            if uri:
                                links.append({"rect": area, "type": "uri", "target": uri, "title": title})
                        elif act_any.type == 2:  # POPPLER_ACTION_GOTO_DEST
                            act_goto = ctypes.cast(m.action, ctypes.POINTER(PopplerActionGotoDest)).contents
                            if act_goto.dest:
                                d = act_goto.dest.contents
                                target_page = d.page_num
                                if d.named_dest:
                                    resolved = cls._lib.poppler_document_find_dest(doc_handle, d.named_dest)
                                    if resolved:
                                        target_page = resolved.contents.page_num
                                        cls._lib.poppler_dest_free(resolved)
                                links.append({"rect": area, "type": "page", "target": target_page, "title": title})
                    cur = cur.contents.next
                cls._lib.poppler_page_free_link_mapping(glist)
                return links
            finally:
                cls._libgobject.g_object_unref(page)

    @classmethod
    def get_pdf_bookmarks(cls, doc_handle, max_items: int = 4000, max_depth: int = 8) -> List["HeadingItem"]:
        """Walk embedded PDF bookmark tree via PopplerIndexIter and return HeadingItems."""
        if not doc_handle or not cls.is_available():
            return []
        items: List[HeadingItem] = []

        def _walk(it_ptr, level: int):
            while it_ptr and len(items) < max_items:
                act = cls._lib.poppler_index_iter_get_action(it_ptr)
                if act:
                    try:
                        act_any = ctypes.cast(act, ctypes.POINTER(PopplerActionAny)).contents
                        raw_title = act_any.title.decode("utf-8", errors="replace") if act_any.title else ""
                        title = re.sub(r"[\x00-\x1f]+", " ", raw_title).strip()
                        target_page = 0
                        if act_any.type == 2:  # POPPLER_ACTION_GOTO_DEST
                            act_goto = ctypes.cast(act, ctypes.POINTER(PopplerActionGotoDest)).contents
                            if act_goto.dest:
                                d = act_goto.dest.contents
                                target_page = d.page_num
                                if d.named_dest:
                                    resolved = cls._lib.poppler_document_find_dest(doc_handle, d.named_dest)
                                    if resolved:
                                        target_page = resolved.contents.page_num
                                        cls._lib.poppler_dest_free(resolved)
                        if title:
                            items.append(HeadingItem(title=title, line=1, level=level, page=max(0, target_page)))
                    finally:
                        cls._lib.poppler_action_free(act)

                if level < max_depth and len(items) < max_items:
                    child_it = cls._lib.poppler_index_iter_get_child(it_ptr)
                    if child_it:
                        try:
                            _walk(child_it, level + 1)
                        finally:
                            cls._lib.poppler_index_iter_free(child_it)

                if not cls._lib.poppler_index_iter_next(it_ptr):
                    break

        with cls._lock:
            root_it = cls._lib.poppler_index_iter_new(doc_handle)
            if not root_it:
                return []
            try:
                _walk(root_it, 1)
            finally:
                cls._lib.poppler_index_iter_free(root_it)
        return items

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

    @classmethod
    def _read_zip_member(cls, file_path: str, member_name: str) -> Optional[bytes]:
        """Read a file entry from a zip/odt/docx archive using zipfile with unzip/7z CLI fallbacks."""
        # 1. Try python's built-in zipfile
        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                if member_name in zf.namelist():
                    return zf.read(member_name)
        except Exception:
            pass

        # 2. Fallback to system 'unzip -p'
        if shutil.which("unzip"):
            try:
                proc = subprocess.run(
                    ["unzip", "-p", "--", file_path, member_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False
                )
                if proc.returncode == 0 and proc.stdout:
                    return proc.stdout
            except Exception:
                pass

        # 3. Fallback to system '7z e -so'
        if shutil.which("7z"):
            try:
                proc = subprocess.run(
                    ["7z", "e", "-so", "--", file_path, member_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False
                )
                if proc.returncode == 0 and proc.stdout:
                    return proc.stdout
            except Exception:
                pass

        return None

    @classmethod
    def extract_odt_text(cls, file_path: str) -> str:
        """Extract readable paragraphs and headings from an OpenDocument Text (.odt) archive."""
        try:
            xml_bytes = cls._read_zip_member(file_path, "content.xml")
            if not xml_bytes:
                return f"[Invalid or unreadable ODT file: {os.path.basename(file_path)}]"
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

    @classmethod
    def extract_docx_text(cls, file_path: str) -> str:
        """Extract readable paragraphs and headings from a Microsoft Word (.docx) archive."""
        try:
            xml_bytes = cls._read_zip_member(file_path, "word/document.xml")
            if not xml_bytes:
                return f"[Invalid or unreadable DOCX file: {os.path.basename(file_path)}]"
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
    def extract_outline(
        cls,
        text: str,
        doc_type: str = "markdown",
        pdf_page_count: int = 0,
        pdf_renderer: Optional["PDFPageRenderer"] = None,
    ) -> List[HeadingItem]:
        """Extract Table of Contents / Outline headings from document content."""
        items: List[HeadingItem] = []

        if doc_type == "pdf" and pdf_renderer is not None and pdf_renderer.page_count > 0:
            items, _ = pdf_renderer.get_outline_with_source(fallback_text=text)
            return items

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
        self._text_layout_cache = {}  # page_num: (text, [(x1,y1,x2,y2), ...])
        self._links_cache = {}  # page_num: [{"rect": (x1,y1,x2,y2), "type": ..., "target": ...}, ...]
        self._bookmarks_cache = None
        self._outline_cache = None
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

    def get_page_text_layout(self, page_num: int) -> Tuple[str, List[Tuple[float, float, float, float]]]:
        """Return (text, char_rects) for page_num (1-based). Rectangles are (x1, y1, x2, y2) in PDF points."""
        if not self.pdf_path or self.page_count == 0:
            return "", []
        page_num = max(1, min(page_num, self.page_count))
        with self._render_lock:
            if page_num in self._text_layout_cache:
                return self._text_layout_cache[page_num]

        if self._doc_handle and PopplerBridge.is_available():
            res = PopplerBridge.get_page_text_layout(self._doc_handle, page_num - 1)
            with self._render_lock:
                self._text_layout_cache[page_num] = res
            return res
        return "", []

    def get_page_links(self, page_num: int) -> List[dict]:
        """Return list of interactive hyperlinks (URIs and internal page jumps) on page_num (1-based)."""
        if not self.pdf_path or self.page_count == 0:
            return []
        page_num = max(1, min(page_num, self.page_count))
        with self._render_lock:
            if page_num in self._links_cache:
                return self._links_cache[page_num]

        if self._doc_handle and PopplerBridge.is_available():
            links = PopplerBridge.get_page_links(self._doc_handle, page_num - 1)
            with self._render_lock:
                self._links_cache[page_num] = links
            return links
        return []

    def render_page_surface(self, page_num: int, scale: float = 1.0) -> Optional[cairo.ImageSurface]:
        """Render page_num (1-based) directly to an in-memory cairo.ImageSurface (purely CPU-bound, thread safe)."""
        if not self.pdf_path or self.page_count == 0:
            return None
        page_num = max(1, min(page_num, self.page_count))
        if self._doc_handle and PopplerBridge.is_available():
            return PopplerBridge.render_page(self._doc_handle, page_num - 1, scale=scale * 1.333)
        return None

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
        surf = self.render_page_surface(page_num, scale)
        if surf is not None:
            w = surf.get_width()
            h = surf.get_height()
            try:
                pix = Gdk.pixbuf_get_from_surface(surf, 0, 0, w, h)
            except Exception:
                pix = None

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
        """Asynchronously render page in a background thread and safely create GdkPixbuf on the main GTK loop."""
        page_num = max(1, min(page_num, self.page_count)) if self.page_count > 0 else page_num
        scale_key = round(scale, 2)
        cache_key = (page_num, scale_key)

        with self._render_lock:
            if cache_key in self._pixbuf_cache:
                cached_pix = self._pixbuf_cache[cache_key]
                GLib.idle_add(callback, page_num, scale, cached_pix)
                return

        def worker():
            # In background worker: render purely in memory to cairo surface
            surf = self.render_page_surface(page_num, scale)

            def _on_main_thread():
                pix = None
                if surf is not None:
                    w = surf.get_width()
                    h = surf.get_height()
                    try:
                        pix = Gdk.pixbuf_get_from_surface(surf, 0, 0, w, h)
                    except Exception:
                        pix = None

                if pix is None:
                    # Fallback to pdftoppm on main thread
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

                callback(page_num, scale, pix)
                return False

            GLib.idle_add(_on_main_thread)

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

    _RE_TOC_CONTAINER = re.compile(
        r"^(Chapter|CHAPTER|Part|PART|Unit|UNIT|Module|MODULE|Section|SECTION|Appendix|APPENDIX|Book|BOOK)\s+([0-9IVXivxA-Z]+)\b[:.\-\s]*(.*)$"
    )
    _RE_TOC_DOTS = re.compile(r"^(.+?)\s*(?:\.{2,}|·{2,}|…+|\s{2,})\s*(\d{1,4})\s*$")
    _RE_TOC_TIGHT = re.compile(r"^(.+?[A-Za-z\)])\.(\d{1,4})\s*$")
    _RE_TOC_SUBSECTION = re.compile(r"^(\d+\.\d+|[A-Z]\.\d+)\s+")

    def get_bookmarks(self) -> List[HeadingItem]:
        """Return embedded PDF bookmarks from PopplerIndexIter, cached per document."""
        if not self.pdf_path or self.page_count == 0:
            return []
        with self._render_lock:
            if self._bookmarks_cache is not None:
                return self._bookmarks_cache
        if self._doc_handle and PopplerBridge.is_available():
            bms = PopplerBridge.get_pdf_bookmarks(self._doc_handle)
            with self._render_lock:
                self._bookmarks_cache = bms
            return bms
        return []

    @staticmethod
    def _split_columns_on_page(txt: str, rects: List[Tuple[float, float, float, float]], page_w: float) -> List[str]:
        """Split concatenated two-column PDF lines using horizontal character rect gaps."""
        segments = []
        cur: List[str] = []
        cur_x = 0.0
        cur_y = 0.0
        prev_x2 = None
        for ch, rc in zip(txt, rects):
            if ch == "\n":
                if cur:
                    segments.append((cur_x, cur_y, "".join(cur).strip()))
                cur = []
                prev_x2 = None
            else:
                if prev_x2 is not None and (rc[0] - prev_x2) > 16.0:
                    if cur:
                        segments.append((cur_x, cur_y, "".join(cur).strip()))
                    cur = []
                    cur_x = rc[0]
                    cur_y = rc[1]
                elif not cur:
                    cur_x = rc[0]
                    cur_y = rc[1]
                cur.append(ch)
                prev_x2 = rc[2]
        if cur:
            segments.append((cur_x, cur_y, "".join(cur).strip()))

        cleaned = [
            (x, y, re.sub(r"[\x00-\x1f]+", " ", s).strip())
            for x, y, s in segments
            if s.strip()
        ]
        mid = page_w * 0.45
        left = [(y, s) for x, y, s in cleaned if x < mid]
        right = [(y, s) for x, y, s in cleaned if x >= mid]
        left_alpha = sum(1 for _, s in left if re.search(r"[A-Za-z]{2,}", s))
        right_alpha = sum(1 for _, s in right if re.search(r"[A-Za-z]{2,}", s))
        if (len(left) >= 4 and len(right) >= 4) or (left_alpha >= 3 and right_alpha >= 3):
            left.sort(key=lambda t: t[0])
            right.sort(key=lambda t: t[0])
            return [s for _, s in left] + [s for _, s in right]
        return [s for _, _, s in cleaned]

    def _extract_toc_chapters(self) -> List[HeadingItem]:
        """Auto-populate chapters & sections from printed Table of Contents pages."""
        if not self._doc_handle or not PopplerBridge.is_available():
            return []
        max_scan = min(18, self.page_count)
        raw_items: List[HeadingItem] = []
        seen = set()
        toc_pages: List[int] = []
        started = False

        for p in range(1, max_scan + 1):
            txt, rects = self.get_page_text_layout(p)
            if not txt:
                continue
            pw, _ = self.get_page_size(p)
            lines = self._split_columns_on_page(txt, rects, pw)
            has_toc_kw = any(re.search(r"\b(table of contents|contents)\b", l, re.I) for l in lines[:6])
            matches = [l for l in lines if self._RE_TOC_DOTS.match(l) or self._RE_TOC_TIGHT.match(l)]

            if (has_toc_kw and len(matches) >= 2) or (started and len(matches) >= 4):
                page_items = []
                dup_count = 0
                idx = 0
                while idx < len(lines):
                    l = lines[idx]
                    idx += 1
                    if re.match(r"^(table of contents|contents|\d{1,4})$", l, re.I) or (
                        "table of contents" in l.lower() and not self._RE_TOC_DOTS.match(l)
                    ):
                        continue
                    m = self._RE_TOC_DOTS.match(l) or self._RE_TOC_TIGHT.match(l)
                    if m:
                        title = m.group(1).strip(" .·…")
                        pg = int(m.group(2))
                        if 1 <= pg <= self.page_count + 40 and len(title) >= 2:
                            is_chap = bool(
                                self._RE_TOC_CONTAINER.match(title)
                                or re.match(r"^(Answers|Index|Glossary|Appendix|Preface|Solutions|Bibliography)", title, re.I)
                            )
                            is_sub = bool(self._RE_TOC_SUBSECTION.match(title))
                            lvl = 1 if (is_chap or not is_sub) else 2
                            key = (title.lower(), pg)
                            if key in seen:
                                dup_count += 1
                            else:
                                page_items.append((key, HeadingItem(title=title, line=1, level=lvl, page=pg)))
                    elif self._RE_TOC_CONTAINER.match(l):
                        if idx < len(lines):
                            nxt = lines[idx]
                            m_nxt = self._RE_TOC_DOTS.match(nxt) or self._RE_TOC_TIGHT.match(nxt)
                            if m_nxt and not self._RE_TOC_CONTAINER.match(nxt) and not self._RE_TOC_SUBSECTION.match(nxt):
                                combined = f"{l} {m_nxt.group(1).strip(' .·…')}"
                                pg = int(m_nxt.group(2))
                                idx += 1
                                key = (combined.lower(), pg)
                                if key in seen:
                                    dup_count += 1
                                else:
                                    page_items.append((key, HeadingItem(title=combined, line=1, level=1, page=pg)))
                                continue
                        key = (l.lower(), 0)
                        if key in seen:
                            dup_count += 1
                        else:
                            page_items.append((key, HeadingItem(title=l, line=1, level=1, page=0)))

                if started and dup_count > len(page_items):
                    break
                started = True
                toc_pages.append(p)
                for key, it in page_items:
                    seen.add(key)
                    raw_items.append(it)
            elif started:
                break

        if len(raw_items) < 2:
            return []

        # Calibrate printed page number vs physical PDF page offset
        votes = {}
        toc_end_page = toc_pages[-1]
        for item in raw_items:
            if item.page > 0 and sum(votes.values()) < 5:
                words = [
                    w for w in re.findall(r"[A-Za-z]{4,}", item.title)
                    if w.lower() not in ("chapter", "section", "module", "part", "unit", "introduction", "application")
                ]
                kw = words[0].lower() if words else ""
                if kw:
                    for off in [0] + list(range(1, 35)) + list(range(-1, -6, -1)):
                        cand_p = item.page + off
                        if toc_end_page < cand_p <= self.page_count:
                            ptxt, _ = self.get_page_text_layout(cand_p)
                            top_txt = "\n".join(ptxt.splitlines()[:10]).lower()
                            if kw in top_txt:
                                votes[off] = votes.get(off, 0) + 1
                                break
        offset = max(votes, key=votes.get) if votes else 0

        for item in raw_items:
            if item.page > 0:
                item.page = max(1, min(self.page_count, item.page + offset))

        for i, item in enumerate(raw_items):
            if item.page == 0:
                for j in range(i + 1, len(raw_items)):
                    if raw_items[j].page > 0:
                        cand = raw_items[j].page
                        if cand > 1:
                            prev_txt, _ = self.get_page_text_layout(cand - 1)
                            prefix_kw = item.title.split(":")[0].strip().lower()
                            if prefix_kw and prefix_kw in prev_txt.lower():
                                cand = cand - 1
                        item.page = cand
                        break
                if item.page == 0:
                    item.page = 1

        return raw_items

    def _extract_body_chapters(self, fallback_text: str = "") -> List[HeadingItem]:
        """Detect explicit Chapter / Module / Unit / Part headings in body pages."""
        pages = fallback_text.split("\x0c") if fallback_text else []
        if not pages and self.page_count <= 120:
            pages = [self.get_page_text_layout(p)[0] for p in range(1, self.page_count + 1)]
        if not pages:
            return []

        re_chap = re.compile(
            r"^(?:CHAPTER|Chapter)\s+(?:\d+|[IVXLCDM]+|One|Two|Three|Four|Five|Six|Seven|Eight|Nine|Ten)\b(?:[:.\-\s]+.*)?$"
        )
        re_unit = re.compile(
            r"^(?:PART|Part|MODULE|Module|UNIT|Unit|BOOK|Book|LESSON|Lesson)\s+[A-Z0-9]+\b(?:[:.\-\s]+.*)?$"
        )
        re_app = re.compile(r"^(?:APPENDIX|Appendix)(?:\s+[A-Z0-9]+)?\b(?:[:.\-\s]+.*)?$")

        items: List[HeadingItem] = []
        seen_titles = set()
        for idx, page_txt in enumerate(pages[:self.page_count], start=1):
            lines = [re.sub(r"[\x00-\x1f]+", " ", l).strip() for l in page_txt.splitlines() if l.strip()]
            for li, line in enumerate(lines[:10]):
                if len(line) > 100:
                    continue
                if re_chap.match(line) or re_unit.match(line) or re_app.match(line):
                    title = line
                    # Join wrapped title if the line ends with ':' or is just 'Chapter N'
                    if (title.endswith(":") or len(title.split()) <= 2) and li + 1 < len(lines):
                        nxt = lines[li + 1]
                        if 3 <= len(nxt) <= 80 and not nxt.isdigit():
                            title = f"{title} {nxt}".replace(": ", ": ").strip()
                    norm_key = re.sub(r"\s+", " ", title.lower())
                    if norm_key not in seen_titles:
                        seen_titles.add(norm_key)
                        items.append(HeadingItem(title=title, line=1, level=1, page=idx))
                    break
        return items if len(items) >= 2 else []

    def _extract_font_headings(self) -> List[HeadingItem]:
        """Detect large-font structural headings when PDF lacks bookmarks, TOC, and 'Chapter' labels."""
        if not self._doc_handle or not PopplerBridge.is_available() or self.page_count > 120:
            return []

        all_heights: List[float] = []
        page_lines: List[Tuple[int, str, float]] = []

        for p in range(1, self.page_count + 1):
            txt, rects = self.get_page_text_layout(p)
            if not txt or not rects:
                continue
            cur_c: List[str] = []
            cur_h: List[float] = []
            for ch, rc in zip(txt, rects):
                if ch == "\n":
                    if cur_c and cur_h:
                        t = re.sub(r"[\x00-\x1f]+", " ", "".join(cur_c)).strip()
                        avg_h = sum(cur_h) / len(cur_h)
                        if t:
                            page_lines.append((p, t, avg_h))
                            all_heights.extend(cur_h)
                    cur_c, cur_h = [], []
                elif rc[3] > rc[1]:
                    cur_c.append(ch)
                    cur_h.append(rc[3] - rc[1])
            if cur_c and cur_h:
                t = re.sub(r"[\x00-\x1f]+", " ", "".join(cur_c)).strip()
                avg_h = sum(cur_h) / len(cur_h)
                if t:
                    page_lines.append((p, t, avg_h))
                    all_heights.extend(cur_h)

        if not all_heights:
            return []
        sorted_h = sorted(all_heights)
        med_h = sorted_h[len(sorted_h) // 2]
        if med_h <= 1.0:
            return []

        # Count occurrences across pages to suppress repeating running headers
        freq = {}
        for _, t, h in page_lines:
            if h >= med_h * 1.18:
                freq[t.lower()] = freq.get(t.lower(), 0) + 1

        max_repeat = max(2, int(self.page_count * 0.35))
        items: List[HeadingItem] = []
        seen = set()
        for p, t, h in page_lines:
            if h < med_h * 1.18 or len(t) < 3 or len(t) > 95:
                continue
            if t.isdigit() or not any(c.isalpha() for c in t):
                continue
            if self.page_count > 3 and freq.get(t.lower(), 0) >= max_repeat:
                continue
            key = (t.lower(), p)
            if key in seen:
                continue
            seen.add(key)
            lvl = 1 if (h >= med_h * 1.32 or re.match(r"^(\d+\.\s|[◆■●▸Chapter|Module|Part])", t)) else 2
            items.append(HeadingItem(title=t, line=1, level=lvl, page=p))

        return items if (len(items) >= 2 or (len(items) == 1 and self.page_count <= 3)) else []

    def get_outline_with_source(self, fallback_text: str = "") -> Tuple[List[HeadingItem], str]:
        """
        Return (HeadingItems, source_type) where source_type is one of:
        'bookmarks' | 'auto-chapters' | 'pages'
        """
        if not self.pdf_path or self.page_count == 0:
            return [], "pages"

        with self._render_lock:
            if self._outline_cache is not None:
                return self._outline_cache

        # Tier 1: Native embedded PDF bookmarks
        bms = self.get_bookmarks()
        if len(bms) >= 2 or (len(bms) == 1 and self.page_count <= 5):
            res = (bms, "bookmarks")
            with self._render_lock:
                self._outline_cache = res
            return res

        # Tier 2A: Blank bookmarks -> Autopopulate from Table of Contents
        toc_items = self._extract_toc_chapters()
        if len(toc_items) >= 2:
            res = (toc_items, "auto-chapters")
            with self._render_lock:
                self._outline_cache = res
            return res

        # Tier 2B: Blank bookmarks -> Autopopulate from Chapter/Module/Unit body headers
        body_items = self._extract_body_chapters(fallback_text=fallback_text)
        if len(body_items) >= 2:
            res = (body_items, "auto-chapters")
            with self._render_lock:
                self._outline_cache = res
            return res

        # Tier 2C: Blank bookmarks -> Autopopulate from large-font typography headings
        font_items = self._extract_font_headings()
        if font_items:
            res = (font_items, "auto-chapters")
            with self._render_lock:
                self._outline_cache = res
            return res

        # Tier 3: Page-by-page markers fallback
        pages = fallback_text.split("\x0c") if fallback_text else []
        fallback_items: List[HeadingItem] = []
        for idx in range(self.page_count):
            snippet = ""
            if idx < len(pages):
                for line in pages[idx].splitlines():
                    s = line.strip()
                    if len(s) >= 3:
                        snippet = f" — {s[:42]}"
                        break
            fallback_items.append(HeadingItem(title=f"Page {idx + 1}{snippet}", line=1, level=1, page=idx + 1))

        res = (fallback_items, "pages")
        with self._render_lock:
            self._outline_cache = res
        return res

    def cleanup_doc(self):
        with self._render_lock:
            self._pixbuf_cache.clear()
            self._page_sizes.clear()
            self._text_layout_cache.clear()
            self._links_cache.clear()
            self._bookmarks_cache = None
            self._outline_cache = None
            if self._doc_handle and PopplerBridge.is_available():
                PopplerBridge.close_document(self._doc_handle)
                self._doc_handle = None

    def cleanup(self):
        self.cleanup_doc()
        if self.temp_dir and os.path.isdir(self.temp_dir):
            shutil.rmtree(self.temp_dir, ignore_errors=True)
