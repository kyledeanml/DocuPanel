#!/usr/bin/env python3
"""
Unit and Integration Tests for DocuPad
Tests document parsing, Markdown/CSV HTML rendering, outline extraction,
Retina zoom steps, and SVG / desktop asset integrity.
"""

import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
import gi

gi.require_version('GtkSource', '4')
from gi.repository import GtkSource

from doc_tools import DocumentParser
from viewer import DocViewer


class TestDocumentParserAndOutline(unittest.TestCase):
    """Test Markdown/CSV/JSON parsing, HTML rendering, and TOC outline extraction."""

    def test_markdown_outline_extraction(self):
        md = "# Main Title\nSome intro text.\n## Section One\n```python\n# comment inside code\n```\n### Subsection A\n"
        outline = DocumentParser.extract_outline(md, doc_type="markdown")
        self.assertEqual(len(outline), 3)
        self.assertEqual(outline[0].title, "Main Title")
        self.assertEqual(outline[0].level, 1)
        self.assertEqual(outline[1].title, "Section One")
        self.assertEqual(outline[1].level, 2)
        self.assertEqual(outline[2].title, "Subsection A")
        self.assertEqual(outline[2].level, 3)

    def test_markdown_to_html_rendering(self):
        md = "# DocuPad Header\n\nParagraph with **bold** and `code`.\n\n| Col1 | Col2 |\n|---|---|\n| A | B |\n"
        html_out = DocumentParser.render_markdown_to_html(md, doc_type="markdown")
        self.assertIn("<h1>DocuPad Header</h1>", html_out)
        self.assertIn("<strong>bold</strong>", html_out)
        self.assertIn("<code>code</code>", html_out)
        self.assertIn("<table>", html_out)

    def test_csv_to_html_rendering(self):
        csv_text = "Name,Role\nAlice,Engineer\nBob,Designer\n"
        html_out = DocumentParser.render_markdown_to_html(csv_text, doc_type="csv", file_path="team.csv")
        self.assertIn("<th>Name</th>", html_out)
        self.assertIn("<td>Engineer</td>", html_out)

    def test_docx_zlib_compressed_extraction(self):
        import zipfile
        xml = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:body>'
            b'<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Resume Title</w:t></w:r></w:p>'
            b'<w:p><w:r><w:t>Body paragraph text.</w:t></w:r></w:p>'
            b'</w:body></w:document>'
        )
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("word/document.xml", xml)
            extracted = DocumentParser.extract_docx_text(tmp_path)
            self.assertIn("# Resume Title", extracted)
            self.assertIn("Body paragraph text.", extracted)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


class TestViewerAndAssets(unittest.TestCase):
    """Test DocViewer configuration, style scheme, and SVG/desktop assets."""

    def setUp(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))

    def test_zoom_steps(self):
        steps = DocViewer.ZOOM_STEPS
        self.assertIn(1.0, steps)
        self.assertEqual(steps[DocViewer.DEFAULT_ZOOM_INDEX], 1.00)
        self.assertTrue(all(steps[i] < steps[i + 1] for i in range(len(steps) - 1)))

    def test_ctrl_mouse_wheel_scroll_zoom(self):
        from gi.repository import Gdk
        dummy = type("DummyViewer", (), {
            "_last_scroll_zoom_time": 0,
            "zoomed": [],
            "zoom_in": lambda self: self.zoomed.append("in"),
            "zoom_out": lambda self: self.zoomed.append("out"),
        })()

        # Test SMOOTH scroll up (3-tuple from get_scroll_deltas)
        ev_up = Gdk.Event.new(Gdk.EventType.SCROLL)
        ev_up.scroll.direction = Gdk.ScrollDirection.SMOOTH
        ev_up.scroll.state = Gdk.ModifierType.CONTROL_MASK
        ev_up.scroll.delta_y = -1.0
        ev_up.scroll.time = 101
        self.assertTrue(DocViewer._on_scroll_event(dummy, None, ev_up))
        self.assertEqual(dummy.zoomed, ["in"])

        # Duplicate event with identical X11 timestamp should be deduplicated
        self.assertTrue(DocViewer._on_scroll_event(dummy, None, ev_up))
        self.assertEqual(dummy.zoomed, ["in"])

        # Test SMOOTH scroll down
        ev_down = Gdk.Event.new(Gdk.EventType.SCROLL)
        ev_down.scroll.direction = Gdk.ScrollDirection.SMOOTH
        ev_down.scroll.state = Gdk.ModifierType.CONTROL_MASK
        ev_down.scroll.delta_y = 1.0
        ev_down.scroll.time = 102
        self.assertTrue(DocViewer._on_scroll_event(dummy, None, ev_down))
        self.assertEqual(dummy.zoomed, ["in", "out"])

    def test_docupad_scheme_available(self):
        manager = GtkSource.StyleSchemeManager.get_default()
        self.assertIn("docupad-neon", manager.get_scheme_ids())

    def test_svg_and_watermark_validity(self):
        svg_path = os.path.join(self.base_dir, "docupad.svg")
        self.assertTrue(os.path.exists(svg_path))
        tree = ET.parse(svg_path)
        self.assertTrue(tree.getroot().tag.endswith("svg"))
        self.assertEqual(tree.getroot().attrib.get("viewBox"), "0 0 512 512")
        self.assertIsNotNone(DocViewer._get_watermark_pixbuf())

    def test_desktop_and_scripts(self):
        desktop_path = os.path.join(self.base_dir, "docupad.desktop")
        self.assertTrue(os.path.exists(desktop_path))
        with open(desktop_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Name=DocuPad", content)
        self.assertIn("StartupWMClass=docupad", content)


if __name__ == "__main__":
    unittest.main()
