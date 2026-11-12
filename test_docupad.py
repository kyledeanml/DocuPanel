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

import cairo

sys.dont_write_bytecode = True
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkSource', '4')
from gi.repository import Gtk, Gdk, GtkSource

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

            # Test render_page_async
            async_called = []
            renderer.render_page_async(1, 1.0, lambda p, s, px: async_called.append(px))
            import time
            from gi.repository import GLib
            ctx = GLib.main_context_default()
            for _ in range(50):
                time.sleep(0.01)
                while ctx.pending():
                    ctx.iteration(False)
                if async_called:
                    break
            self.assertTrue(len(async_called) > 0)
            renderer.cleanup()

            # 3. Test DocViewer load_file populates pdf_image pixbuf when display is present
            from gi.repository import Gtk, Gdk
            has_display, _ = Gtk.init_check()
            if has_display:
                viewer = DocViewer()
                loaded = viewer.load_file(pdf_path)
                self.assertTrue(loaded)
                self.assertEqual(viewer.get_view_mode(), "pdf")
                self.assertIsNotNone(viewer.pdf_image.get_pixbuf())
                self.assertGreater(viewer.pdf_image.get_pixbuf().get_width(), 100)
                # Verify clean tooltips with no broken PageUp/PageDown references
                self.assertEqual(viewer.btn_pdf_prev.get_tooltip_text(), "Previous Page")
                self.assertEqual(viewer.btn_pdf_next.get_tooltip_text(), "Next Page")
                if viewer.pdf_renderer:
                    viewer.pdf_renderer.cleanup()
        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                    os.rmdir(tmp_dir)
                except OSError:
                    pass

    def test_pdf_mouse_wheel_and_keyboard_page_navigation(self):
        """Test simple mouse wheel scroll up/down and PageUp/PageDown keyboard navigation."""
        import cairo
        from gi.repository import Gtk, Gdk
        has_display, _ = Gtk.init_check()
        if not has_display:
            self.skipTest("Requires display connection for GtkWidget test")

        tmp_dir = tempfile.mkdtemp(prefix="docupad_multipage_")
        pdf_path = os.path.join(tmp_dir, "multipage_book.pdf")
        try:
            # Create a 3-page PDF
            surf = cairo.PDFSurface(pdf_path, 400, 300)
            cr = cairo.Context(surf)
            for p in range(1, 4):
                cr.set_source_rgb(0.1 * p, 0.2 * p, 0.3 * p)
                cr.paint()
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(32)
                cr.move_to(50, 100)
                cr.show_text(f"Page {p}")
                surf.show_page()
            surf.finish()

            viewer = DocViewer()
            viewer.load_file(pdf_path)
            self.assertEqual(viewer.current_pdf_page, 1)
            self.assertEqual(viewer.pdf_renderer.page_count, 3)

            # Helper to create mock event
            class MockEvent:
                def __init__(self, **kwargs):
                    for k, v in kwargs.items():
                        setattr(self, k, v)

            # 1. Test mouse wheel scroll DOWN changes to page 2
            ev_down = MockEvent(
                state=0,
                time=1000,
                direction=Gdk.ScrollDirection.DOWN
            )
            handled = viewer._on_scroll_event(viewer, ev_down)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 2)

            # 2. Test mouse wheel scroll DOWN changes to page 3
            ev_down2 = MockEvent(
                state=0,
                time=1200,
                direction=Gdk.ScrollDirection.DOWN
            )
            viewer._on_scroll_event(viewer, ev_down2)
            self.assertEqual(viewer.current_pdf_page, 3)

            # 3. Test mouse wheel scroll UP changes back to page 2
            ev_up = MockEvent(
                state=0,
                time=1400,
                direction=Gdk.ScrollDirection.UP
            )
            handled = viewer._on_scroll_event(viewer, ev_up)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 2)

            # 4. Test smooth mouse wheel scroll DOWN (delta_y > 0)
            ev_smooth_down = MockEvent(
                state=0,
                time=1600,
                direction=Gdk.ScrollDirection.SMOOTH,
                delta_y=0.8,
                get_scroll_deltas=lambda: (True, 0.0, 0.8)
            )
            viewer._on_scroll_event(viewer, ev_smooth_down)
            self.assertEqual(viewer.current_pdf_page, 3)

            # 5. Test Keyboard Page_Up changes back to page 2
            ev_key_pup = MockEvent(
                state=0,
                keyval=Gdk.KEY_Page_Up
            )
            handled = viewer._on_key_press(viewer, ev_key_pup)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 2)

            # 6. Test Keyboard Page_Down changes to page 3
            ev_key_pdown = MockEvent(
                state=0,
                keyval=Gdk.KEY_Page_Down
            )
            handled = viewer._on_key_press(viewer, ev_key_pdown)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 3)

            # 7. Test Keyboard Home jumps to page 1
            ev_key_home = MockEvent(
                state=0,
                keyval=Gdk.KEY_Home
            )
            handled = viewer._on_key_press(viewer, ev_key_home)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 1)

            # 8. Test Keyboard End jumps to page 3
            ev_key_end = MockEvent(
                state=0,
                keyval=Gdk.KEY_End
            )
            handled = viewer._on_key_press(viewer, ev_key_end)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 3)

            # 9. Test Keyboard Left arrow moves to page 2
            ev_key_left = MockEvent(
                state=0,
                keyval=Gdk.KEY_Left
            )
            handled = viewer._on_key_press(viewer, ev_key_left)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 2)

            # 10. Test Keyboard Right arrow moves to page 3
            ev_key_right = MockEvent(
                state=0,
                keyval=Gdk.KEY_Right
            )
            handled = viewer._on_key_press(viewer, ev_key_right)
            self.assertTrue(handled)
            self.assertEqual(viewer.current_pdf_page, 3)

            if viewer.pdf_renderer:
                viewer.pdf_renderer.cleanup()
        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                    os.rmdir(tmp_dir)
                except OSError:
                    pass

    def test_pdf_mouse_cursor_grip_and_panning_when_zoomed_in(self):
        """Test mouse cursor grip, grabbing cursor, and smooth adjustment panning when zoomed in."""
        import cairo
        from gi.repository import Gtk, Gdk
        has_display, _ = Gtk.init_check()
        if not has_display:
            self.skipTest("Requires display connection for GtkWidget test")

        tmp_dir = tempfile.mkdtemp(prefix="docupad_pan_")
        pdf_path = os.path.join(tmp_dir, "large_page.pdf")
        try:
            # Create a large page PDF
            surf = cairo.PDFSurface(pdf_path, 1200, 1600)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0.9, 0.9, 0.9)
            cr.paint()
            surf.show_page()
            surf.finish()

            viewer = DocViewer()
            viewer.load_file(pdf_path)
            self.assertEqual(viewer.get_view_mode(), "pdf")

            # Check pannability
            viewer.zoom_index = DocViewer.DEFAULT_ZOOM_INDEX
            self.assertFalse(viewer.zoom_index > DocViewer.DEFAULT_ZOOM_INDEX)

            # Zoom in
            viewer.zoom_in()
            self.assertGreater(viewer.zoom_index, DocViewer.DEFAULT_ZOOM_INDEX)
            self.assertTrue(viewer._is_pdf_pannable())

            # Configure adjustments for test
            vadj = viewer.scrolled_pdf.get_vadjustment()
            hadj = viewer.scrolled_pdf.get_hadjustment()
            vadj.set_lower(0.0)
            vadj.set_upper(2000.0)
            vadj.set_page_size(500.0)
            vadj.set_value(100.0)

            hadj.set_lower(0.0)
            hadj.set_upper(1500.0)
            hadj.set_page_size(400.0)
            hadj.set_value(50.0)

            class MockButtonEvent:
                def __init__(self, button, x_root, y_root):
                    self.button = button
                    self.x_root = x_root
                    self.y_root = y_root

                def get_root_coords(self):
                    return True, self.x_root, self.y_root

            # 1. Button Press (Left Click to Grip)
            press_ev = MockButtonEvent(1, 300.0, 400.0)
            handled = viewer._on_pan_button_press(viewer.pdf_event_box, press_ev)
            self.assertTrue(handled)
            self.assertTrue(viewer._is_panning)
            self.assertEqual(viewer._pan_start_x, 300.0)
            self.assertEqual(viewer._pan_start_y, 400.0)

            # 2. Motion Notify (Drag mouse diagonally: move right 30px, up 40px)
            # Dragging right pulls page right -> hadj decreases by 30 (from 50 -> 20)
            # Dragging up pushes page up -> vadj increases by 40 (from 100 -> 140)
            motion_ev = MockButtonEvent(1, 330.0, 360.0)
            handled = viewer._on_pan_motion_notify(viewer.pdf_event_box, motion_ev)
            self.assertTrue(handled)
            self.assertTrue(viewer._pan_has_moved)
            self.assertAlmostEqual(hadj.get_value(), 20.0)
            self.assertAlmostEqual(vadj.get_value(), 140.0)

            # 3. Button Release (Release Grip)
            release_ev = MockButtonEvent(1, 330.0, 360.0)
            handled = viewer._on_pan_button_release(viewer.pdf_event_box, release_ev)
            self.assertTrue(handled)
            self.assertFalse(viewer._is_panning)

            if viewer.pdf_renderer:
                viewer.pdf_renderer.cleanup()
        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                    os.rmdir(tmp_dir)
                except OSError:
                    pass

    def test_retina_zoom_accuracy_and_display(self):
        """Test exact Retina zoom rounding without truncation and HeaderBar / PDF / status label consistency."""
        from gi.repository import Gtk
        has_display, _ = Gtk.init_check()
        if not has_display:
            self.skipTest("Requires display connection for DocuPadWindow test")

        from docupad import DocuPadWindow
        win = DocuPadWindow()
        viewer = win.get_current_viewer()
        self.assertIsNotNone(viewer)

        # Check all zoom steps percent representations
        expected_percents = [int(round(s * 100)) for s in DocViewer.ZOOM_STEPS]
        for idx, expected in enumerate(expected_percents):
            viewer.zoom_index = idx
            pct = viewer.get_zoom_percent()
            self.assertEqual(pct, expected)
            self.assertNotIn(".", str(pct))

        # Check steps that previously truncated: 70%, 90%, 140%, 155%
        # 0.70 -> 70 (not 69)
        viewer.zoom_index = DocViewer.ZOOM_STEPS.index(0.70)
        self.assertEqual(viewer.get_zoom_percent(), 70)
        win._update_zoom_ui(viewer)
        self.assertEqual(win.lbl_zoom.get_text(), "Retina: 70%")
        self.assertEqual(win.btn_zoom_reset.get_label(), "70%")

        # 0.90 -> 90 (not 89)
        viewer.zoom_index = DocViewer.ZOOM_STEPS.index(0.90)
        self.assertEqual(viewer.get_zoom_percent(), 90)
        win._update_zoom_ui(viewer)
        self.assertEqual(win.lbl_zoom.get_text(), "Retina: 90%")
        self.assertEqual(win.btn_zoom_reset.get_label(), "90%")

        # 1.40 -> 140 (not 139)
        viewer.zoom_index = DocViewer.ZOOM_STEPS.index(1.40)
        self.assertEqual(viewer.get_zoom_percent(), 140)
        win._update_zoom_ui(viewer)
        self.assertEqual(win.lbl_zoom.get_text(), "Retina: 140%")
        self.assertEqual(win.btn_zoom_reset.get_label(), "140%")

        # Sensitivity at zoom bounds
        viewer.zoom_index = 0
        win._update_zoom_ui(viewer)
        self.assertFalse(viewer.can_zoom_out())
        self.assertTrue(viewer.can_zoom_in())
        self.assertFalse(win.btn_zoom_out.get_sensitive())
        self.assertTrue(win.btn_zoom_in.get_sensitive())

        viewer.zoom_index = len(DocViewer.ZOOM_STEPS) - 1
        win._update_zoom_ui(viewer)
        self.assertTrue(viewer.can_zoom_out())
        self.assertFalse(viewer.can_zoom_in())
        self.assertTrue(win.btn_zoom_out.get_sensitive())
        self.assertFalse(win.btn_zoom_in.get_sensitive())

        # Reset zoom
        win.zoom_active(reset=True)
        self.assertEqual(viewer.get_zoom_percent(), 100)
        self.assertEqual(win.lbl_zoom.get_text(), "Retina: 100%")
        self.assertEqual(win.btn_zoom_reset.get_label(), "100%")
        self.assertTrue(win.btn_zoom_out.get_sensitive())
        self.assertTrue(win.btn_zoom_in.get_sensitive())

        win.destroy()

    def test_start_in_full_page_view_on_double_click_pdf_and_compatible(self):
        """Test DocuPad starting in full page view (maximized, sidebar hidden, fit page) when opening PDF/compatible files."""
        import cairo, time
        from gi.repository import Gtk, Gdk
        has_display, _ = Gtk.init_check()
        if not has_display:
            self.skipTest("Requires display connection for full page view test")

        from docupad import DocuPadWindow
        tmp_dir = tempfile.mkdtemp(prefix="docupad_fullpage_")
        pdf_path = os.path.join(tmp_dir, "sample_document.pdf")
        md_path = os.path.join(tmp_dir, "sample_notes.md")

        try:
            # Create a 2-page test PDF
            surf = cairo.PDFSurface(pdf_path, 600, 800)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0.2, 0.4, 0.6)
            cr.paint()
            surf.show_page()
            cr.set_source_rgb(0.3, 0.5, 0.7)
            cr.paint()
            surf.show_page()
            surf.finish()

            with open(md_path, "w", encoding="utf-8") as f:
                f.write("# Full Page Notes\n\nTesting rendered reader view.")

            # 1. Test opening PDF starts in Full Page View
            win_pdf = DocuPadWindow(initial_files=[pdf_path])
            win_pdf.show_all()
            win_pdf.present()

            t0 = time.time()
            while time.time() - t0 < 0.2:
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                time.sleep(0.01)

            # Window is maximized
            self.assertTrue(win_pdf.is_maximized())
            # Outline sidebar starts closed for clean full-width page view
            self.assertFalse(win_pdf.toc_box.get_visible())
            self.assertFalse(win_pdf.btn_toggle_toc.get_active())

            viewer_pdf = win_pdf.get_current_viewer()
            self.assertEqual(viewer_pdf.get_view_mode(), "pdf")
            self.assertTrue(viewer_pdf._fit_page_mode)

            # Page is fitted to page view without overflowing viewport
            alloc = viewer_pdf.scrolled_pdf.get_allocation()
            self.assertGreater(alloc.width, 200)
            self.assertGreater(alloc.height, 200)

            scale = viewer_pdf.ZOOM_STEPS[viewer_pdf.zoom_index]
            rendered_h = viewer_pdf._current_page_base_h * scale
            rendered_w = viewer_pdf._current_page_base_w * scale
            self.assertLessEqual(rendered_h, alloc.height)
            self.assertLessEqual(rendered_w, alloc.width)

            # Turning to next page maintains fit_page_mode
            viewer_pdf.goto_pdf_page(2)
            self.assertEqual(viewer_pdf.current_pdf_page, 2)
            self.assertTrue(viewer_pdf._fit_page_mode)

            # Full Page button works
            viewer_pdf.zoom_in()
            self.assertFalse(viewer_pdf._fit_page_mode)
            win_pdf.zoom_fit_page()
            self.assertTrue(viewer_pdf._fit_page_mode)

            win_pdf.destroy()

            # 2. Test opening Markdown file starts in Full Page Reader View
            win_md = DocuPadWindow(initial_files=[md_path])
            win_md.show_all()
            win_md.present()

            t0 = time.time()
            while time.time() - t0 < 0.2:
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
                time.sleep(0.01)

            self.assertTrue(win_md.is_maximized())
            self.assertFalse(win_md.toc_box.get_visible())
            viewer_md = win_md.get_current_viewer()
            self.assertEqual(viewer_md.get_view_mode(), "rendered")

            win_md.destroy()

            # 3. Test opening without files starts in normal unmaximized window in Source View
            win_empty = DocuPadWindow()
            self.assertFalse(win_empty.is_maximized())
            self.assertEqual(win_empty.get_current_viewer().get_view_mode(), "source")
            win_empty.destroy()

        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass
            if os.path.exists(md_path):
                try:
                    os.remove(md_path)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def test_mouse_grip_and_pan_with_real_gdk_events_and_fit_state(self):
        """Test mouse grip and panning with real Gdk.Event objects and accurate cursor/pannable state."""
        from docupad import DocuPadWindow
        tmp_dir = tempfile.mkdtemp(prefix="docupad_pan_test_")
        pdf_path = os.path.join(tmp_dir, "pantest.pdf")
        try:
            # Create a 2-page test PDF with standard A4 dimensions (595x842)
            surf = cairo.PDFSurface(pdf_path, 595, 842)
            cr = cairo.Context(surf)
            cr.set_source_rgb(0.95, 0.95, 0.95)
            cr.paint()
            surf.show_page()
            cr.paint()
            surf.show_page()
            surf.finish()

            win = DocuPadWindow(initial_files=[pdf_path])
            win.show_all()

            for _ in range(50):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)

            viewer = win.get_current_viewer()
            self.assertEqual(viewer.get_view_mode(), "pdf")

            # 1. In full page view fitting window, page fits cleanly and is not pannable
            viewer.fit_to_page()
            for _ in range(20):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
            self.assertFalse(viewer._is_pdf_pannable())

            # 2. Zoom in so document has scrollable overflow
            for _ in range(5):
                viewer.zoom_in()
            for _ in range(30):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)

            self.assertTrue(viewer._is_pdf_pannable())

            vadj = viewer.scrolled_pdf.get_vadjustment()
            hadj = viewer.scrolled_pdf.get_hadjustment()
            v_start = vadj.get_value()
            h_start = hadj.get_value()

            # 3. Simulate mouse press with native Gdk.Event
            ev_press = Gdk.Event.new(Gdk.EventType.BUTTON_PRESS)
            ev_press.button.button = 1
            ev_press.button.x_root = 400.0
            ev_press.button.y_root = 400.0

            handled_press = viewer._on_pan_button_press(viewer.pdf_event_box, ev_press)
            self.assertTrue(handled_press)
            self.assertTrue(viewer._is_panning)

            # 4. Simulate mouse drag (move pointer up 60px -> scroll down 60px)
            ev_motion = Gdk.Event.new(Gdk.EventType.MOTION_NOTIFY)
            ev_motion.motion.x_root = 400.0
            ev_motion.motion.y_root = 340.0

            handled_motion = viewer._on_pan_motion_notify(viewer.pdf_event_box, ev_motion)
            self.assertTrue(handled_motion)
            self.assertAlmostEqual(vadj.get_value(), v_start + 60.0, delta=1.0)

            # 5. Simulate mouse release with native Gdk.Event
            ev_release = Gdk.Event.new(Gdk.EventType.BUTTON_RELEASE)
            ev_release.button.button = 1

            handled_release = viewer._on_pan_button_release(viewer.pdf_event_box, ev_release)
            self.assertTrue(handled_release)
            self.assertFalse(viewer._is_panning)

            # 6. Fit to page clears pannable state
            viewer.fit_to_page()
            for _ in range(20):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)
            self.assertFalse(viewer._is_pdf_pannable())

            win.destroy()

        finally:
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass


