#!/usr/bin/env python3
"""
DocuPad - Document Viewer & Editor Component
Provides a Retina-optimized multi-mode document viewer (Rendered HTML/PDF View + Source View)
with the translucent DocuPad watermark (darker-blue open book & document on an unassuming yoga pad).
"""

import os
import tempfile
import cairo
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GtkSource', '4')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, GtkSource, Pango, GObject
from doc_tools import DocumentParser, PDFPageRenderer

try:
    gi.require_version('WebKit2', '4.1')
    from gi.repository import WebKit2
    HAS_WEBKIT = True
except Exception:
    try:
        gi.require_version('WebKit2', '4.0')
        from gi.repository import WebKit2
        HAS_WEBKIT = True
    except Exception:
        HAS_WEBKIT = False


_styles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles")
if os.path.exists(_styles_dir):
    GtkSource.StyleSchemeManager.get_default().prepend_search_path(_styles_dir)


class DocViewer(Gtk.Box):
    """
    Multi-mode document viewer and editor widget with:
    - Source / Text View (GtkSourceView 4 with translucent DocuPad watermark)
    - Rendered Document View (WebKit2 HTML preview for Markdown/RST/HTML/CSV or PDF Page Canvas)
    - Dynamic Retina Zooming (50% to 300%)
    """

    __gsignals__ = {
        'cursor-moved': (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        'content-modified': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'zoom-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        'file-saved': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'outline-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

    ZOOM_STEPS = [
        0.50, 0.60, 0.70, 0.80, 0.90,
        1.00,
        1.10, 1.25, 1.40, 1.55, 1.75, 2.00, 2.25, 2.50, 3.00
    ]
    DEFAULT_ZOOM_INDEX = 5  # 100%
    _shared_css_provider = None
    _watermark_pixbuf = None
    _watermark_loaded = False

    def __init__(self, file_path=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self.file_path = None
        self.file_encoding = "UTF-8"
        self.line_ending = "LF"
        self.doc_type = "markdown"
        self._is_modified = False
        self.read_only = False

        # Cached statistics
        self._stats_dirty = True
        self._cached_word_count = 0

        # Font & Retina scaling
        self.base_font_family = "Monospace"
        self.base_font_size = 11
        self.zoom_index = self.DEFAULT_ZOOM_INDEX

        # PDF state
        self.pdf_renderer = None
        self.current_pdf_page = 1
        self.total_pdf_pages = 0

        # Stack switching between 'source', 'rendered', and 'pdf'
        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.view_stack.set_transition_duration(100)
        self.pack_start(self.view_stack, True, True, 0)

        # 1. Source / Text View
        self.scrolled_source = Gtk.ScrolledWindow()
        self.scrolled_source.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_source.set_shadow_type(Gtk.ShadowType.NONE)

        self.source_buffer = GtkSource.Buffer()
        self.source_view = GtkSource.View.new_with_buffer(self.source_buffer)
        self.scrolled_source.add(self.source_view)
        self.view_stack.add_named(self.scrolled_source, "source")

        # 2. Rendered Document View (WebKit2 or rich fallback TextView)
        self.scrolled_rendered = Gtk.ScrolledWindow()
        self.scrolled_rendered.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        if HAS_WEBKIT:
            self.web_view = WebKit2.WebView()
            rgba = Gdk.RGBA(0.039, 0.055, 0.102, 1.0)
            self.web_view.set_background_color(rgba)
            self.scrolled_rendered.add(self.web_view)
        else:
            self.web_view = None
            self.fallback_preview = Gtk.TextView()
            self.fallback_preview.set_editable(False)
            self.fallback_preview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            self.fallback_preview.set_left_margin(24)
            self.fallback_preview.set_right_margin(24)
            self.fallback_preview.set_top_margin(18)
            self.fallback_preview.set_bottom_margin(18)
            self.scrolled_rendered.add(self.fallback_preview)
        self.view_stack.add_named(self.scrolled_rendered, "rendered")

        # 3. PDF Page Canvas View
        self._build_pdf_view()

        # Configure properties, styles, and signals
        self._setup_view_properties()
        self._setup_retina_styles()
        self._setup_signals()

        self.apply_retina_font()
        self.set_theme("docupad-neon")

        self.show_all()
        self.view_stack.set_visible_child_name("source")

        if file_path:
            expanded = os.path.abspath(os.path.expanduser(file_path))
            if os.path.isfile(expanded):
                self.load_file(expanded)
            elif not os.path.exists(expanded):
                self.file_path = expanded
                self.doc_type = DocumentParser.get_doc_type(self.file_path)
                self.set_syntax_from_filename(self.file_path)

    def _build_pdf_view(self):
        pdf_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # PDF Navigation Toolbar
        nav_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        nav_bar.set_margin_start(12)
        nav_bar.set_margin_end(12)
        nav_bar.set_margin_top(6)
        nav_bar.set_margin_bottom(6)

        self.btn_pdf_prev = Gtk.Button.new_from_icon_name("go-previous-symbolic", Gtk.IconSize.BUTTON)
        self.btn_pdf_prev.set_tooltip_text("Previous PDF Page (PageUp)")
        self.btn_pdf_prev.connect("clicked", lambda b: self.goto_pdf_page(self.current_pdf_page - 1))
        nav_bar.pack_start(self.btn_pdf_prev, False, False, 0)

        self.btn_pdf_next = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        self.btn_pdf_next.set_tooltip_text("Next PDF Page (PageDown)")
        self.btn_pdf_next.connect("clicked", lambda b: self.goto_pdf_page(self.current_pdf_page + 1))
        nav_bar.pack_start(self.btn_pdf_next, False, False, 0)

        self.lbl_pdf_page = Gtk.Label(label="Page 1 / 1")
        nav_bar.pack_start(self.lbl_pdf_page, False, False, 6)

        pdf_box.pack_start(nav_bar, False, False, 0)

        self.scrolled_pdf = Gtk.ScrolledWindow()
        self.scrolled_pdf.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.pdf_image = Gtk.Image()
        self.pdf_image.set_margin_top(16)
        self.pdf_image.set_margin_bottom(16)
        self.pdf_image.set_margin_start(16)
        self.pdf_image.set_margin_end(16)
        self.scrolled_pdf.add(self.pdf_image)
        pdf_box.pack_start(self.scrolled_pdf, True, True, 0)

        self.view_stack.add_named(pdf_box, "pdf")

    def _setup_view_properties(self):
        view = self.source_view
        view.set_show_line_numbers(True)
        view.set_highlight_current_line(True)
        view.set_show_line_marks(False)
        view.set_auto_indent(True)
        view.set_indent_on_tab(True)
        view.set_tab_width(4)
        view.set_insert_spaces_instead_of_tabs(True)
        view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        view.set_monospace(True)
        view.set_smart_home_end(GtkSource.SmartHomeEndType.ALWAYS)
        view.set_left_margin(16)
        view.set_right_margin(16)
        view.set_top_margin(12)
        view.set_bottom_margin(12)

    def _setup_retina_styles(self):
        if DocViewer._shared_css_provider is None:
            provider = Gtk.CssProvider()
            css = b"""
            textview {
                font-feature-settings: "liga 1, calt 1";
                background-color: #0a0e1a;
                color: #ffffff;
            }
            textview text {
                background-color: #0a0e1a;
                color: #ffffff;
            }
            textview border {
                background-color: #070a14;
                border-right: 1px solid rgba(0, 240, 255, 0.25);
                padding-right: 8px;
                padding-left: 6px;
            }
            """
            try:
                provider.load_from_data(css)
                DocViewer._shared_css_provider = provider
            except Exception:
                return

        context = self.source_view.get_style_context()
        context.add_provider(
            DocViewer._shared_css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _setup_signals(self):
        self.source_buffer.connect("changed", self._on_buffer_changed)
        self.source_buffer.connect("notify::cursor-position", self._on_cursor_moved)

        scroll_mask = Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK
        for w in (self, self.scrolled_source, self.source_view, self.scrolled_rendered, self.scrolled_pdf):
            w.add_events(scroll_mask)
            w.connect("scroll-event", self._on_scroll_event)
        if HAS_WEBKIT and self.web_view is not None:
            self.web_view.add_events(scroll_mask)
            self.web_view.connect("scroll-event", self._on_scroll_event)

        self.source_view.connect_after("draw", self._on_draw_watermark)

    @classmethod
    def _get_watermark_pixbuf(cls):
        """Load and cache the official DocuPad SVG (translucent blue open tome & doc on yoga pad)."""
        if not cls._watermark_loaded:
            cls._watermark_loaded = True
            svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docupad.svg")
            if os.path.exists(svg_path):
                try:
                    cls._watermark_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                        svg_path, 340, 340, True
                    )
                except Exception:
                    cls._watermark_pixbuf = None
        return cls._watermark_pixbuf

    def _on_draw_watermark(self, widget, cr):
        """Render the translucent darker-blue open book & document resting on a yoga pad."""
        pixbuf = self._get_watermark_pixbuf()
        if pixbuf is None:
            return False

        try:
            text_win = widget.get_window(Gtk.TextWindowType.TEXT)
            alloc = widget.get_allocation()
            if text_win is not None:
                gutter_w = alloc.width - text_win.get_width()
                view_w = text_win.get_width()
                view_h = text_win.get_height()
            else:
                gutter_w = 48
                view_w = alloc.width - gutter_w
                view_h = alloc.height

            pw = pixbuf.get_width()
            ph = pixbuf.get_height()
            if view_w < 120 or view_h < 120:
                return False

            x = gutter_w + max(20, (view_w - pw) * 0.55)
            y = max(20, (view_h - ph) * 0.58)

            cr.save()
            cr.set_operator(cairo.OPERATOR_OVER)
            Gdk.cairo_set_source_pixbuf(cr, pixbuf, x, y)
            cr.paint_with_alpha(0.32)
            cr.restore()
        except Exception:
            pass
        return False

    def _on_buffer_changed(self, buffer):
        self._stats_dirty = True
        if not self._is_modified:
            self._is_modified = True
        self.emit("content-modified", True)

    def _on_cursor_moved(self, buffer, pspec):
        line, col = self.get_cursor_position()
        self.emit("cursor-moved", line, col)

    def _on_scroll_event(self, widget, event):
        if event.state & Gdk.ModifierType.CONTROL_MASK:
            if event.direction == Gdk.ScrollDirection.UP:
                self.zoom_in()
                return True
            elif event.direction == Gdk.ScrollDirection.DOWN:
                self.zoom_out()
                return True
            elif event.direction == Gdk.ScrollDirection.SMOOTH:
                _, dy = event.get_scroll_deltas()
                if dy < -0.01:
                    self.zoom_in()
                    return True
                elif dy > 0.01:
                    self.zoom_out()
                    return True
        return False

    # --------------------------------------------------------------------------
    # View Mode Switching (Rendered vs Source)
    # --------------------------------------------------------------------------
    def get_view_mode(self) -> str:
        return self.view_stack.get_visible_child_name() or "source"

    def set_view_mode(self, mode: str):
        """Switch between 'source' and 'rendered' (or 'pdf' for PDF documents)."""
        if mode == "rendered":
            if self.doc_type == "pdf" and self.pdf_renderer and self.pdf_renderer.page_count > 0:
                self.view_stack.set_visible_child_name("pdf")
                self._refresh_pdf_page()
            else:
                self.refresh_rendered_preview()
                self.view_stack.set_visible_child_name("rendered")
        else:
            self.view_stack.set_visible_child_name("source")
            self.source_view.grab_focus()

    def toggle_view_mode(self) -> str:
        cur = self.get_view_mode()
        if cur == "source":
            self.set_view_mode("rendered")
        else:
            self.set_view_mode("source")
        return self.get_view_mode()

    def refresh_rendered_preview(self):
        text = self.get_text()
        html_doc = DocumentParser.render_markdown_to_html(text, self.doc_type, self.file_path)
        if HAS_WEBKIT and self.web_view is not None:
            base_uri = "file://" + os.path.dirname(os.path.abspath(self.file_path)) + "/" if self.file_path else "file:///"
            self.web_view.load_html(html_doc, base_uri)
            self.web_view.set_zoom_level(self.ZOOM_STEPS[self.zoom_index])
        elif hasattr(self, "fallback_preview"):
            self.fallback_preview.get_buffer().set_text(text)

    def goto_pdf_page(self, page_num: int):
        if not self.pdf_renderer or self.pdf_renderer.page_count == 0:
            return
        self.current_pdf_page = max(1, min(int(page_num), self.pdf_renderer.page_count))
        self._refresh_pdf_page()

    def _refresh_pdf_page(self):
        if not self.pdf_renderer:
            return
        scale = self.ZOOM_STEPS[self.zoom_index]
        dpi = int(round(130 * scale))
        png_path = self.pdf_renderer.render_page_png(self.current_pdf_page, dpi=dpi)
        self.lbl_pdf_page.set_text(f"Page {self.current_pdf_page} / {self.pdf_renderer.page_count}  ({int(scale*100)}%)")
        self.btn_pdf_prev.set_sensitive(self.current_pdf_page > 1)
        self.btn_pdf_next.set_sensitive(self.current_pdf_page < self.pdf_renderer.page_count)
        if png_path and os.path.isfile(png_path):
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file(png_path)
                self.pdf_image.set_from_pixbuf(pix)
            except Exception:
                pass

    # --------------------------------------------------------------------------
    # Retina Vision & Font Scaling
    # --------------------------------------------------------------------------
    def get_zoom_percent(self):
        return int(round(self.ZOOM_STEPS[self.zoom_index] * 100))

    def zoom_in(self):
        if self.zoom_index < len(self.ZOOM_STEPS) - 1:
            self.zoom_index += 1
            self.apply_retina_font()
            self.emit("zoom-changed", self.get_zoom_percent())

    def zoom_out(self):
        if self.zoom_index > 0:
            self.zoom_index -= 1
            self.apply_retina_font()
            self.emit("zoom-changed", self.get_zoom_percent())

    def zoom_reset(self):
        self.zoom_index = self.DEFAULT_ZOOM_INDEX
        self.apply_retina_font()
        self.emit("zoom-changed", self.get_zoom_percent())

    def apply_retina_font(self):
        scale = self.ZOOM_STEPS[self.zoom_index]
        scaled_size_pt = self.base_font_size * scale

        font_desc = Pango.FontDescription()
        font_desc.set_family(self.base_font_family)
        font_desc.set_size(int(scaled_size_pt * Pango.SCALE))
        self.source_view.override_font(font_desc)

        if HAS_WEBKIT and self.web_view is not None:
            self.web_view.set_zoom_level(scale)
        if self.get_view_mode() == "pdf":
            self._refresh_pdf_page()

    def set_font_from_string(self, font_str: str):
        desc = Pango.FontDescription.from_string(font_str)
        family = desc.get_family()
        size = desc.get_size() / Pango.SCALE
        if family:
            self.base_font_family = family
        if size > 0:
            self.base_font_size = size
        self.zoom_index = self.DEFAULT_ZOOM_INDEX
        self.apply_retina_font()
        self.emit("zoom-changed", self.get_zoom_percent())

    def get_font_string(self) -> str:
        return f"{self.base_font_family} {int(self.base_font_size)}"

    def set_theme(self, scheme_id: str):
        manager = GtkSource.StyleSchemeManager.get_default()
        scheme = manager.get_scheme(scheme_id)
        if scheme is None:
            for fallback in ("docupad-neon", "oblivion", "solarized-dark", "classic"):
                scheme = manager.get_scheme(fallback)
                if scheme:
                    break
        if scheme:
            self.source_buffer.set_style_scheme(scheme)

    def set_syntax_from_filename(self, filename: str):
        lang_manager = GtkSource.LanguageManager.get_default()
        lang = None
        if filename:
            lang = lang_manager.guess_language(filename, None)
        if not lang:
            lang = lang_manager.get_language("markdown")
        self.source_buffer.set_language(lang)

    def set_line_numbers(self, visible: bool):
        self.source_view.set_show_line_numbers(visible)

    def get_line_numbers(self) -> bool:
        return self.source_view.get_show_line_numbers()

    def set_word_wrap(self, wrap: bool):
        mode = Gtk.WrapMode.WORD_CHAR if wrap else Gtk.WrapMode.NONE
        self.source_view.set_wrap_mode(mode)

    def get_word_wrap(self) -> bool:
        return self.source_view.get_wrap_mode() != Gtk.WrapMode.NONE

    def set_read_only(self, ro: bool):
        self.read_only = bool(ro)
        self.source_view.set_editable(not self.read_only)

    def goto_line(self, line_number: int):
        self.goto_line_col(line_number, 1)

    def goto_line_col(self, line_number: int, column: int = 1):
        buf = self.source_buffer
        total_lines = max(1, buf.get_line_count())
        target_line = max(0, min(int(line_number) - 1, total_lines - 1))

        iter_at_line = buf.get_iter_at_line(target_line)
        eol = iter_at_line.copy()
        if not eol.ends_line():
            eol.forward_to_line_end()
        line_length = eol.get_line_offset()
        offset = max(0, min(int(column) - 1, line_length))
        iter_at_line.set_line_offset(offset)

        buf.place_cursor(iter_at_line)
        self.source_view.scroll_to_iter(iter_at_line, 0.25, False, 0.0, 0.0)
        self.source_view.grab_focus()

    def get_text(self) -> str:
        start = self.source_buffer.get_start_iter()
        end = self.source_buffer.get_end_iter()
        return self.source_buffer.get_text(start, end, True)

    def set_text(self, text: str, undoable: bool = True):
        safe_text = text.replace("\x00", "\ufffd") if "\x00" in text else text
        if not undoable:
            self.source_buffer.begin_not_undoable_action()
        try:
            self.source_buffer.set_text(safe_text)
        finally:
            if not undoable:
                self.source_buffer.end_not_undoable_action()
        self.source_buffer.place_cursor(self.source_buffer.get_start_iter())

    def get_cursor_position(self):
        insert_mark = self.source_buffer.get_insert()
        cursor_iter = self.source_buffer.get_iter_at_mark(insert_mark)
        return cursor_iter.get_line() + 1, cursor_iter.get_line_offset() + 1

    def get_document_statistics(self):
        buf = self.source_buffer
        lines = max(1, buf.get_line_count())
        chars = buf.get_char_count()
        if chars == 0:
            self._cached_word_count = 0
            self._stats_dirty = False
            return 1, 0, 0, 0
        if self._stats_dirty:
            text = self.get_text()
            self._cached_word_count = len(text.split())
            self._stats_dirty = False
        words = self._cached_word_count
        read_min = max(1, int(round(words / 200.0))) if words > 0 else 0
        return lines, words, chars, read_min

    def is_modified(self) -> bool:
        return self._is_modified

    def load_file(self, path: str) -> bool:
        abs_path = os.path.abspath(path)
        text, encoding, doc_type = DocumentParser.load_document_text(abs_path)

        self.file_path = abs_path
        self.file_encoding = encoding
        self.doc_type = doc_type
        self.set_text(text, undoable=False)
        self.set_syntax_from_filename(self.file_path)

        if doc_type == "pdf":
            if self.pdf_renderer:
                self.pdf_renderer.cleanup()
            self.pdf_renderer = PDFPageRenderer(abs_path)
            self.total_pdf_pages = self.pdf_renderer.page_count
            self.current_pdf_page = 1
            self.set_read_only(True)
            self.set_view_mode("rendered")
        elif doc_type in ("markdown", "rst", "html", "csv", "odt", "docx"):
            if doc_type in ("odt", "docx"):
                self.set_read_only(True)
            self.set_view_mode("rendered")
        else:
            self.set_view_mode("source")

        self._is_modified = False
        self.emit("content-modified", False)
        self.emit("outline-updated")
        return True

    def save_file(self, path: str = None) -> bool:
        target_path = path or self.file_path
        if not target_path:
            raise ValueError("No file path specified to save.")

        if self.doc_type in ("pdf", "odt", "docx") and target_path == self.file_path:
            raise ValueError(f"Use 'Save As' to export extracted {self.doc_type.upper()} text to a new file.")

        abs_target = os.path.abspath(target_path)
        text = self.get_text()
        encoded_bytes = text.encode("utf-8")

        target_dir = os.path.dirname(abs_target) or "."
        if os.access(target_dir, os.W_OK) and not os.path.islink(abs_target):
            fd, tmp_path = tempfile.mkstemp(prefix=".docupad_save_", dir=target_dir)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(encoded_bytes)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, abs_target)
            except Exception:
                if os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except OSError:
                        pass
                raise
        else:
            with open(abs_target, "wb") as f:
                f.write(encoded_bytes)

        self.file_path = abs_target
        self.doc_type = DocumentParser.get_doc_type(self.file_path)
        self._is_modified = False
        self.set_syntax_from_filename(self.file_path)
        self.emit("content-modified", False)
        self.emit("file-saved", self.file_path)
        self.emit("outline-updated")
        return True
