#!/usr/bin/env python3
"""
Unit and Integration Tests for DocPad
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
        md = "# DocPad Header\n\nParagraph with **bold** and `code`.\n\n| Col1 | Col2 |\n|---|---|\n| A | B |\n"
        html_out = DocumentParser.render_markdown_to_html(md, doc_type="markdown")
        self.assertIn("<h1>DocPad Header</h1>", html_out)
        self.assertIn("<strong>bold</strong>", html_out)
        self.assertIn("<code>code</code>", html_out)
        self.assertIn("<table>", html_out)

    def test_csv_to_html_rendering(self):
        csv_text = "Name,Role\nAlice,Engineer\nBob,Designer\n"
        html_out = DocumentParser.render_markdown_to_html(csv_text, doc_type="csv", file_path="team.csv")
        self.assertIn("<th>Name</th>", html_out)
        self.assertIn("<td>Engineer</td>", html_out)


class TestViewerAndAssets(unittest.TestCase):
    """Test DocViewer configuration, style scheme, and SVG/desktop assets."""

    def setUp(self):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))

    def test_zoom_steps(self):
        steps = DocViewer.ZOOM_STEPS
        self.assertIn(1.0, steps)
        self.assertEqual(steps[DocViewer.DEFAULT_ZOOM_INDEX], 1.00)
        self.assertTrue(all(steps[i] < steps[i + 1] for i in range(len(steps) - 1)))

    def test_docpad_scheme_available(self):
        manager = GtkSource.StyleSchemeManager.get_default()
        self.assertIn("docpad-neon", manager.get_scheme_ids())

    def test_svg_and_watermark_validity(self):
        svg_path = os.path.join(self.base_dir, "docpad.svg")
        self.assertTrue(os.path.exists(svg_path))
        tree = ET.parse(svg_path)
        self.assertTrue(tree.getroot().tag.endswith("svg"))
        self.assertEqual(tree.getroot().attrib.get("viewBox"), "0 0 512 512")
        self.assertIsNotNone(DocViewer._get_watermark_pixbuf())

    def test_desktop_and_scripts(self):
        desktop_path = os.path.join(self.base_dir, "docpad.desktop")
        self.assertTrue(os.path.exists(desktop_path))
        with open(desktop_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Name=DocPad", content)
        self.assertIn("StartupWMClass=docpad", content)


if __name__ == "__main__":
    unittest.main()