class TestTextSelectionAndHyperlinks(unittest.TestCase):
    """Test PDF text selection, Cairo overlays, clipboard copying, and Ctrl+Click hyperlinks."""

    def test_pdf_text_selection_and_expansion(self):
        tmp_dir = tempfile.mkdtemp(prefix="docupad_sel_test_")
        pdf_path = os.path.join(tmp_dir, "text_test.pdf")
        try:
            surf = cairo.PDFSurface(pdf_path, 595, 842)
            cr = cairo.Context(surf)
            cr.select_font_face("Sans")
            cr.set_font_size(24)
            cr.move_to(50, 100)
            cr.show_text("Hello DocuPad World")
            cr.move_to(50, 140)
            cr.show_text("Second line of document")
            surf.show_page()
            surf.finish()

            viewer = DocViewer()
            loaded = viewer.load_file(pdf_path)
            self.assertTrue(loaded)
            self.assertEqual(viewer.get_view_mode(), "pdf")

            # Let GTK process initial idle/rendering tasks
            for _ in range(20):
                while Gtk.events_pending():
                    Gtk.main_iteration_do(False)

            self.assertIn("Hello DocuPad World", viewer._page_text)
            self.assertGreater(len(viewer._page_char_rects), 10)

            # 1. Test character hit testing
            # First character 'H' is around x=50, y=80-105
            char_idx = viewer._find_char_at(55.0, 95.0)
            self.assertIsNotNone(char_idx)
            self.assertEqual(viewer._page_text[char_idx], "H")

            # 2. Test word expansion: "Hello"
            w_start, w_end = viewer._expand_to_word(char_idx)
            self.assertEqual(viewer._page_text[w_start:w_end], "Hello")

            # 3. Test line expansion: "Hello DocuPad World"
            l_start, l_end = viewer._expand_to_line(char_idx)
            self.assertEqual(viewer._page_text[l_start:l_end], "Hello DocuPad World")

            # 4. Test setting selection range
            viewer._set_pdf_selection(w_start, w_end)
            self.assertEqual(viewer._selected_text, "Hello")
            self.assertEqual(viewer._selected_range, (w_start, w_end))

            # 5. Test clipboard copying
            viewer._copy_to_clipboard(viewer._selected_text)
            clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            self.assertEqual(clip.wait_for_text(), "Hello")

            # 6. Test select all
            viewer._select_all_pdf()
            self.assertEqual(viewer._selected_range, (0, len(viewer._page_text)))
            self.assertEqual(viewer._selected_text, viewer._page_text)

            # 7. Test clear selection
            viewer._clear_pdf_selection()
            self.assertIsNone(viewer._selected_range)
            self.assertEqual(viewer._selected_text, "")

            # 8. Test Cairo overlay drawing runs with active selection without error
            viewer._set_pdf_selection(0, 5)
            img_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, 800)
            test_cr = cairo.Context(img_surf)
            res = viewer._on_draw_pdf_overlay(viewer.pdf_event_box, test_cr)
            self.assertFalse(res)  # Draws cleanly and returns False to allow GTK propagation

        finally:
            viewer.pdf_renderer.cleanup()
            if os.path.exists(pdf_path):
                try:
                    os.remove(pdf_path)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def test_source_view_url_detection_and_cursor(self):
        viewer = DocViewer()
        sample_doc = (
            "# Links Guide\n\n"
            "Visit our website at https://github.com/bluechile/docupad for updates.\n"
            "Or read the [DocuPad Manual](https://docupad.org/docs) online.\n"
            "Contact: mailto:dev@docupad.org\n"
        )
        viewer.set_text(sample_doc)
        viewer.set_view_mode("source")

        for _ in range(10):
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

        # Test regex detection directly on text lines
        matches_urls = viewer._URL_REGEX.findall(sample_doc)
        self.assertIn("https://github.com/bluechile/docupad", matches_urls)
        self.assertIn("https://docupad.org/docs", matches_urls)
        self.assertIn("mailto:dev@docupad.org", matches_urls)

        md_matches = [m.group(2) for m in viewer._MD_LINK_REGEX.finditer(sample_doc)]
        self.assertIn("https://docupad.org/docs", md_matches)

        # Test updating cursor with Ctrl modifier
        viewer._update_source_cursor(is_ctrl=True, url="https://github.com/bluechile/docupad")
        self.assertEqual(viewer.source_view.get_tooltip_text(), "Ctrl + Click to follow link: https://github.com/bluechile/docupad")

        viewer._update_source_cursor(is_ctrl=False, url=None)
        self.assertIsNone(viewer.source_view.get_tooltip_text())

    def test_open_hyperlink_emits_signal_for_local_files(self):
        viewer = DocViewer()
        received_paths = []
        viewer.connect("open-file-requested", lambda v, path: received_paths.append(path))

        with tempfile.NamedTemporaryFile(suffix=".md", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            # 1. file:// URI
            viewer.open_hyperlink(f"file://{tmp_path}")
            self.assertEqual(len(received_paths), 1)
            self.assertEqual(received_paths[0], tmp_path)

            # 2. direct absolute path
            viewer.open_hyperlink(tmp_path)
            self.assertEqual(len(received_paths), 2)
            self.assertEqual(received_paths[1], tmp_path)
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_pdf_hyperlink_finding_and_hover(self):
        viewer = DocViewer()
        viewer._page_links = [
            {"rect": (100.0, 200.0, 300.0, 220.0), "type": "uri", "target": "https://docupad.org", "title": "DocuPad"},
            {"rect": (100.0, 250.0, 300.0, 270.0), "type": "page", "target": 5, "title": "Chapter 5"},
        ]

        # 1. Hit testing inside link 1
        link1 = viewer._find_link_at(150.0, 210.0)
        self.assertIsNotNone(link1)
        self.assertEqual(link1["target"], "https://docupad.org")
        self.assertEqual(link1["type"], "uri")

        # 2. Hit testing inside link 2
        link2 = viewer._find_link_at(200.0, 260.0)
        self.assertIsNotNone(link2)
        self.assertEqual(link2["target"], 5)
        self.assertEqual(link2["type"], "page")

        # 3. Hit testing outside links
        self.assertIsNone(viewer._find_link_at(50.0, 50.0))

    def test_webkit_decide_policy_intercepts_link_clicks(self):
        viewer = DocViewer()
        opened_links = []
        viewer.open_hyperlink = lambda url: opened_links.append(url)

        class MockRequest:
            def get_uri(self):
                return "https://example.com/page"

        class MockAction:
            def get_navigation_type(self):
                from gi.repository import WebKit2
                return WebKit2.NavigationType.LINK_CLICKED

            def get_request(self):
                return MockRequest()

        class MockDecision:
            def __init__(self):
                self.ignored = False

            def get_navigation_action(self):
                return MockAction()

            def get_navigation_type(self):
                from gi.repository import WebKit2
                return WebKit2.NavigationType.LINK_CLICKED

            def get_request(self):
                return MockRequest()

            def ignore(self):
                self.ignored = True

        from gi.repository import WebKit2
        decision = MockDecision()
        handled = viewer._on_web_view_decide_policy(None, decision, WebKit2.PolicyDecisionType.NAVIGATION_ACTION)

        self.assertTrue(handled)
        self.assertTrue(decision.ignored)
        self.assertEqual(opened_links, ["https://example.com/page"])


if __name__ == "__main__":
    unittest.main()
