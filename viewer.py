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
from typing import Optional

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GtkSource', '4')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, GtkSource, Pango, GObject, GLib
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
        self.scrolled_pdf.connect("size-allocate", self._on_pdf_size_allocate)
        self.pdf_image = Gtk.Image()
        self.pdf_image.set_margin_top(16)
        self.pdf_image.set_margin_bottom(16)
        self.pdf_image.set_margin_start(16)
        self.pdf_image.set_margin_end(16)

        self.pdf_event_box = Gtk.EventBox()
        self.pdf_event_box.add(self.pdf_image)
        self.pdf_event_box.set_visible_window(True)

        pan_mask = (
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.ENTER_NOTIFY_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.pdf_event_box.add_events(pan_mask)
        self.pdf_event_box.connect("button-press-event", self._on_pan_button_press)
        self.pdf_event_box.connect("button-release-event", self._on_pan_button_release)
        self.pdf_event_box.connect("motion-notify-event", self._on_pan_motion_notify)
        self.pdf_event_box.connect("enter-notify-event", self._on_pan_enter_notify)
        self.pdf_event_box.connect("leave-notify-event", self._on_pan_leave_notify)
        self.pdf_event_box.connect("realize", lambda w: self._update_pan_cursor(gripping=False))

        self.scrolled_pdf.add_events(pan_mask)
        self.scrolled_pdf.connect("button-press-event", self._on_pan_button_press)
        self.scrolled_pdf.connect("button-release-event", self._on_pan_button_release)
        self.scrolled_pdf.connect("motion-notify-event", self._on_pan_motion_notify)
        self.scrolled_pdf.connect("enter-notify-event", self._on_pan_enter_notify)
        self.scrolled_pdf.connect("leave-notify-event", self._on_pan_leave_notify)
        self.scrolled_pdf.connect("realize", lambda w: self._update_pan_cursor(gripping=False))

        self.scrolled_pdf.add(self.pdf_event_box)
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

        if HAS_WEBKIT and self.web_view is not None:
            self.web_view.add_events(scroll_mask)
            self.web_view.connect("event", self._on_webkit_event)
            self.web_view.connect("scroll-event", self._on_scroll_event)

        self.source_view.connect_after("draw", self._on_draw_watermark)

    @classmethod
    def _get_watermark_pixbuf(cls, size: int = 340):
        """Load and cache the official DocuPad SVG (translucent blue open tome & doc on yoga pad)."""
        svg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docupad.svg")
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
        """Keyboard navigation for PDF pages (Page Up, Page Down, Arrow keys, Home, End)."""
        if self.get_view_mode() == "pdf":
            state = getattr(event, "state", 0)
            mod_mask = Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK
            if not (state & mod_mask):
                kv = getattr(event, "keyval", 0)
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

    # --------------------------------------------------------------------------
    # Mouse Cursor Grip & Drag Panning (when zoomed in)
    # --------------------------------------------------------------------------
    def _is_pdf_pannable(self) -> bool:
        """Check if PDF page can be panned (zoomed in or larger than viewport)."""
        if self.get_view_mode() != "pdf":
            return False
        if self.zoom_index > self.DEFAULT_ZOOM_INDEX:
            return True
        if hasattr(self, "scrolled_pdf"):
            vadj = self.scrolled_pdf.get_vadjustment()
            hadj = self.scrolled_pdf.get_hadjustment()
            if vadj and (vadj.get_upper() - vadj.get_lower() > vadj.get_page_size() + 2):
                return True
            if hadj and (hadj.get_upper() - hadj.get_lower() > hadj.get_page_size() + 2):
                return True
        return False

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

        for gdk_win in windows:
            disp = gdk_win.get_display()
            if not disp:
                continue
            if gripping:
                cursor = Gdk.Cursor.new_from_name(disp, "grabbing")
                if not cursor:
                    cursor = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.FLEUR)
                gdk_win.set_cursor(cursor)
            elif self._is_pdf_pannable():
                cursor = Gdk.Cursor.new_from_name(disp, "grab")
                if not cursor:
                    cursor = Gdk.Cursor.new_for_display(disp, Gdk.CursorType.HAND1)
                gdk_win.set_cursor(cursor)
            else:
                gdk_win.set_cursor(None)

    def _get_event_root_coords(self, event):
        if hasattr(event, "get_root_coords"):
            res = event.get_root_coords()
            if res and res[0]:
                return float(res[1]), float(res[2])
        return float(getattr(event, "x_root", 0.0)), float(getattr(event, "y_root", 0.0))

    def _on_pan_button_press(self, widget, event):
        """Grip the page on left or middle mouse click when zoomed in."""
        if self.get_view_mode() == "pdf" and getattr(event, "button", 0) in (1, 2):
            if self._is_pdf_pannable():
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
        """Move the page smoothly with the cursor while gripping."""
        if self.get_view_mode() == "pdf":
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
            else:
                self._update_pan_cursor(gripping=False)
        return False

    def _on_pan_button_release(self, widget, event):
        """Release the gripped page when mouse button is released."""
        if getattr(self, "_is_panning", False) and getattr(event, "button", 0) in (1, 2):
            self._is_panning = False
            self._update_pan_cursor(gripping=False)
            return True
        return False

    def _on_pan_enter_notify(self, widget, event):
        if self.get_view_mode() == "pdf" and not getattr(self, "_is_panning", False):
            self._update_pan_cursor(gripping=False)
        return False

    def _on_pan_leave_notify(self, widget, event):
        if not getattr(self, "_is_panning", False):
            for gdk_win in self._get_pan_windows():
                gdk_win.set_cursor(None)
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

    def _refresh_pdf_page(self, sync: bool = False):
        if not self.pdf_renderer:
            return
        scale = self.ZOOM_STEPS[self.zoom_index]
        pct = self.get_zoom_percent()
        self.lbl_pdf_page.set_text(f"Page {self.current_pdf_page} / {self.pdf_renderer.page_count}  ({pct}%)")
        self.btn_pdf_prev.set_sensitive(self.current_pdf_page > 1)
        self.btn_pdf_next.set_sensitive(self.current_pdf_page < self.pdf_renderer.page_count)

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

        avail_w = max(50.0, float(view_w) - 36.0)
        avail_h = max(50.0, float(view_h) - 36.0)

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
