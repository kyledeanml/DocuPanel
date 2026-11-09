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

    def test_docx_and_odt_unzip_fallback_on_missing_zlib(self):
        """Verify DOCX and ODT fallback to system unzip/7z when python's zipfile raises missing zlib error."""
        from unittest.mock import patch
        import zipfile
        xml_docx = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b'<w:body>'
            b'<w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Professional Experience</w:t></w:r></w:p>'
            b'<w:p><w:r><w:t>Developed Linux Mint desktop applications.</w:t></w:r></w:p>'
            b'</w:body></w:document>'
        )
        xml_odt = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0">'
            b'<office:body><office:text>'
            b'<h>Project Overview</h>'
            b'<p>Summary of achievements.</p>'
            b'</office:text></office:body></office:document-content>'
        )

        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp_d:
            docx_path = tmp_d.name
        with tempfile.NamedTemporaryFile(suffix=".odt", delete=False) as tmp_o:
            odt_path = tmp_o.name

        try:
            with zipfile.ZipFile(docx_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("word/document.xml", xml_docx)
            with zipfile.ZipFile(odt_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                zf.writestr("content.xml", xml_odt)

            def mock_zipfile_init(*args, **kwargs):
                raise RuntimeError("Compression requires the (missing) zlib module")

            with patch("zipfile.ZipFile", side_effect=mock_zipfile_init):
                extracted_docx = DocumentParser.extract_docx_text(docx_path)
                self.assertIn("## Professional Experience", extracted_docx)
                self.assertIn("Developed Linux Mint desktop applications.", extracted_docx)

                extracted_odt = DocumentParser.extract_odt_text(odt_path)
                self.assertIn("## Project Overview", extracted_odt)
                self.assertIn("Summary of achievements.", extracted_odt)
        finally:
            if os.path.exists(docx_path):
                os.remove(docx_path)
            if os.path.exists(odt_path):
                os.remove(odt_path)


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

    def test_pdf_renderer_and_poppler_bridge(self):
        from doc_tools import PDFPageRenderer, PopplerBridge
        self.assertTrue(PopplerBridge.is_available())

        # Test with available system PDF if present, or create minimal PDF
        pdf_path = "/home/bluechile/Games/Heroic/PillarsOfEternity/CollectorsBook/pe-collectors-book.pdf"
        if not os.path.isfile(pdf_path):
            import cairo
            pdf_path = os.path.join(tempfile.gettempdir(), "test_docupad.pdf")
            surf = cairo.PDFSurface(pdf_path, 200, 200)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0, 0, 0)
            cr.show_text("DocuPad Test")
            surf.show_page()
            surf.finish()

        renderer = PDFPageRenderer(pdf_path)
        self.assertGreater(renderer.page_count, 0)
        pix = renderer.render_page_pixbuf(1, scale=1.0)
        self.assertIsNotNone(pix)
        self.assertGreater(pix.get_width(), 0)
        self.assertGreater(pix.get_height(), 0)

        # Test zoom scaling calculation
        pix_zoomed = renderer.render_page_pixbuf(1, scale=1.5)
        self.assertIsNotNone(pix_zoomed)
        self.assertGreater(pix_zoomed.get_width(), pix.get_width())

        renderer.cleanup()

    def test_pdf_renderer_filename_with_spaces_and_sync_load(self):
        """Test PDF with spaces in filename and ensure viewer load_file populates pixbuf immediately."""
        import cairo
        from doc_tools import PDFPageRenderer, PopplerBridge

        tmp_dir = tempfile.mkdtemp(prefix="docupad test spaces ")
        pdf_path = os.path.join(tmp_dir, "Mini Book Pages - 188 x 171 With cover HR3.pdf")
        try:
            surf = cairo.PDFSurface(pdf_path, 300, 400)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0.1, 0.2, 0.4)
            cr.paint()
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
            cr.set_font_size(24)
            cr.move_to(30, 80)
            cr.show_text("Death Stranding Mini Book")
            surf.show_page()
            surf.finish()

            # 1. Test PopplerBridge direct open with spaces
            doc_handle = PopplerBridge.open_document(pdf_path)
            self.assertIsNotNone(doc_handle)
            self.assertEqual(PopplerBridge.get_page_count(doc_handle), 1)

            # 2. Test PDFPageRenderer surface & pixbuf
            renderer = PDFPageRenderer(pdf_path)
            self.assertEqual(renderer.page_count, 1)
            surf_rendered = renderer.render_page_surface(1, scale=1.0)
            self.assertIsNotNone(surf_rendered)
            pix = renderer.render_page_pixbuf(1, scale=1.0)
            self.assertIsNotNone(pix)
            self.assertGreater(pix.get_width(), 100)
            renderer.cleanup()

            # 3. Test DocViewer load_file populates pdf_image pixbuf when display is present
            from gi.repository import Gtk
            has_display, _ = Gtk.init_check()
            if has_display:
                viewer = DocViewer()
                loaded = viewer.load_file(pdf_path)
                self.assertTrue(loaded)
                self.assertEqual(viewer.get_view_mode(), "pdf")
                self.assertIsNotNone(viewer.pdf_image.get_pixbuf())
                self.assertGreater(viewer.pdf_image.get_pixbuf().get_width(), 100)
                if viewer.pdf_renderer:
                    viewer.pdf_renderer.cleanup()
        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                    os.rmdir(tmp_dir)
                except OSError:
                    pass


if __name__ == "__main__":
    unittest.main()
