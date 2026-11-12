#!/usr/bin/env python3
"""
DocuPanel - Document Viewer & Editor Component
Provides a Retina-optimized multi-mode document viewer (Rendered HTML/PDF View + Source View)
with the translucent DocuPanel watermark (darker-blue open book & document on an unassuming yoga pad).
"""

import os
import re
import urllib.parse
import tempfile
import cairo
import gi
from typing import Optional, Tuple, List

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GtkSource', '4')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, GtkSource, Pango, GObject, GLib, Gio
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
    - Source / Text View (GtkSourceView 4 with translucent DocuPanel watermark)
    - Rendered Document View (WebKit2 HTML preview for Markdown/RST/HTML/CSV or PDF Page Canvas)
    - Dynamic Retina Zooming (50% to 300%)
    """

    __gsignals__ = {
        'cursor-moved': (GObject.SignalFlags.RUN_FIRST, None, (int, int)),
        'content-modified': (GObject.SignalFlags.RUN_FIRST, None, (bool,)),
        'zoom-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
        'file-saved': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'outline-updated': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'open-file-requested': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
        'pdf-page-changed': (GObject.SignalFlags.RUN_FIRST, None, (int,)),
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
    _URL_REGEX = re.compile(r'https?://[^\s)\]>"\'`]+|mailto:[^\s)\]>"\'`]+|file://[^\s)\]>"\'`]+')
    _MD_LINK_REGEX = re.compile(r'\[([^\]]+)\]\((https?://[^\s)\]>"\'`]+|[^)\s]+)\)')

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
        self._last_scroll_zoom_time = 0
        self._is_panning = False
        self._fit_page_mode = False
        self._fit_page_pending = False
        self._font_css_provider = None

        # PDF state & responsive rendering
        self.pdf_renderer = None
        self.current_pdf_page = 1
        self.total_pdf_pages = 0
        self._current_pdf_pixbuf = None
        self._current_page_base_w = 595.0
        self._current_page_base_h = 842.0
        self._pdf_render_gen = 0
        self._pdf_debounce_timer = None

        # PDF text selection & interactive hyperlinks
        self._page_text = ""
        self._page_char_rects = []
        self._page_links = []
        self._selected_range = None
        self._selected_text = ""
        self._is_selecting_text = False
        self._sel_anchor_idx = None
        self._hovered_link = None
        self._source_hovered_url = None
        self.outline_source = "headings"

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

        # 2. Rendered Document View (WebKit2 directly in Stack, or fallback TextView in ScrolledWindow)
        self.scrolled_rendered = None
        if HAS_WEBKIT:
            self.web_view = WebKit2.WebView()
            rgba = Gdk.RGBA(0.039, 0.055, 0.102, 1.0)
            self.web_view.set_background_color(rgba)
            self.view_stack.add_named(self.web_view, "rendered")
        else:
            self.web_view = None
            self.scrolled_rendered = Gtk.ScrolledWindow()
            self.scrolled_rendered.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
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
        self.set_theme("docupanel-neon")

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
        self.btn_pdf_prev.set_tooltip_text("Previous Page")
        self.btn_pdf_prev.connect("clicked", lambda b: self.goto_pdf_page(self.current_pdf_page - 1))
        nav_bar.pack_start(self.btn_pdf_prev, False, False, 0)

        self.btn_pdf_next = Gtk.Button.new_from_icon_name("go-next-symbolic", Gtk.IconSize.BUTTON)
        self.btn_pdf_next.set_tooltip_text("Next Page")
        self.btn_pdf_next.connect("clicked", lambda b: self.goto_pdf_page(self.current_pdf_page + 1))
        nav_bar.pack_start(self.btn_pdf_next, False, False, 0)

        self.btn_pdf_fit = Gtk.Button.new_from_icon_name("zoom-fit-best-symbolic", Gtk.IconSize.BUTTON)
        self.btn_pdf_fit.set_tooltip_text("Full Page View / Fit Page (F5 / Ctrl+9)")
        self.btn_pdf_fit.connect("clicked", lambda b: self.fit_to_page())
        nav_bar.pack_start(self.btn_pdf_fit, False, False, 0)

        self.lbl_pdf_page = Gtk.Label(label="Page 1 / 1")
        nav_bar.pack_start(self.lbl_pdf_page, False, False, 6)

        pdf_box.pack_start(nav_bar, False, False, 0)

        self.scrolled_pdf = Gtk.ScrolledWindow()
        self.scrolled_pdf.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_pdf.set_kinetic_scrolling(False)
        self.scrolled_pdf.connect("size-allocate", self._on_pdf_size_allocate)
        self.pdf_image = Gtk.Image()
        self.pdf_image.set_margin_top(16)
        self.pdf_image.set_margin_bottom(16)
        self.pdf_image.set_margin_start(16)
        self.pdf_image.set_margin_end(16)

        self.pdf_event_box = Gtk.EventBox()
        self.pdf_event_box.add(self.pdf_image)
        self.pdf_event_box.set_visible_window(True)
        self.pdf_event_box.connect_after("draw", self._on_draw_pdf_overlay)

        pan_mask = (
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.pdf_event_box.add_events(pan_mask)
        self.pdf_event_box.connect("button-press-event", self._on_pan_button_press)
        self.pdf_event_box.connect("button-release-event", self._on_pan_button_release)
        self.pdf_event_box.connect("motion-notify-event", self._on_pan_motion_notify)
        self.pdf_event_box.connect("enter-notify-event", self._on_pan_enter_notify)
        self.pdf_event_box.connect("leave-notify-event", self._on_pan_leave_notify)
        self.pdf_event_box.connect("grab-broken-event", self._on_pan_grab_broken)
        self.pdf_event_box.connect("realize", lambda w: self._update_pan_cursor(gripping=False))

        self.scrolled_pdf.add_events(pan_mask)
        self.scrolled_pdf.connect("button-press-event", self._on_pan_button_press)
        self.scrolled_pdf.connect("button-release-event", self._on_pan_button_release)
        self.scrolled_pdf.connect("motion-notify-event", self._on_pan_motion_notify)
        self.scrolled_pdf.connect("enter-notify-event", self._on_pan_enter_notify)
        self.scrolled_pdf.connect("leave-notify-event", self._on_pan_leave_notify)
        self.scrolled_pdf.connect("grab-broken-event", self._on_pan_grab_broken)
        self.scrolled_pdf.connect("realize", lambda w: self._update_pan_cursor(gripping=False))

        self.scrolled_pdf.add(self.pdf_event_box)
        viewport = self.scrolled_pdf.get_child()
        if viewport:
            viewport.add_events(pan_mask)
            viewport.connect("button-press-event", self._on_pan_button_press)
            viewport.connect("button-release-event", self._on_pan_button_release)
            viewport.connect("motion-notify-event", self._on_pan_motion_notify)
            viewport.connect("enter-notify-event", self._on_pan_enter_notify)
            viewport.connect("leave-notify-event", self._on_pan_leave_notify)
            viewport.connect("grab-broken-event", self._on_pan_grab_broken)
            viewport.connect("realize", lambda w: self._update_pan_cursor(gripping=False))

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
        targets = [self, self.scrolled_source, self.source_view, self.scrolled_pdf]
        if hasattr(self, "pdf_event_box") and self.pdf_event_box is not None:
            targets.append(self.pdf_event_box)
        if self.scrolled_rendered is not None:
            targets.append(self.scrolled_rendered)
        if hasattr(self, "fallback_preview") and self.fallback_preview is not None:
            targets.append(self.fallback_preview)

        for w in targets:
            w.add_events(scroll_mask)
            w.connect("scroll-event", self._on_scroll_event)

        key_targets = [self, self.scrolled_pdf]
        if hasattr(self, "pdf_event_box") and self.pdf_event_box is not None:
            key_targets.append(self.pdf_event_box)
        for kw in key_targets:
            kw.connect("key-press-event", self._on_key_press)
            kw.connect("key-release-event", self._on_key_release)

        # Source View mouse & keyboard interactions for Ctrl+Click hyperlink navigation
        self.source_view.add_events(
            Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.KEY_PRESS_MASK
            | Gdk.EventMask.KEY_RELEASE_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.source_view.connect("motion-notify-event", self._on_source_motion_notify)
        self.source_view.connect("button-press-event", self._on_source_button_press)
        self.source_view.connect("key-press-event", self._on_source_key_press)
        self.source_view.connect("key-release-event", self._on_source_key_release)
        self.source_view.connect("leave-notify-event", self._on_source_leave_notify)
        self.source_view.connect("populate-popup", self._on_source_populate_popup)

        if HAS_WEBKIT and self.web_view is not None:
            self.web_view.add_events(scroll_mask)
            self.web_view.connect("event", self._on_webkit_event)
            self.web_view.connect("scroll-event", self._on_scroll_event)
            self.web_view.connect("decide-policy", self._on_web_view_decide_policy)

        self.source_view.connect_after("draw", self._on_draw_watermark)

    @classmethod
    def _get_watermark_pixbuf(cls, size: int = 340):
        """Load and cache the official DocuPanel SVG (translucent blue open tome & doc on yoga pad)."""
        svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docupanel.svg")
        if size == 340:
            if not cls._watermark_loaded:
                cls._watermark_loaded = True
                if os.path.exists(svg_path):
                    try:
                        cls._watermark_pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                            svg_path, 340, 340, True
                        )
                    except Exception:
                        cls._watermark_pixbuf = None
            return cls._watermark_pixbuf
        if os.path.exists(svg_path):
            try:
                return GdkPixbuf.Pixbuf.new_from_file_at_scale(svg_path, size, size, True)
            except Exception:
                return cls._watermark_pixbuf
        return None

    def _on_draw_watermark(self, widget, cr):
        """Render the translucent darker-blue open book & document resting on a yoga pad."""
        scale = self.ZOOM_STEPS[self.zoom_index]
        target_sz = max(140, min(680, int(round(340 * scale))))
        pixbuf = self._get_watermark_pixbuf(target_sz)
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

    def _on_webkit_event(self, widget, event):
        if event.type == Gdk.EventType.SCROLL:
            return self._on_scroll_event(widget, event)
        return False

    def _on_scroll_event(self, widget, event):
        state = getattr(event, "state", 0)
        if not state and hasattr(event, "get_state"):
            ok, st = event.get_state()
            if ok:
                state = st
        if state & Gdk.ModifierType.CONTROL_MASK:
            ev_time = getattr(event, "time", 0)
            last_time = getattr(self, "_last_scroll_zoom_time", 0)

            direction = getattr(event, "direction", None)
            if direction is None and hasattr(event, "get_scroll_direction"):
                ok_d, d = event.get_scroll_direction()
                if ok_d:
                    direction = d

            if direction == Gdk.ScrollDirection.UP:
                if not ev_time or ev_time != last_time:
                    self._last_scroll_zoom_time = ev_time
                    self.zoom_in()
                return True
            elif direction == Gdk.ScrollDirection.DOWN:
                if not ev_time or ev_time != last_time:
                    self._last_scroll_zoom_time = ev_time
                    self.zoom_out()
                return True
            elif direction == Gdk.ScrollDirection.SMOOTH:
                res = event.get_scroll_deltas() if hasattr(event, "get_scroll_deltas") else ()
                dy = res[-1] if res else getattr(event, "delta_y", 0.0)
                if dy < -0.01:
                    if not ev_time or ev_time != last_time:
                        self._last_scroll_zoom_time = ev_time
                        self.zoom_in()
                    return True
                elif dy > 0.01:
                    if not ev_time or ev_time != last_time:
                        self._last_scroll_zoom_time = ev_time
                        self.zoom_out()
                    return True
                return True

        # Simple mouse wheel scroll up or down (without Control key)
        if self.get_view_mode() == "pdf":
            ev_time = getattr(event, "time", 0)
            last_page_time = getattr(self, "_last_page_scroll_time", 0)

            direction = getattr(event, "direction", None)
            if direction is None and hasattr(event, "get_scroll_direction"):
                ok_d, d = event.get_scroll_direction()
                if ok_d:
                    direction = d

            time_diff = (ev_time - last_page_time) if (ev_time and last_page_time) else 999
            if time_diff < 0:
                time_diff = 999

            if direction == Gdk.ScrollDirection.UP:
                if time_diff >= 120:
                    self._last_page_scroll_time = ev_time
                    self.goto_pdf_page(self.current_pdf_page - 1)
                return True
            elif direction == Gdk.ScrollDirection.DOWN:
                if time_diff >= 120:
                    self._last_page_scroll_time = ev_time
                    self.goto_pdf_page(self.current_pdf_page + 1)
                return True
            elif direction == Gdk.ScrollDirection.SMOOTH:
                res = event.get_scroll_deltas() if hasattr(event, "get_scroll_deltas") else ()
                dy = res[-1] if res else getattr(event, "delta_y", 0.0)
                if not hasattr(self, "_scroll_delta_accum"):
                    self._scroll_delta_accum = 0.0
                self._scroll_delta_accum += dy

                if self._scroll_delta_accum <= -0.3 and time_diff >= 120:
                    self._last_page_scroll_time = ev_time
                    self._scroll_delta_accum = 0.0
                    self.goto_pdf_page(self.current_pdf_page - 1)
                    return True
                elif self._scroll_delta_accum >= 0.3 and time_diff >= 120:
                    self._last_page_scroll_time = ev_time
                    self._scroll_delta_accum = 0.0
                    self.goto_pdf_page(self.current_pdf_page + 1)
                    return True
                elif abs(self._scroll_delta_accum) >= 0.3:
                    self._scroll_delta_accum = 0.0
                    return True
                return True

        return False

    def _on_key_press(self, widget, event):
        """Keyboard navigation and shortcuts (Ctrl+C, Ctrl+A, Page Up/Down, Arrows, Home/End)."""
        kv = getattr(event, "keyval", 0)
        state = getattr(event, "state", 0)
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if kv in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            if self.get_view_mode() == "pdf":
                self._update_pdf_hover_cursor(is_ctrl=True)
            elif self.get_view_mode() == "source":
                if self._source_hovered_url:
                    self._update_source_cursor(True, self._source_hovered_url)

        if self.get_view_mode() == "pdf":
            # Ctrl + C: Copy selected text
            if is_ctrl and kv in (Gdk.KEY_c, Gdk.KEY_C):
                if getattr(self, "_selected_text", None):
                    self._copy_to_clipboard(self._selected_text)
                    return True

            # Ctrl + A: Select all text on current PDF page
            if is_ctrl and kv in (Gdk.KEY_a, Gdk.KEY_A):
                self._select_all_pdf()
                return True

            mod_mask = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
            if not (state & mod_mask):
                is_shift = bool(state & Gdk.ModifierType.SHIFT_MASK)

                # Previous Page: Page_Up, KP_Page_Up, Prior, Left, Backspace, Shift+Space
                if kv in (Gdk.KEY_Page_Up, Gdk.KEY_KP_Page_Up, Gdk.KEY_Prior, Gdk.KEY_Left, Gdk.KEY_BackSpace) or (kv == Gdk.KEY_space and is_shift):
                    self.goto_pdf_page(self.current_pdf_page - 1)
                    return True

                # Next Page: Page_Down, KP_Page_Down, Next, Right, Space
                elif kv in (Gdk.KEY_Page_Down, Gdk.KEY_KP_Page_Down, Gdk.KEY_Next, Gdk.KEY_Right, Gdk.KEY_space):
                    self.goto_pdf_page(self.current_pdf_page + 1)
                    return True

                # First Page: Home, KP_Home
                elif kv in (Gdk.KEY_Home, Gdk.KEY_KP_Home):
                    self.goto_pdf_page(1)
                    return True

                # Last Page: End, KP_End
                elif kv in (Gdk.KEY_End, Gdk.KEY_KP_End):
                    if self.pdf_renderer and self.pdf_renderer.page_count > 0:
                        self.goto_pdf_page(self.pdf_renderer.page_count)
                    return True
        return False

    def _on_key_release(self, widget, event):
        kv = getattr(event, "keyval", 0)
        if kv in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            if self.get_view_mode() == "pdf":
                self._update_pdf_hover_cursor(is_ctrl=False)
            elif self.get_view_mode() == "source":
                self._update_source_cursor(False, self._source_hovered_url)
        return False

    # --------------------------------------------------------------------------
    # Mouse Cursor Grip & Drag Panning (when zoomed in)
    # --------------------------------------------------------------------------
    def _is_pdf_pannable(self) -> bool:
        """Check if PDF page can be panned (page exceeds viewport in either dimension)."""
        if self.get_view_mode() != "pdf":
            return False
        if getattr(self, "_fit_page_mode", False):
            return False
        if hasattr(self, "scrolled_pdf"):
            vadj = self.scrolled_pdf.get_vadjustment()
            hadj = self.scrolled_pdf.get_hadjustment()
            v_pannable = bool(vadj and (vadj.get_upper() - vadj.get_lower() > vadj.get_page_size() + 2))
            h_pannable = bool(hadj and (hadj.get_upper() - hadj.get_lower() > hadj.get_page_size() + 2))
            if v_pannable or h_pannable:
                return True
            if vadj and hadj and (vadj.get_page_size() > 10 or hadj.get_page_size() > 10):
                return False

        # Fallback if allocations not ready: check pixel dimensions vs viewport
        scale = self.ZOOM_STEPS[self.zoom_index]
        if hasattr(self, "scrolled_pdf"):
            alloc = self.scrolled_pdf.get_allocation()
            base_w = getattr(self, "_current_page_base_w", 0)
            base_h = getattr(self, "_current_page_base_h", 0)
            if alloc.width > 50 and alloc.height > 50 and base_w > 0 and base_h > 0:
                return (base_w * scale > alloc.width - 20) or (base_h * scale > alloc.height - 20)

        # Fallback for headless environments prior to initial allocation
        return self.zoom_index > self.DEFAULT_ZOOM_INDEX

    def _get_pan_windows(self):
        windows = []
        if hasattr(self, "pdf_event_box") and self.pdf_event_box.get_realized():
            w = self.pdf_event_box.get_window()
            if w and w not in windows:
                windows.append(w)
        if hasattr(self, "scrolled_pdf") and self.scrolled_pdf.get_realized():
            w = self.scrolled_pdf.get_window()
            if w and w not in windows:
                windows.append(w)
            child = self.scrolled_pdf.get_child()
            if child and child.get_realized():
                for getter in ("get_window", "get_bin_window", "get_view_window"):
                    if hasattr(child, getter):
                        try:
                            cw = getattr(child, getter)()
                            if cw and cw not in windows:
                                windows.append(cw)
                        except Exception:
                            pass
        return windows

    def _update_pan_cursor(self, gripping: bool = False):
        """Update cursor to open hand (grab), gripping fist (grabbing), or default."""
        if self.get_view_mode() != "pdf":
            return
        windows = self._get_pan_windows()
        if not windows:
            return

        is_pannable = self._is_pdf_pannable()
        for gdk_win in windows:
            try:
                disp = gdk_win.get_display()
                if not disp:
                    continue
                if gripping:
                    cursor = Gdk.Cursor.new_from_name(disp, "grabbing")
                    if not cursor:
                        cursor = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.FLEUR)
                    gdk_win.set_cursor(cursor)
                elif is_pannable:
                    cursor = Gdk.Cursor.new_from_name(disp, "grab")
                    if not cursor:
                        cursor = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.HAND1)
                    gdk_win.set_cursor(cursor)
                else:
                    gdk_win.set_cursor(None)
            except Exception:
                pass

    def _set_pdf_cursor(self, cursor_name: str):
        windows = self._get_pan_windows()
        for gdk_win in windows:
            try:
                disp = gdk_win.get_display()
                if disp:
                    cur = Gdk.Cursor.new_from_name(disp, cursor_name)
                    if not cur:
                        if cursor_name == "pointer":
                            cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.HAND2)
                        elif cursor_name == "xterm":
                            cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.XTERM)
                        elif cursor_name == "grab":
                            cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.HAND1)
                        elif cursor_name == "grabbing":
                            cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.FLEUR)
                    gdk_win.set_cursor(cur)
            except Exception:
                pass

    def _get_event_button(self, event) -> int:
        """Safely extract integer mouse button index (1=Left, 2=Middle, 3=Right)."""
        if hasattr(event, "get_button"):
            try:
                res = event.get_button()
                if res and res[0]:
                    return int(res[1])
            except Exception:
                pass
        btn = getattr(event, "button", 0)
        if hasattr(btn, "button"):
            try:
                return int(btn.button)
            except Exception:
                pass
        if isinstance(btn, (int, float)):
            return int(btn)
        return 0

    def _get_event_root_coords(self, event):
        """Safely extract root screen coordinates (rx, ry) from button or motion events."""
        if hasattr(event, "get_root_coords"):
            try:
                res = event.get_root_coords()
                if res is not None:
                    if len(res) == 3 and res[0]:
                        return float(res[1]), float(res[2])
                    elif len(res) == 2:
                        return float(res[0]), float(res[1])
            except Exception:
                pass
        if hasattr(event, "button") and hasattr(event.button, "x_root"):
            return float(event.button.x_root), float(event.button.y_root)
        if hasattr(event, "motion") and hasattr(event.motion, "x_root"):
            return float(event.motion.x_root), float(event.motion.y_root)
        x = getattr(event, "x_root", 0.0)
        y = getattr(event, "y_root", 0.0)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return float(x), float(y)
        return 0.0, 0.0

    # --------------------------------------------------------------------------
    # PDF Coordinates, Text Layout, and Link Detection
    # --------------------------------------------------------------------------
    def _get_pdf_page_metrics(self) -> Optional[Tuple[float, float, int, int, float, float]]:
        """Return (pix_x, pix_y, pw, ph, page_w, page_h) for currently rendered PDF page."""
        if not self.pdf_renderer or self.pdf_renderer.page_count == 0:
            return None
        pix = self.pdf_image.get_pixbuf()
        if not pix:
            return None
        pw = pix.get_width()
        ph = pix.get_height()
        page_w, page_h = self.pdf_renderer.get_page_size(self.current_pdf_page)
        if page_w <= 0 or page_h <= 0 or pw <= 0 or ph <= 0:
            return None

        img_alloc = self.pdf_image.get_allocation()
        pix_x = img_alloc.x + max(0, int((img_alloc.width - pw) * 0.5))
        pix_y = img_alloc.y + max(0, int((img_alloc.height - ph) * 0.5))
        return (float(pix_x), float(pix_y), int(pw), int(ph), float(page_w), float(page_h))

    def _event_to_pdf_coords(self, widget, event) -> Optional[Tuple[float, float]]:
        """Convert widget event coordinates to PDF page points (0,0 at top-left)."""
        metrics = self._get_pdf_page_metrics()
        if not metrics:
            return None
        pix_x, pix_y, pw, ph, page_w, page_h = metrics

        ex = float(getattr(event, "x", 0.0))
        ey = float(getattr(event, "y", 0.0))
        if widget != self.pdf_event_box and hasattr(widget, "translate_coordinates"):
            pt = widget.translate_coordinates(self.pdf_event_box, int(ex), int(ey))
            if pt is not None:
                ex, ey = float(pt[0]), float(pt[1])

        off_x = ex - pix_x
        off_y = ey - pix_y

        if off_x < -20 or off_x > pw + 20 or off_y < -20 or off_y > ph + 20:
            return None

        clamped_x = max(0.0, min(float(pw), off_x))
        clamped_y = max(0.0, min(float(ph), off_y))
        pdf_x = clamped_x / (pw / page_w)
        pdf_y = clamped_y / (ph / page_h)
        return (pdf_x, pdf_y)

    def _find_char_at(self, pdf_x: float, pdf_y: float) -> Optional[int]:
        if not self._page_char_rects:
            return None
        # 1. Exact character box match
        for i, (x1, y1, x2, y2) in enumerate(self._page_char_rects):
            if x1 <= pdf_x <= x2 and y1 <= pdf_y <= y2:
                return i
        # 2. Line match with vertical tolerance (+- 4pt)
        best_i = None
        min_dx = 1e9
        for i, (x1, y1, x2, y2) in enumerate(self._page_char_rects):
            if (y1 - 4.0) <= pdf_y <= (y2 + 4.0):
                if pdf_x < x1:
                    dx = x1 - pdf_x
                elif pdf_x > x2:
                    dx = pdf_x - x2
                else:
                    dx = 0.0
                if dx < min_dx:
                    min_dx = dx
                    best_i = i
        if best_i is not None and min_dx < 30.0:
            return best_i
        return None

    def _find_link_at(self, pdf_x: float, pdf_y: float) -> Optional[dict]:
        if not self._page_links:
            return None
        for link in self._page_links:
            lx1, ly1, lx2, ly2 = link["rect"]
            if lx1 <= pdf_x <= lx2 and ly1 <= pdf_y <= ly2:
                return link
        return None

    def _set_pdf_selection(self, start: int, end: int):
        if not self._page_text:
            self._clear_pdf_selection()
            return
        start = max(0, min(start, len(self._page_text)))
        end = max(0, min(end, len(self._page_text)))
        if start == end:
            self._clear_pdf_selection()
            return
        if start > end:
            start, end = end, start
        self._selected_range = (start, end)
        self._selected_text = self._page_text[start:end]

    def _clear_pdf_selection(self):
        self._selected_range = None
        self._selected_text = ""
        self._is_selecting_text = False
        self._sel_anchor_idx = None

    def _expand_to_word(self, idx: int) -> Tuple[int, int]:
        if not self._page_text or idx < 0 or idx >= len(self._page_text):
            return (0, 0)
        delims = r' ' + "\t\r\n.,;:!?'\"()[]{}<>/\\|@#$%^&*+-="
        if self._page_text[idx] in " \t\r\n":
            return (idx, idx + 1)
        left = idx
        while left > 0 and self._page_text[left - 1] not in delims:
            left -= 1
        right = idx
        while right < len(self._page_text) and self._page_text[right] not in delims:
            right += 1
        return (left, max(left + 1, right))

    def _expand_to_line(self, idx: int) -> Tuple[int, int]:
        if not self._page_text or idx < 0 or idx >= len(self._page_text):
            return (0, 0)
        left = self._page_text.rfind("\n", 0, idx)
        left = 0 if left == -1 else left + 1
        right = self._page_text.find("\n", idx)
        right = len(self._page_text) if right == -1 else right
        return (left, max(left + 1, right))

    def _on_draw_pdf_overlay(self, widget, cr):
        """Render glowing selection rectangles and hovered link underlines over the PDF page."""
        metrics = self._get_pdf_page_metrics()
        if not metrics:
            return False
        pix_x, pix_y, pw, ph, page_w, page_h = metrics
        sx = pw / page_w
        sy = ph / page_h

        # 1. Text Selection highlight
        if self._selected_range and self._page_char_rects:
            start, end = self._selected_range
            rects = self._page_char_rects[start:end]
            line_boxes = []
            for rx1, ry1, rx2, ry2 in rects:
                merged = False
                for lb in line_boxes:
                    if not (ry2 < lb[1] or ry1 > lb[3]):
                        lb[0] = min(lb[0], rx1)
                        lb[1] = min(lb[1], ry1)
                        lb[2] = max(lb[2], rx2)
                        lb[3] = max(lb[3], ry2)
                        merged = True
                        break
                if not merged:
                    line_boxes.append([rx1, ry1, rx2, ry2])

            for bx1, by1, bx2, by2 in line_boxes:
                wx = pix_x + bx1 * sx
                wy = pix_y + by1 * sy
                ww = max(2.0, (bx2 - bx1) * sx)
                wh = max(2.0, (by2 - by1) * sy)

                cr.set_source_rgba(0.0, 0.85, 1.0, 0.32)
                cr.rectangle(wx, wy, ww, wh)
                cr.fill()

                cr.set_source_rgba(0.0, 0.90, 1.0, 0.65)
                cr.set_line_width(1.0)
                cr.rectangle(wx, wy, ww, wh)
                cr.stroke()

        # 2. Hovered hyperlink glow & underline
        if getattr(self, "_hovered_link", None):
            lx1, ly1, lx2, ly2 = self._hovered_link["rect"]
            wx = pix_x + lx1 * sx
            wy = pix_y + ly1 * sy
            ww = max(2.0, (lx2 - lx1) * sx)
            wh = max(2.0, (ly2 - ly1) * sy)

            cr.set_source_rgba(0.0, 0.90, 1.0, 0.15)
            cr.rectangle(wx, wy, ww, wh)
            cr.fill()

            cr.set_source_rgba(0.0, 0.95, 1.0, 0.90)
            cr.set_line_width(1.5)
            cr.move_to(wx, wy + wh)
            cr.line_to(wx + ww, wy + wh)
            cr.stroke()

        return False

    def _update_pdf_hover_cursor(self, is_ctrl: Optional[bool] = None):
        """Update cursor and link highlight when Ctrl key is pressed or released."""
        if self.get_view_mode() != "pdf":
            return
        if not hasattr(self, "pdf_event_box") or not self.pdf_event_box.get_realized():
            return
        win = self.pdf_event_box.get_window()
        if not win:
            return
        disp = win.get_display()
        seat = disp.get_default_seat() if hasattr(disp, "get_default_seat") else None
        dev = seat.get_pointer() if seat else None
        if not dev:
            return
        _screen, x, y, mask = win.get_device_position(dev)
        if is_ctrl is None:
            is_ctrl = bool(mask & Gdk.ModifierType.CONTROL_MASK)

        metrics = self._get_pdf_page_metrics()
        if not metrics:
            return
        pix_x, pix_y, pw, ph, page_w, page_h = metrics
        off_x = x - pix_x
        off_y = y - pix_y
        if 0 <= off_x <= pw and 0 <= off_y <= ph:
            px = off_x / (pw / page_w)
            py = off_y / (ph / page_h)
            link = self._find_link_at(px, py)
            if link:
                if is_ctrl:
                    self._set_pdf_cursor("pointer")
                    self.pdf_event_box.set_tooltip_text(f"Follow link: {link['target']}")
                    if self._hovered_link != link:
                        self._hovered_link = link
                        self.pdf_event_box.queue_draw()
                    return
                else:
                    self.pdf_event_box.set_tooltip_text(f"Ctrl + Click to follow link: {link['target']}")
                    if self._hovered_link is not None:
                        self._hovered_link = None
                        self.pdf_event_box.queue_draw()
            else:
                self.pdf_event_box.set_tooltip_text(None)
                if self._hovered_link is not None:
                    self._hovered_link = None
                    self.pdf_event_box.queue_draw()

            ci = self._find_char_at(px, py)
            if ci is not None:
                self._set_pdf_cursor("xterm")
                return

        self._update_pan_cursor(gripping=False)

    def _copy_to_clipboard(self, text: str):
        if not text:
            return
        clip = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clip.set_text(text, -1)

    def _select_all_pdf(self):
        if self._page_text:
            self._set_pdf_selection(0, len(self._page_text))
            try:
                clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
                clip.set_text(self._selected_text, -1)
            except Exception:
                pass
            if hasattr(self, "pdf_event_box"):
                self.pdf_event_box.queue_draw()

    def _show_pdf_context_menu(self, event, pdf_x: Optional[float] = None, pdf_y: Optional[float] = None):
        menu = Gtk.Menu()

        link = self._find_link_at(pdf_x, pdf_y) if pdf_x is not None and pdf_y is not None else None
        if link:
            item_open = Gtk.MenuItem(label="Open Link")
            target = link["target"]
            l_type = link["type"]
            if l_type == "uri":
                item_open.connect("activate", lambda i: self.open_hyperlink(target))
            else:
                item_open.connect("activate", lambda i: self.goto_pdf_page(target))
            menu.append(item_open)

            item_copy_link = Gtk.MenuItem(label="Copy Link Address")
            item_copy_link.connect("activate", lambda i: self._copy_to_clipboard(str(target)))
            menu.append(item_copy_link)

            menu.append(Gtk.SeparatorMenuItem())

        item_copy = Gtk.MenuItem(label="Copy")
        item_copy.set_sensitive(bool(self._selected_text))
        item_copy.connect("activate", lambda i: self._copy_to_clipboard(self._selected_text))
        menu.append(item_copy)

        item_sel_all = Gtk.MenuItem(label="Select All")
        item_sel_all.connect("activate", lambda i: self._select_all_pdf())
        menu.append(item_sel_all)

        menu.show_all()
        if hasattr(menu, "popup_at_pointer"):
            menu.popup_at_pointer(event)
        else:
            menu.popup(None, None, None, None, 3, Gtk.get_current_event_time())

    def _on_pan_button_press(self, widget, event):
        """Handle clicks for text selection, Ctrl+Click hyperlinks, and drag panning."""
        if self.get_view_mode() != "pdf":
            return False

        btn = self._get_event_button(event)
        coords = self._event_to_pdf_coords(widget, event)
        pdf_x = coords[0] if coords else None
        pdf_y = coords[1] if coords else None

        # Right-click context menu
        if btn == 3:
            self._show_pdf_context_menu(event, pdf_x, pdf_y)
            return True

        state = getattr(event, "state", 0)
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        # Ctrl + Click to follow link
        if btn == 1 and is_ctrl and coords:
            link = self._find_link_at(coords[0], coords[1])
            if link:
                if link["type"] == "uri":
                    self.open_hyperlink(link["target"])
                    return True
                elif link["type"] == "page":
                    self.goto_pdf_page(link["target"])
                    return True

        ev_type = getattr(event, "type", None)
        # Double-click word selection
        if ev_type == Gdk.EventType._2BUTTON_PRESS and btn == 1 and coords:
            ci = self._find_char_at(coords[0], coords[1])
            if ci is not None:
                s, e = self._expand_to_word(ci)
                self._set_pdf_selection(s, e)
                try:
                    clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
                    clip.set_text(self._selected_text, -1)
                except Exception:
                    pass
                self.pdf_event_box.queue_draw()
                return True

        # Triple-click line selection
        if ev_type == Gdk.EventType._3BUTTON_PRESS and btn == 1 and coords:
            ci = self._find_char_at(coords[0], coords[1])
            if ci is not None:
                s, e = self._expand_to_line(ci)
                self._set_pdf_selection(s, e)
                try:
                    clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
                    clip.set_text(self._selected_text, -1)
                except Exception:
                    pass
                self.pdf_event_box.queue_draw()
                return True

        # Single Left Click: Check if clicking over text
        if btn == 1 and coords:
            ci = self._find_char_at(coords[0], coords[1])
            if ci is not None:
                self._is_selecting_text = True
                self._sel_anchor_idx = ci
                self._set_pdf_selection(ci, ci + 1)
                self.pdf_event_box.queue_draw()
                return True
            else:
                self._clear_pdf_selection()
                self.pdf_event_box.queue_draw()

        # Middle click (btn 2) or left click on margin/empty space: initiate pan if pannable
        if btn in (1, 2) and self._is_pdf_pannable():
            self._is_panning = True
            rx, ry = self._get_event_root_coords(event)
            self._pan_start_x = rx
            self._pan_start_y = ry
            vadj = self.scrolled_pdf.get_vadjustment()
            hadj = self.scrolled_pdf.get_hadjustment()
            self._pan_start_v_val = vadj.get_value() if vadj else 0.0
            self._pan_start_h_val = hadj.get_value() if hadj else 0.0
            self._pan_has_moved = False
            self._update_pan_cursor(gripping=True)
            return True

        return False

    def _on_pan_motion_notify(self, widget, event):
        """Move the page while gripping, expand text selection while dragging, or update hover cursor."""
        if self.get_view_mode() != "pdf":
            return False

        # 1. Panning in progress
        if getattr(self, "_is_panning", False):
            rx, ry = self._get_event_root_coords(event)
            dx = rx - self._pan_start_x
            dy = ry - self._pan_start_y
            if abs(dx) > 1 or abs(dy) > 1:
                self._pan_has_moved = True

            vadj = self.scrolled_pdf.get_vadjustment()
            hadj = self.scrolled_pdf.get_hadjustment()

            if vadj:
                v_min = vadj.get_lower()
                v_max = max(v_min, vadj.get_upper() - vadj.get_page_size())
                target_v = self._pan_start_v_val - dy
                vadj.set_value(max(v_min, min(target_v, v_max)))

            if hadj:
                h_min = hadj.get_lower()
                h_max = max(h_min, hadj.get_upper() - hadj.get_page_size())
                target_h = self._pan_start_h_val - dx
                hadj.set_value(max(h_min, min(target_h, h_max)))

            return True

        # 2. Text selection in progress
        if getattr(self, "_is_selecting_text", False):
            coords = self._event_to_pdf_coords(widget, event)
            if coords:
                ci = self._find_char_at(coords[0], coords[1])
                if ci is not None and self._sel_anchor_idx is not None:
                    start = min(self._sel_anchor_idx, ci)
                    end = max(self._sel_anchor_idx, ci) + 1
                    self._set_pdf_selection(start, end)
                    self.pdf_event_box.queue_draw()
            return True

        # 3. Hovering over PDF (link hover, text I-beam, or pan grab hand)
        coords = self._event_to_pdf_coords(widget, event)
        state = getattr(event, "state", 0)
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if coords:
            link = self._find_link_at(coords[0], coords[1])
            if link:
                if is_ctrl:
                    self._set_pdf_cursor("pointer")
                    self.pdf_event_box.set_tooltip_text(f"Follow link: {link['target']}")
                    if self._hovered_link != link:
                        self._hovered_link = link
                        self.pdf_event_box.queue_draw()
                    return False
                else:
                    self.pdf_event_box.set_tooltip_text(f"Ctrl + Click to follow link: {link['target']}")
                    if self._hovered_link is not None:
                        self._hovered_link = None
                        self.pdf_event_box.queue_draw()
            else:
                self.pdf_event_box.set_tooltip_text(None)
                if self._hovered_link is not None:
                    self._hovered_link = None
                    self.pdf_event_box.queue_draw()

            ci = self._find_char_at(coords[0], coords[1])
            if ci is not None:
                self._set_pdf_cursor("xterm")
                return False

        self._update_pan_cursor(gripping=False)
        return False

    def _on_pan_button_release(self, widget, event):
        """Release the gripped page or finalize text selection."""
        if getattr(self, "_is_panning", False):
            self._is_panning = False
            self._update_pan_cursor(gripping=False)
            return True

        if getattr(self, "_is_selecting_text", False):
            self._is_selecting_text = False
            if self._selected_text:
                try:
                    clip = Gtk.Clipboard.get(Gdk.SELECTION_PRIMARY)
                    clip.set_text(self._selected_text, -1)
                except Exception:
                    pass
            return True

        return False

    def _on_pan_enter_notify(self, widget, event):
        if self.get_view_mode() == "pdf":
            self._update_pan_cursor(gripping=getattr(self, "_is_panning", False))
        return False

    def _on_pan_leave_notify(self, widget, event):
        if getattr(self, "_hovered_link", None) is not None:
            self._hovered_link = None
            if hasattr(self, "pdf_event_box"):
                self.pdf_event_box.queue_draw()
                self.pdf_event_box.set_tooltip_text(None)
        return False

    def _on_pan_grab_broken(self, widget, event):
        if getattr(self, "_is_panning", False):
            self._is_panning = False
            self._update_pan_cursor(gripping=False)
        if getattr(self, "_is_selecting_text", False):
            self._is_selecting_text = False
        return False

    # --------------------------------------------------------------------------
    # Universal Hyperlink Navigation (Ctrl + Click)
    # --------------------------------------------------------------------------
    def open_hyperlink(self, target_url: str):
        """Open web links in default system browser, or local files in a new DocuPanel tab."""
        if not target_url:
            return
        target_str = str(target_url).strip()

        # If it's a local file URL or file path
        parsed_path = None
        if target_str.startswith("file://"):
            p = urllib.parse.urlparse(target_str).path
            parsed_path = urllib.parse.unquote(p)
        elif target_str.startswith(("/", "./", "../", "~/")):
            parsed_path = os.path.abspath(os.path.expanduser(target_str))
        elif not (target_str.startswith("http://") or target_str.startswith("https://") or target_str.startswith("mailto:")):
            if self.file_path:
                candidate = os.path.abspath(os.path.join(os.path.dirname(self.file_path), target_str))
                if os.path.exists(candidate):
                    parsed_path = candidate

        if parsed_path and os.path.exists(parsed_path):
            self.emit("open-file-requested", parsed_path)
            return

        # Otherwise launch default web browser or application via Gio / webbrowser
        try:
            Gio.AppInfo.launch_default_for_uri(target_str, None)
        except Exception:
            try:
                import webbrowser
                webbrowser.open(target_str)
            except Exception:
                pass

    def _find_source_url_at(self, x: int, y: int) -> Optional[str]:
        """Find URL or markdown link under source view buffer coordinates (x, y)."""
        bx, by = self.source_view.window_to_buffer_coords(Gtk.TextWindowType.TEXT, x, y)
        res = self.source_view.get_iter_at_location(bx, by)
        iter_at = res[1] if isinstance(res, tuple) else res
        if not iter_at:
            return None
        line_start = iter_at.copy()
        line_start.set_line_offset(0)
        line_end = iter_at.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()
        line_text = self.source_buffer.get_text(line_start, line_end, False)
        char_offset = iter_at.get_line_offset()

        # Check markdown links first: [text](target)
        for m in self._MD_LINK_REGEX.finditer(line_text):
            if m.start() <= char_offset <= m.end():
                return m.group(2).strip()

        # Check direct URLs
        for m in self._URL_REGEX.finditer(line_text):
            if m.start() <= char_offset <= m.end():
                return m.group(0).strip()

        return None

    def _update_source_cursor(self, is_ctrl: bool, url: Optional[str] = None):
        if is_ctrl and url:
            self.source_view.set_tooltip_text(f"Ctrl + Click to follow link: {url}")
        else:
            self.source_view.set_tooltip_text(None)

        gdk_win = self.source_view.get_window(Gtk.TextWindowType.TEXT)
        if not gdk_win:
            return
        disp = gdk_win.get_display()
        if is_ctrl and url:
            cur = Gdk.Cursor.new_from_name(disp, "pointer")
            if not cur:
                cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.HAND2)
            gdk_win.set_cursor(cur)
        else:
            cur = Gdk.Cursor.new_from_name(disp, "xterm")
            if not cur:
                cur = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.XTERM)
            gdk_win.set_cursor(cur)

    def _on_source_motion_notify(self, widget, event):
        state = getattr(event, "state", 0)
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        url = self._find_source_url_at(int(event.x), int(event.y))
        self._source_hovered_url = url
        self._update_source_cursor(is_ctrl, url)
        return False

    def _on_source_button_press(self, widget, event):
        btn = self._get_event_button(event)
        state = getattr(event, "state", 0)
        is_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if btn == 1 and is_ctrl:
            url = self._find_source_url_at(int(event.x), int(event.y))
            if url:
                self.open_hyperlink(url)
                return True
        return False

    def _on_source_key_press(self, widget, event):
        kv = getattr(event, "keyval", 0)
        if kv in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            if self._source_hovered_url:
                self._update_source_cursor(True, self._source_hovered_url)
        return False

    def _on_source_key_release(self, widget, event):
        kv = getattr(event, "keyval", 0)
        if kv in (Gdk.KEY_Control_L, Gdk.KEY_Control_R):
            self._update_source_cursor(False, self._source_hovered_url)
        return False

    def _on_source_leave_notify(self, widget, event):
        self._source_hovered_url = None
        self._update_source_cursor(False, None)
        return False

    def _on_source_populate_popup(self, view, menu):
        if self._source_hovered_url:
            sep = Gtk.SeparatorMenuItem()
            menu.prepend(sep)
            item_copy = Gtk.MenuItem(label="Copy Link Address")
            item_copy.connect("activate", lambda i: self._copy_to_clipboard(self._source_hovered_url))
            menu.prepend(item_copy)
            item_open = Gtk.MenuItem(label="Open Link")
            item_open.connect("activate", lambda i: self.open_hyperlink(self._source_hovered_url))
            menu.prepend(item_open)
            menu.show_all()

    def _on_web_view_decide_policy(self, web_view, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            try:
                action = decision.get_navigation_action() if hasattr(decision, "get_navigation_action") else None
                if action:
                    nav_type = action.get_navigation_type()
                    req = action.get_request()
                else:
                    nav_type = decision.get_navigation_type()
                    req = decision.get_request()
                uri = req.get_uri() if req else None
                if nav_type == WebKit2.NavigationType.LINK_CLICKED and uri:
                    decision.ignore()
                    self.open_hyperlink(uri)
                    return True
            except Exception:
                pass
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
                self._refresh_pdf_page(sync=True)
                self.scrolled_pdf.show_all()
                self._update_pan_cursor(gripping=False)
            else:
                self.refresh_rendered_preview()
                self.view_stack.set_visible_child_name("rendered")
        else:
            self._is_panning = False
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

    def get_outline_items(self) -> Tuple[List, str]:
        """Return (HeadingItems, source_type) where PDF uses native bookmarks or autopopulated chapters."""
        text = self.get_text()
        if self.doc_type == "pdf" and self.pdf_renderer and self.pdf_renderer.page_count > 0:
            items, source = self.pdf_renderer.get_outline_with_source(fallback_text=text)
            self.outline_source = source
            return items, source
        items = DocumentParser.extract_outline(
            text,
            doc_type=self.doc_type,
            pdf_page_count=self.total_pdf_pages,
            pdf_renderer=self.pdf_renderer,
        )
        self.outline_source = "headings"
        return items, "headings"

    def goto_pdf_page(self, page_num: int):
        if not self.pdf_renderer or self.pdf_renderer.page_count == 0:
            return
        self._is_panning = False
        new_page = max(1, min(int(page_num), self.pdf_renderer.page_count))
        if new_page != self.current_pdf_page:
            self._current_pdf_pixbuf = None
        self.current_pdf_page = new_page

        if getattr(self, "_fit_page_mode", False):
            try:
                pw, ph = self.pdf_renderer.get_page_size(self.current_pdf_page)
                curr_base_w = pw * 1.333
                curr_base_h = ph * 1.333
                if abs(curr_base_w - getattr(self, "_current_page_base_w", 0)) > 25 or abs(curr_base_h - getattr(self, "_current_page_base_h", 0)) > 25:
                    self.fit_to_page()
                    self.emit("pdf-page-changed", self.current_pdf_page)
                    return
            except Exception:
                pass

        self._refresh_pdf_page(sync=True)

        if hasattr(self, "scrolled_pdf"):
            vadj = self.scrolled_pdf.get_vadjustment()
            if vadj:
                vadj.set_value(vadj.get_lower())
            hadj = self.scrolled_pdf.get_hadjustment()
            if hadj:
                hadj.set_value(hadj.get_lower())
        self._update_pan_cursor(gripping=False)
        self.emit("pdf-page-changed", self.current_pdf_page)

    def _refresh_pdf_page(self, sync: bool = False):
        if not self.pdf_renderer:
            return
        scale = self.ZOOM_STEPS[self.zoom_index]
        pct = self.get_zoom_percent()
        self.lbl_pdf_page.set_text(f"Page {self.current_pdf_page} / {self.pdf_renderer.page_count}  ({pct}%)")
        self.btn_pdf_prev.set_sensitive(self.current_pdf_page > 1)
        self.btn_pdf_next.set_sensitive(self.current_pdf_page < self.pdf_renderer.page_count)

        # Update text layout and links for current page
        try:
            self._page_text, self._page_char_rects = self.pdf_renderer.get_page_text_layout(self.current_pdf_page)
            self._page_links = self.pdf_renderer.get_page_links(self.current_pdf_page)
        except Exception:
            self._page_text, self._page_char_rects, self._page_links = "", [], []
        self._clear_pdf_selection()
        if hasattr(self, "pdf_event_box"):
            self.pdf_event_box.queue_draw()

        if sync:
            timer = getattr(self, "_pdf_debounce_timer", None)
            if timer:
                try:
                    GLib.source_remove(timer)
                except Exception:
                    pass
                self._pdf_debounce_timer = None

            pix = self.pdf_renderer.render_page_pixbuf(self.current_pdf_page, scale)
            if pix is not None:
                self._current_pdf_pixbuf = pix
                self._current_page_base_w = max(10.0, pix.get_width() / scale)
                self._current_page_base_h = max(10.0, pix.get_height() / scale)
                self.pdf_image.set_from_pixbuf(pix)
                if hasattr(self, "pdf_event_box"):
                    self.pdf_event_box.queue_draw()
                self._update_pan_cursor(gripping=getattr(self, "_is_panning", False))
            return

        # 1. Instant zero-latency visual scaling of existing pixbuf (1ms smooth feedback)
        if getattr(self, "_current_pdf_pixbuf", None) is not None and getattr(self, "_current_page_base_w", 0) > 0:
            target_w = max(10, int(round(self._current_page_base_w * scale)))
            target_h = max(10, int(round(self._current_page_base_h * scale)))
            cur_pix = self.pdf_image.get_pixbuf()
            if cur_pix is not None and abs(cur_pix.get_width() - target_w) > 3:
                try:
                    quick_scaled = self._current_pdf_pixbuf.scale_simple(
                        target_w, target_h, GdkPixbuf.InterpType.BILINEAR
                    )
                    if quick_scaled:
                        self.pdf_image.set_from_pixbuf(quick_scaled)
                except Exception:
                    pass

        # 2. Debounced asynchronous razor-sharp vector render (runs in background thread)
        self._pdf_render_gen = getattr(self, "_pdf_render_gen", 0) + 1
        gen = self._pdf_render_gen

        timer = getattr(self, "_pdf_debounce_timer", None)
        if timer:
            try:
                GLib.source_remove(timer)
            except Exception:
                pass
            self._pdf_debounce_timer = None

        def _do_render():
            self._pdf_debounce_timer = None
            page = self.current_pdf_page
            self.pdf_renderer.render_page_async(
                page,
                scale,
                lambda p_num, sc, pix: self._on_pdf_rendered(gen, p_num, sc, pix)
            )
            return False

        self._pdf_debounce_timer = GLib.timeout_add(30, _do_render)

    def _on_pdf_rendered(self, gen: int, page_num: int, scale: float, pixbuf: Optional[GdkPixbuf.Pixbuf]):
        if gen != getattr(self, "_pdf_render_gen", 0) or page_num != self.current_pdf_page or pixbuf is None:
            return
        self._current_pdf_pixbuf = pixbuf
        self._current_page_base_w = max(10.0, pixbuf.get_width() / scale)
        self._current_page_base_h = max(10.0, pixbuf.get_height() / scale)
        self.pdf_image.set_from_pixbuf(pixbuf)
        if hasattr(self, "pdf_event_box"):
            self.pdf_event_box.queue_draw()
        self._update_pan_cursor(gripping=getattr(self, "_is_panning", False))

    # --------------------------------------------------------------------------
    # Retina Vision & Font Scaling
    # --------------------------------------------------------------------------
    def get_zoom_percent(self) -> int:
        return int(round(self.ZOOM_STEPS[self.zoom_index] * 100))

    def can_zoom_in(self) -> bool:
        return self.zoom_index < len(self.ZOOM_STEPS) - 1

    def can_zoom_out(self) -> bool:
        return self.zoom_index > 0

    def _on_pdf_size_allocate(self, widget, allocation):
        if getattr(self, "_fit_page_mode", False):
            cur_w = allocation.width
            cur_h = allocation.height
            last_w = getattr(self, "_last_allocated_w", 0)
            last_h = getattr(self, "_last_allocated_h", 0)
            if abs(cur_w - last_w) > 20 or abs(cur_h - last_h) > 20:
                self._last_allocated_w = cur_w
                self._last_allocated_h = cur_h
                self.fit_to_page()
                return
        self._update_pan_cursor(gripping=getattr(self, "_is_panning", False))

    def fit_to_page(self):
        """Scale PDF page so the entire page fits cleanly in the viewport (Full Page View)."""
        self._fit_page_mode = True
        if self.get_view_mode() != "pdf" or not self.pdf_renderer or self.pdf_renderer.page_count == 0:
            return

        alloc = self.scrolled_pdf.get_allocation()
        view_w = alloc.width
        view_h = alloc.height

        if view_w <= 50 or view_h <= 50:
            self._fit_page_pending = True
            disp = Gdk.Display.get_default()
            if disp:
                mon = disp.get_primary_monitor() or (disp.get_monitor(0) if disp.get_n_monitors() > 0 else None)
                if mon:
                    geom = mon.get_geometry()
                    view_w = max(400, geom.width - 48)
                    view_h = max(300, geom.height - 180)
                else:
                    view_w, view_h = 1000, 700
            else:
                view_w, view_h = 1000, 700

        try:
            pw, ph = self.pdf_renderer.get_page_size(self.current_pdf_page)
            base_w = pw * 1.333
            base_h = ph * 1.333
            self._current_page_base_w = base_w
            self._current_page_base_h = base_h
        except Exception:
            base_w = getattr(self, "_current_page_base_w", 595.0 * 1.333)
            base_h = getattr(self, "_current_page_base_h", 842.0 * 1.333)

        if base_w <= 0 or base_h <= 0:
            return

        avail_w = max(50.0, float(view_w) - 48.0)
        avail_h = max(50.0, float(view_h) - 48.0)

        scale_w = avail_w / float(base_w)
        scale_h = avail_h / float(base_h)
        target_scale = min(scale_w, scale_h)

        best_idx = 0
        for i, step in enumerate(self.ZOOM_STEPS):
            if step <= target_scale:
                best_idx = i
            else:
                break

        if hasattr(self, "scrolled_pdf"):
            vadj = self.scrolled_pdf.get_vadjustment()
            if vadj:
                vadj.set_value(vadj.get_lower())
            hadj = self.scrolled_pdf.get_hadjustment()
            if hadj:
                hadj.set_value(hadj.get_lower())

        if self.zoom_index != best_idx:
            self.zoom_index = best_idx
            self.apply_retina_font()
            self.emit("zoom-changed", self.get_zoom_percent())
        else:
            self._refresh_pdf_page(sync=True)
        self._update_pan_cursor(gripping=False)

    def zoom_in(self):
        self._fit_page_mode = False
        if self.can_zoom_in():
            self.zoom_index += 1
            self.apply_retina_font()
            self.emit("zoom-changed", self.get_zoom_percent())

    def zoom_out(self):
        self._fit_page_mode = False
        if self.can_zoom_out():
            self.zoom_index -= 1
            self.apply_retina_font()
            self.emit("zoom-changed", self.get_zoom_percent())

    def zoom_reset(self):
        self._fit_page_mode = False
        self.zoom_index = self.DEFAULT_ZOOM_INDEX
        self.apply_retina_font()
        self.emit("zoom-changed", self.get_zoom_percent())

    def apply_retina_font(self):
        scale = self.ZOOM_STEPS[self.zoom_index]
        scaled_size_pt = max(6.0, self.base_font_size * scale)

        font_desc = Pango.FontDescription()
        font_desc.set_family(self.base_font_family)
        font_desc.set_size(int(scaled_size_pt * Pango.SCALE))
        self.source_view.override_font(font_desc)

        try:
            if self._font_css_provider is None:
                self._font_css_provider = Gtk.CssProvider()
                self.source_view.get_style_context().add_provider(
                    self._font_css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_USER
                )
            css_str = (
                f"textview, textview text {{ "
                f"font-family: \"{self.base_font_family}\", monospace; "
                f"font-size: {scaled_size_pt:.1f}pt; "
                f"}}"
            )
            self._font_css_provider.load_from_data(css_str.encode("utf-8"))
        except Exception:
            pass

        self.source_view.queue_draw()

        if HAS_WEBKIT and self.web_view is not None:
            self.web_view.set_zoom_level(scale)
        elif hasattr(self, "fallback_preview") and self.fallback_preview is not None:
            self.fallback_preview.override_font(font_desc)

        if self.get_view_mode() == "pdf":
            self._refresh_pdf_page()
            self._update_pan_cursor(gripping=False)

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
            for fallback in ("docupanel-neon", "oblivion", "solarized-dark", "classic"):
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
            self._fit_page_mode = True
            self._fit_page_pending = True
            self.fit_to_page()
            self._update_pan_cursor(gripping=False)
        elif doc_type in ("markdown", "rst", "html", "csv", "odt", "docx"):
            if doc_type in ("odt", "docx"):
                self.set_read_only(True)
            self.set_view_mode("rendered")
        else:
            self.set_view_mode("source")

        self._is_modified = False
        self.emit("content-modified", False)
        self.emit("zoom-changed", self.get_zoom_percent())
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
            fd, tmp_path = tempfile.mkstemp(prefix=".docupanel_save_", dir=target_dir)
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
