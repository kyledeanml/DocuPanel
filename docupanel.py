#!/usr/bin/env python3
"""
DocuPanel - DocumentViewer & Markdown/PDF/Text Companion Application
With Retina Vision, Dark Neon Blue Theme, Table of Contents Outline Sidebar,
Rendered HTML & PDF Page Canvas, and Translucent DocuPanel Yoga-Pad Watermark.
Designed for Linux Mint Cinnamon alongside PythonPanel.
"""

import os
import sys
from typing import Optional

sys.dont_write_bytecode = True
import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GdkPixbuf', '2.0')
gi.require_version('GtkSource', '4')
gi.require_version('Pango', '1.0')

from gi.repository import Gtk, Gdk, GdkPixbuf, GtkSource, Pango, GLib, Gio
from viewer import DocViewer
from search_bar import SearchBar
from doc_tools import DocumentParser


class DocuPanelWindow(Gtk.ApplicationWindow):
    """Main application window for DocuPanel."""

    APP_NAME = "DocuPanel"
    APP_VERSION = "2.1"

    def __init__(self, app=None, initial_files=None):
        super().__init__(application=app, title=self.APP_NAME)
        self.set_default_size(1100, 740)
        self.set_position(Gtk.WindowPosition.CENTER)
        self._stats_timeout_id = None

        # Set application & window icon for Cinnamon panel, alt-tab, and taskbar
        self.set_wmclass("docupanel", "DocuPanel")
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docupanel.svg")
        if os.path.exists(icon_path):
            try:
                Gtk.Window.set_default_icon_from_file(icon_path)
                self.set_icon_from_file(icon_path)
                pixbufs = [
                    GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, sz, sz, True)
                    for sz in (16, 24, 32, 48, 64, 128, 256)
                ]
                Gtk.Window.set_default_icon_list(pixbufs)
                self.set_icon_list(pixbufs)
            except Exception:
                pass

        # Dark / Light mode state & neon theme
        self.dark_mode = True
        self.pdf_dark_mode = True
        self._theme_css_provider = None
        self._updating_theme_toggle = False
        self.current_theme = "docupanel-neon"
        self._apply_theme(self.dark_mode)

        # UI Construction
        self._build_header_bar()
        self._build_main_area()
        self._build_status_bar()
        self._setup_shortcuts()

        self.add_events(Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        self.connect("scroll-event", self._on_window_scroll_event)
        self.connect("key-press-event", self._on_window_key_press_event)
        self.connect("delete-event", self._on_delete_event)

        has_initial_files = bool(initial_files)
        if has_initial_files:
            self.maximize()

        if initial_files:
            for f in initial_files:
                self.new_tab(f)
        if self.notebook_tabs.get_n_pages() == 0:
            self.new_tab()

        self.show_all()
        if hasattr(self, "toc_box") and not self.btn_toggle_toc.get_active():
            self.toc_box.set_visible(False)
        self.search_bar.set_reveal_child(False)

    def _on_window_scroll_event(self, widget, event):
        viewer = self.get_current_viewer()
        if viewer is not None:
            return viewer._on_scroll_event(widget, event)
        return False

    def _on_window_key_press_event(self, widget, event):
        focus_w = self.get_focus()
        if isinstance(focus_w, Gtk.Entry):
            return False
        viewer = self.get_current_viewer()
        if viewer is not None:
            return viewer._on_key_press(widget, event)
        return False

    _UI_THEME_PALETTES = {
        True: {
            "win_bg": "#0b0f19",
            "win_fg": "#ffffff",
            "hb_grad": "linear-gradient(to bottom, #111827, #0b0f19)",
            "hb_border": "rgba(0, 240, 255, 0.35)",
            "hb_shadow": "0 1px 6px rgba(0, 240, 255, 0.12)",
            "title_shadow": "0 0 8px rgba(0, 240, 255, 0.45)",
            "subtitle_fg": "#38bdf8",
            "btn_bg": "#161f33",
            "btn_fg": "#ffffff",
            "btn_border": "rgba(56, 189, 248, 0.35)",
            "btn_hover_bg": "#1e2d4a",
            "btn_hover_fg": "#00f0ff",
            "btn_hover_border": "#00f0ff",
            "btn_checked_bg": "#0284c7",
            "btn_checked_fg": "#ffffff",
            "btn_checked_border": "#00f0ff",
            "nb_bg": "#080b12",
            "nb_border": "rgba(56, 189, 248, 0.25)",
            "tab_bg": "#0f1626",
            "tab_fg": "#bae6fd",
            "tab_active_bg": "#17233b",
            "tab_active_fg": "#ffffff",
            "entry_bg": "#0d1322",
            "entry_fg": "#ffffff",
            "entry_border": "rgba(56, 189, 248, 0.45)",
            "tree_bg": "#0a0e19",
            "tree_fg": "#ffffff",
            "tree_hdr_bg": "#111827",
            "tree_hdr_fg": "#38bdf8",
            "pop_bg": "#0f172a",
            "pop_fg": "#ffffff",
            "pop_border": "rgba(0, 240, 255, 0.4)",
            "sel_bg": "#0284c7",
            "sel_fg": "#ffffff",
            "dim_fg": "#94a3b8",
        },
        False: {
            "win_bg": "#f1f5f9",
            "win_fg": "#0f172a",
            "hb_grad": "linear-gradient(to bottom, #ffffff, #e2e8f0)",
            "hb_border": "rgba(2, 132, 199, 0.35)",
            "hb_shadow": "0 1px 4px rgba(15, 23, 42, 0.08)",
            "title_shadow": "none",
            "subtitle_fg": "#0284c7",
            "btn_bg": "#ffffff",
            "btn_fg": "#0f172a",
            "btn_border": "#cbd5e1",
            "btn_hover_bg": "#e0f2fe",
            "btn_hover_fg": "#0369a1",
            "btn_hover_border": "#0284c7",
            "btn_checked_bg": "#0284c7",
            "btn_checked_fg": "#ffffff",
            "btn_checked_border": "#0369a1",
            "nb_bg": "#e2e8f0",
            "nb_border": "#cbd5e1",
            "tab_bg": "#e2e8f0",
            "tab_fg": "#334155",
            "tab_active_bg": "#ffffff",
            "tab_active_fg": "#0f172a",
            "entry_bg": "#ffffff",
            "entry_fg": "#0f172a",
            "entry_border": "#94a3b8",
            "tree_bg": "#ffffff",
            "tree_fg": "#0f172a",
            "tree_hdr_bg": "#f1f5f9",
            "tree_hdr_fg": "#0369a1",
            "pop_bg": "#ffffff",
            "pop_fg": "#0f172a",
            "pop_border": "#cbd5e1",
            "sel_bg": "#0284c7",
            "sel_fg": "#ffffff",
            "dim_fg": "#475569",
        },
    }

    def _apply_dark_neon_theme(self):
        """Backward-compatible wrapper that applies the active dark-neon theme."""
        self._apply_theme(True)

    def _apply_theme(self, dark_mode: bool):
        """Apply unified Dark Neon or Crisp Light CSS across DocuPanel UI widgets."""
        dark_mode = bool(dark_mode)
        styles_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "styles")
        if os.path.exists(styles_dir):
            GtkSource.StyleSchemeManager.get_default().prepend_search_path(styles_dir)

        settings = Gtk.Settings.get_default()
        if settings:
            settings.set_property("gtk-application-prefer-dark-theme", dark_mode)

        self.dark_mode = dark_mode
        self.current_theme = "docupanel-neon" if dark_mode else "docupanel-light"

        p = self._UI_THEME_PALETTES[dark_mode]
        css = f"""
        window, dialog, .background, scrolledwindow, viewport {{
            background-color: {p["win_bg"]};
            color: {p["win_fg"]};
        }}

        headerbar {{
            background: {p["hb_grad"]};
            border-bottom: 1px solid {p["hb_border"]};
            box-shadow: {p["hb_shadow"]};
            color: {p["win_fg"]};
            padding: 4px 6px;
        }}

        headerbar .title {{
            color: {p["win_fg"]};
            font-weight: 700;
            letter-spacing: 0.4px;
            text-shadow: {p["title_shadow"]};
        }}

        headerbar .subtitle {{
            color: {p["subtitle_fg"]};
            font-size: 90%;
        }}

        headerbar button, button {{
            background-image: none;
            background-color: {p["btn_bg"]};
            color: {p["btn_fg"]};
            border: 1px solid {p["btn_border"]};
            border-radius: 5px;
        }}

        headerbar button:hover, button:hover {{
            background-color: {p["btn_hover_bg"]};
            border-color: {p["btn_hover_border"]};
            color: {p["btn_hover_fg"]};
            box-shadow: 0 0 8px rgba(0, 240, 255, 0.25);
        }}

        headerbar button:checked, headerbar button:active,
        button:checked, button:active {{
            background-color: {p["btn_checked_bg"]};
            border-color: {p["btn_checked_border"]};
            color: {p["btn_checked_fg"]};
        }}

        headerbar button:checked label, button:checked label {{
            color: {p["btn_checked_fg"]};
        }}

        notebook, notebook header {{
            background-color: {p["nb_bg"]};
            border-color: {p["nb_border"]};
        }}

        notebook tab {{
            background-color: {p["tab_bg"]};
            color: {p["tab_fg"]};
            padding: 4px 10px;
            border: 1px solid transparent;
        }}

        notebook tab:checked {{
            background-color: {p["tab_active_bg"]};
            color: {p["tab_active_fg"]};
            border-bottom: 2px solid {p["btn_hover_border"]};
            box-shadow: 0 -1px 8px rgba(0, 240, 255, 0.16);
        }}

        entry, searchentry {{
            background-color: {p["entry_bg"]};
            color: {p["entry_fg"]};
            border: 1px solid {p["entry_border"]};
            border-radius: 4px;
            caret-color: {p["btn_hover_border"]};
        }}

        entry:focus, searchentry:focus {{
            border-color: {p["btn_hover_border"]};
            box-shadow: 0 0 6px rgba(0, 240, 255, 0.3);
        }}

        treeview.view {{
            background-color: {p["tree_bg"]};
            color: {p["tree_fg"]};
        }}

        treeview.view:selected {{
            background-color: {p["sel_bg"]};
            color: {p["sel_fg"]};
        }}

        treeview.view header button {{
            background-color: {p["tree_hdr_bg"]};
            color: {p["tree_hdr_fg"]};
            font-weight: bold;
            border-bottom: 1px solid {p["hb_border"]};
        }}

        popover, menu, .popup {{
            background-color: {p["pop_bg"]};
            color: {p["pop_fg"]};
            border: 1px solid {p["pop_border"]};
        }}

        menuitem:hover, modelbutton:hover {{
            background-color: {p["sel_bg"]};
            color: {p["sel_fg"]};
        }}

        checkbutton, checkbutton label, label {{
            color: {p["win_fg"]};
        }}

        .dim-label {{
            color: {p["dim_fg"]};
        }}
        """.encode("utf-8")

        if self._theme_css_provider is None:
            self._theme_css_provider = Gtk.CssProvider()
            screen = Gdk.Screen.get_default()
            if screen:
                Gtk.StyleContext.add_provider_for_screen(
                    screen,
                    self._theme_css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
        try:
            self._theme_css_provider.load_from_data(css)
        except Exception:
            pass

        if hasattr(self, "_toc_page_renderer") and self._toc_page_renderer is not None:
            self._toc_page_renderer.set_property("foreground", "#00d4ff" if dark_mode else "#0284c7")

    # --------------------------------------------------------------------------
    # HeaderBar Construction
    # --------------------------------------------------------------------------
    def _build_header_bar(self):
        hb = Gtk.HeaderBar()
        hb.set_show_close_button(True)
        hb.set_title(self.APP_NAME)
        hb.set_subtitle("Document Viewer")
        self.header_bar = hb
        self.set_titlebar(hb)

        # Left Controls: New, Open, Save, Save As
        file_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        Gtk.StyleContext.add_class(file_box.get_style_context(), "linked")

        btn_new = Gtk.Button.new_from_icon_name("document-new-symbolic", Gtk.IconSize.BUTTON)
        btn_new.set_tooltip_text("New Document Tab (Ctrl+N / Ctrl+T)")
        btn_new.connect("clicked", lambda b: self.new_tab())
        file_box.pack_start(btn_new, False, False, 0)

        btn_open = Gtk.Button.new_from_icon_name("document-open-symbolic", Gtk.IconSize.BUTTON)
        btn_open.set_tooltip_text("Open Document (Ctrl+O)")
        btn_open.connect("clicked", lambda b: self.on_open())
        file_box.pack_start(btn_open, False, False, 0)

        btn_save = Gtk.Button.new_from_icon_name("document-save-symbolic", Gtk.IconSize.BUTTON)
        btn_save.set_tooltip_text("Save Document (Ctrl+S)")
        btn_save.connect("clicked", lambda b: self.on_save())
        file_box.pack_start(btn_save, False, False, 0)

        btn_save_as = Gtk.Button.new_from_icon_name("document-save-as-symbolic", Gtk.IconSize.BUTTON)
        btn_save_as.set_tooltip_text("Save Document As (Ctrl+Shift+S)")
        btn_save_as.connect("clicked", lambda b: self.on_save_as())
        file_box.pack_start(btn_save_as, False, False, 0)

        hb.pack_start(file_box)

        # Document View Mode Switcher (Rendered View vs Source/Text View + TOC Sidebar)
        mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        Gtk.StyleContext.add_class(mode_box.get_style_context(), "linked")
        mode_box.set_margin_start(8)

        self.btn_toggle_toc = Gtk.ToggleButton(label="Outline")
        self.btn_toggle_toc.set_active(False)
        self.btn_toggle_toc.set_tooltip_text("Toggle Document Outline / Table of Contents Sidebar (F9)")
        self.btn_toggle_toc.connect("toggled", self._on_toggle_toc)
        mode_box.pack_start(self.btn_toggle_toc, False, False, 0)

        self.btn_mode_rendered = Gtk.ToggleButton(label="Reader View")
        self.btn_mode_rendered.set_tooltip_text("Switch between Rendered Reader View and Source View (F6 / Ctrl+P)")
        self.btn_mode_rendered.connect("toggled", self._on_mode_toggled)
        mode_box.pack_start(self.btn_mode_rendered, False, False, 0)

        self.btn_read_only = Gtk.ToggleButton(label="Read-Only")
        self.btn_read_only.set_tooltip_text("Lock/Unlock Document Editing (Ctrl+R)")
        self.btn_read_only.connect("toggled", self._on_read_only_toggled)
        mode_box.pack_start(self.btn_read_only, False, False, 0)

        hb.pack_start(mode_box)

        # Right Controls: Menu, Search, Line Numbers, Retina Zoom
        btn_menu = Gtk.MenuButton()
        btn_menu.set_image(Gtk.Image.new_from_icon_name("open-menu-symbolic", Gtk.IconSize.BUTTON))
        btn_menu.set_tooltip_text("DocuPanel Options & Companion Tools")
        btn_menu.set_popover(self._build_menu_popover(btn_menu))
        hb.pack_end(btn_menu)

        # Retina Zoom Controls
        zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        Gtk.StyleContext.add_class(zoom_box.get_style_context(), "linked")

        self.btn_zoom_out = Gtk.Button.new_from_icon_name("zoom-out-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_out.set_tooltip_text("Retina Zoom Out (Ctrl+-)")
        self.btn_zoom_out.connect("clicked", lambda b: self.zoom_active(out=True))
        zoom_box.pack_start(self.btn_zoom_out, False, False, 0)

        self.btn_zoom_reset = Gtk.Button(label="100%")
        self.btn_zoom_reset.set_tooltip_text("Reset Retina Scaling to 100% (Ctrl+0)")
        self.btn_zoom_reset.connect("clicked", lambda b: self.zoom_active(reset=True))
        zoom_box.pack_start(self.btn_zoom_reset, False, False, 0)

        self.btn_zoom_in = Gtk.Button.new_from_icon_name("zoom-in-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_in.set_tooltip_text("Retina Zoom In (Ctrl++)")
        self.btn_zoom_in.connect("clicked", lambda b: self.zoom_active(out=False))
        zoom_box.pack_start(self.btn_zoom_in, False, False, 0)

        self.btn_zoom_fit = Gtk.Button.new_from_icon_name("zoom-fit-best-symbolic", Gtk.IconSize.BUTTON)
        self.btn_zoom_fit.set_tooltip_text("Full Page View / Fit Page (F5 / Ctrl+9)")
        self.btn_zoom_fit.connect("clicked", lambda b: self.zoom_fit_page())
        zoom_box.pack_start(self.btn_zoom_fit, False, False, 0)

        hb.pack_end(zoom_box)

        # Line Numbers Toggle
        self.btn_line_nums = Gtk.ToggleButton(label="#")
        self.btn_line_nums.set_active(True)
        self.btn_line_nums.set_tooltip_text("Toggle Line Numbers in Source View")
        self.btn_line_nums.connect("toggled", self._on_toggle_line_numbers)
        hb.pack_end(self.btn_line_nums)

        # Search Button
        btn_find = Gtk.Button.new_from_icon_name("edit-find-symbolic", Gtk.IconSize.BUTTON)
        btn_find.set_tooltip_text("Find / Replace in Document (Ctrl+F / Ctrl+H)")
        btn_find.connect("clicked", lambda b: self.on_show_find())
        hb.pack_end(btn_find)

        # Dark Mode Toggle
        self.btn_dark_mode = Gtk.ToggleButton()
        dark_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self._dark_mode_icon = Gtk.Image.new_from_icon_name("weather-clear-night-symbolic", Gtk.IconSize.BUTTON)
        self._dark_mode_lbl = Gtk.Label(label="Dark")
        dark_box.pack_start(self._dark_mode_icon, False, False, 0)
        dark_box.pack_start(self._dark_mode_lbl, False, False, 0)
        self.btn_dark_mode.add(dark_box)
        self.btn_dark_mode.set_active(getattr(self, "dark_mode", True))
        self.btn_dark_mode.set_tooltip_text("Toggle Document Dark Mode (Invert/Darken Documents, Ctrl+Shift+D)")
        self.btn_dark_mode.connect("toggled", self._on_dark_mode_toggled)
        hb.pack_end(self.btn_dark_mode)

    def _build_menu_popover(self, parent_btn):
        popover = Gtk.Popover.new(parent_btn)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        vbox.set_margin_top(10)
        vbox.set_margin_bottom(10)
        vbox.set_margin_start(12)
        vbox.set_margin_end(12)

        lbl_doc = Gtk.Label()
        lbl_doc.set_markup("<b>Document Navigation &amp; Export</b>")
        lbl_doc.set_xalign(0)
        vbox.pack_start(lbl_doc, False, False, 2)

        items = [
            ("Toggle Reader / Source View (F6)", lambda: self._trigger_toggle_mode()),
            ("Toggle Outline Sidebar (F9)", lambda: self.btn_toggle_toc.set_active(not self.btn_toggle_toc.get_active())),
            ("Export Document to HTML...", lambda: self.on_export_html()),
            ("Open in PythonPanel...", lambda: self.on_open_in_pythonpanel()),
            ("Go to Line... (Ctrl+G)", lambda: self.on_goto_line()),
        ]
        for label_text, cb in items:
            btn = Gtk.ModelButton(label=label_text)
            btn.connect("clicked", lambda b, fn=cb: (popover.popdown(), fn()))
            vbox.pack_start(btn, False, False, 0)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        lbl_view = Gtk.Label()
        lbl_view.set_markup("<b>Retina Display &amp; Typography</b>")
        lbl_view.set_xalign(0)
        vbox.pack_start(lbl_view, False, False, 2)

        self.chk_wrap = Gtk.CheckButton(label="Word Wrap")
        self.chk_wrap.set_active(True)
        self.chk_wrap.connect("toggled", self._on_toggle_wrap)
        vbox.pack_start(self.chk_wrap, False, False, 0)

        self.chk_dark_mode = Gtk.CheckButton(label="Dark Mode (Ctrl+Shift+D)")
        self.chk_dark_mode.set_active(getattr(self, "dark_mode", True))
        self.chk_dark_mode.connect("toggled", self._on_dark_mode_toggled)
        vbox.pack_start(self.chk_dark_mode, False, False, 0)

        self.chk_pdf_dark = Gtk.CheckButton(label="PDF Dark Reading Mode (Ctrl+I)")
        self.chk_pdf_dark.set_active(getattr(self, "pdf_dark_mode", True))
        self.chk_pdf_dark.connect("toggled", self._on_menu_pdf_dark_toggled)
        vbox.pack_start(self.chk_pdf_dark, False, False, 0)

        btn_font = Gtk.Button(label="Select Font...")
        btn_font.connect("clicked", lambda b: (popover.popdown(), self.on_select_font()))
        vbox.pack_start(btn_font, False, False, 2)

        vbox.pack_start(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), False, False, 4)

        btn_about = Gtk.ModelButton(label="About DocuPanel")
        btn_about.connect("clicked", lambda b: (popover.popdown(), self.on_about()))
        vbox.pack_start(btn_about, False, False, 0)

        vbox.show_all()
        popover.add(vbox)
        return popover

    # --------------------------------------------------------------------------
    # Main Area: Outline Sidebar + Multi-Tab Document Viewer
    # --------------------------------------------------------------------------
    def _build_main_area(self):
        outer_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add(outer_vbox)

        self.search_bar = SearchBar(None)
        outer_vbox.pack_start(self.search_bar, False, False, 0)

        self.hpaned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.hpaned.set_position(250)
        outer_vbox.pack_start(self.hpaned, True, True, 0)

        # Left Pane: Document Outline / Bookmarks
        self.toc_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toc_box.set_no_show_all(True)
        self.toc_box.set_visible(False)
        toc_header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        toc_header.set_margin_start(10)
        toc_header.set_margin_end(8)
        toc_header.set_margin_top(6)
        toc_header.set_margin_bottom(6)

        lbl_toc = Gtk.Label()
        lbl_toc.set_markup("<b>DOCUMENT OUTLINE / BOOKMARKS</b>")
        lbl_toc.set_xalign(0)
        toc_header.pack_start(lbl_toc, False, False, 0)

        self.lbl_toc_source = Gtk.Label(label="")
        self.lbl_toc_source.set_xalign(0)
        Gtk.StyleContext.add_class(self.lbl_toc_source.get_style_context(), "dim-label")
        toc_header.pack_start(self.lbl_toc_source, False, False, 0)

        toc_header.show_all()
        self.toc_box.pack_start(toc_header, False, False, 0)

        toc_scroll = Gtk.ScrolledWindow()
        toc_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        # Columns: (display_title, line_number, page_number, page_badge)
        self.toc_store = Gtk.TreeStore(str, int, int, str)
        self.toc_tree = Gtk.TreeView(model=self.toc_store)
        self.toc_tree.set_headers_visible(False)
        self.toc_tree.set_activate_on_single_click(True)

        renderer = Gtk.CellRendererText()
        renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        col = Gtk.TreeViewColumn("Section", renderer, text=0)
        col.set_expand(True)
        self.toc_tree.append_column(col)

        renderer_pg = Gtk.CellRendererText()
        renderer_pg.set_property("xalign", 1.0)
        renderer_pg.set_property("foreground", "#00d4ff" if getattr(self, "dark_mode", True) else "#0284c7")
        renderer_pg.set_property("scale", 0.85)
        self._toc_page_renderer = renderer_pg
        col_pg = Gtk.TreeViewColumn("Page", renderer_pg, text=3)
        col_pg.set_expand(False)
        self.toc_tree.append_column(col_pg)

        self.toc_tree.connect("row-activated", self._on_toc_row_activated)

        toc_scroll.add(self.toc_tree)
        toc_scroll.show_all()
        self.toc_box.pack_start(toc_scroll, True, True, 0)
        self.hpaned.pack1(self.toc_box, resize=False, shrink=False)

        # Right Pane: Multi-Tab Document Notebook
        self.notebook_tabs = Gtk.Notebook()
        self.notebook_tabs.set_scrollable(True)
        self.notebook_tabs.connect("switch-page", self._on_tab_switched)
        self.hpaned.pack2(self.notebook_tabs, resize=True, shrink=True)

        self.statusbar_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        outer_vbox.pack_end(self.statusbar_box, False, False, 0)

    def _build_status_bar(self):
        sb = self.statusbar_box
        sb.set_margin_start(12)
        sb.set_margin_end(12)
        sb.set_margin_top(4)
        sb.set_margin_bottom(4)

        self.lbl_cursor = Gtk.Label(label="Ln 1, Col 1")
        sb.pack_start(self.lbl_cursor, False, False, 0)

        sb.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 2)

        self.lbl_stats = Gtk.Label(label="1 lines, 0 words (~0 min read)")
        sb.pack_start(self.lbl_stats, False, False, 0)

        self.lbl_zoom = Gtk.Label(label="Retina: 100%")
        sb.pack_end(self.lbl_zoom, False, False, 0)

        sb.pack_end(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 2)

        self.lbl_format = Gtk.Label(label="MARKDOWN | UTF-8")
        sb.pack_end(self.lbl_format, False, False, 0)

    # --------------------------------------------------------------------------
    # Multi-Tab Management
    # --------------------------------------------------------------------------
    def new_tab(self, file_path=None):
        if file_path:
            abs_target = os.path.abspath(os.path.expanduser(file_path))
            for i in range(self.notebook_tabs.get_n_pages()):
                existing = self.notebook_tabs.get_nth_page(i)
                if existing.file_path and os.path.abspath(existing.file_path) == abs_target:
                    self.notebook_tabs.set_current_page(i)
                    return existing

        viewer = DocViewer(
            file_path=file_path,
            dark_mode=getattr(self, "dark_mode", True),
            pdf_dark_mode=getattr(self, "pdf_dark_mode", True),
        )
        viewer.connect("cursor-moved", self._on_cursor_moved)
        viewer.connect("content-modified", lambda v, mod: self._on_content_modified(v))
        viewer.connect("zoom-changed", self._on_zoom_changed)
        viewer.connect("file-saved", lambda v, p: self._update_tab_title(v))
        viewer.connect("outline-updated", lambda v: self.refresh_outline())
        viewer.connect("open-file-requested", lambda v, path: self.new_tab(path))
        viewer.connect("pdf-page-changed", self._on_pdf_page_changed)
        viewer.connect("pdf-dark-mode-changed", self._on_viewer_pdf_dark_changed)

        tab_box = self._create_tab_label(viewer)
        idx = self.notebook_tabs.append_page(viewer, tab_box)
        self.notebook_tabs.set_tab_reorderable(viewer, True)
        self.notebook_tabs.show_all()
        self.notebook_tabs.set_current_page(idx)

        self._sync_ui_to_viewer(viewer)
        return viewer

    def _create_tab_label(self, viewer):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = os.path.basename(viewer.file_path) if viewer.file_path else "Untitled.md"
        lbl = Gtk.Label(label=title)
        box.pack_start(lbl, False, False, 0)

        btn_close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        btn_close.set_relief(Gtk.ReliefStyle.NONE)
        btn_close.set_tooltip_text("Close Document Tab (Ctrl+W)")
        btn_close.connect("clicked", lambda b: self.close_tab(viewer))
        box.pack_start(btn_close, False, False, 0)

        box.show_all()
        viewer._tab_label_widget = lbl
        return box

    def _update_tab_title(self, viewer):
        if not hasattr(viewer, "_tab_label_widget"):
            return
        name = os.path.basename(viewer.file_path) if viewer.file_path else "Untitled.md"
        if viewer.is_modified():
            name = f"*{name}"
        viewer._tab_label_widget.set_text(name)

        if viewer == self.get_current_viewer():
            sub = viewer.file_path if viewer.file_path else "Untitled Document"
            if viewer.is_modified():
                sub += " (Modified)"
            self.header_bar.set_subtitle(sub)

    def get_current_viewer(self) -> DocViewer:
        idx = self.notebook_tabs.get_current_page()
        if idx >= 0:
            return self.notebook_tabs.get_nth_page(idx)
        return None

    def close_tab(self, viewer=None):
        target = viewer or self.get_current_viewer()
        if not target:
            return

        if target.is_modified():
            fname = os.path.basename(target.file_path) if target.file_path else "Untitled.md"
            dialog = Gtk.MessageDialog(
                transient_for=self,
                flags=0,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.NONE,
                text=f"Save changes to \"{fname}\" before closing?"
            )
            dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
            dialog.add_button("Discard", Gtk.ResponseType.NO)
            dialog.add_button("Save", Gtk.ResponseType.YES)
            resp = dialog.run()
            dialog.destroy()

            if resp == Gtk.ResponseType.YES:
                if not self.on_save(target):
                    return
            elif resp != Gtk.ResponseType.NO:
                return

        if target.pdf_renderer:
            target.pdf_renderer.cleanup()

        page_num = self.notebook_tabs.page_num(target)
        if page_num >= 0:
            self.notebook_tabs.remove_page(page_num)

        if self.notebook_tabs.get_n_pages() == 0:
            self.new_tab()

    def _on_tab_switched(self, notebook, page, page_num):
        if isinstance(page, DocViewer):
            self._sync_ui_to_viewer(page)

    def _sync_ui_to_viewer(self, viewer: DocViewer):
        if not viewer:
            return
        self.search_bar.set_viewer(viewer)
        self._update_tab_title(viewer)

        self._updating_mode_toggle = True
        try:
            self.btn_mode_rendered.set_active(viewer.get_view_mode() in ("rendered", "pdf"))
            self.btn_read_only.set_active(viewer.read_only)
            self.btn_line_nums.set_active(viewer.get_line_numbers())
            if hasattr(self, "chk_pdf_dark"):
                self.chk_pdf_dark.set_active(bool(getattr(viewer, "pdf_dark_mode", False)))
        finally:
            self._updating_mode_toggle = False

        line, col = viewer.get_cursor_position()
        self.lbl_cursor.set_text(f"Ln {line}, Col {col}")
        self._refresh_stats_now(viewer)
        self.refresh_outline()

    # --------------------------------------------------------------------------
    # Outline / Table of Contents
    # --------------------------------------------------------------------------
    def refresh_outline(self):
        viewer = self.get_current_viewer()
        self.toc_store.clear()
        if not viewer:
            if hasattr(self, "lbl_toc_source"):
                self.lbl_toc_source.set_text("")
            return

        headings, source = viewer.get_outline_items()

        if hasattr(self, "lbl_toc_source"):
            source_labels = {
                "bookmarks": f"PDF Bookmarks ({len(headings)})",
                "auto-chapters": f"Auto-Populated Chapters ({len(headings)})",
                "pages": f"Page Index ({len(headings)})",
                "headings": f"Section Headings ({len(headings)})",
            }
            self.lbl_toc_source.set_text(source_labels.get(source, ""))

        # Insert hierarchically into TreeStore based on heading level
        # Stack holds tuples of (level, Gtk.TreeIter)
        parent_stack = []
        for h in headings:
            lvl = max(1, int(h.level))
            while parent_stack and parent_stack[-1][0] >= lvl:
                parent_stack.pop()
            parent_iter = parent_stack[-1][1] if parent_stack else None
            page_badge = f"p.{h.page}" if h.page > 0 else ""
            row_iter = self.toc_store.append(parent_iter, [h.title, h.line, h.page, page_badge])
            parent_stack.append((lvl, row_iter))

        # Expand automatically when concise; keep top chapters tidy for large multi-chapter books
        if len(headings) <= 45:
            self.toc_tree.expand_all()
        else:
            # Expand level 1 only if not overcrowded
            top_count = sum(1 for h in headings if h.level == 1)
            if top_count <= 8 and len(headings) <= 100:
                self.toc_tree.expand_all()

        if viewer.doc_type == "pdf" and viewer.current_pdf_page > 0:
            self._sync_toc_selection_to_page(viewer.current_pdf_page)

    def _on_pdf_page_changed(self, viewer, page_num: int):
        if viewer == self.get_current_viewer():
            self._sync_toc_selection_to_page(page_num)

    def _sync_toc_selection_to_page(self, page_num: int):
        """Highlight the outline/bookmark entry matching or immediately preceding page_num."""
        if page_num <= 0 or not hasattr(self, "toc_store"):
            return

        best_path = None
        best_page = -1

        def _walk(it):
            nonlocal best_path, best_page
            while it:
                pg = self.toc_store.get_value(it, 2)
                path = self.toc_store.get_path(it)
                if 0 < pg <= page_num and pg >= best_page:
                    best_page = pg
                    best_path = path
                if self.toc_store.iter_has_child(it) and self.toc_tree.row_expanded(path):
                    _walk(self.toc_store.iter_children(it))
                it = self.toc_store.iter_next(it)

        root_it = self.toc_store.get_iter_first()
        if root_it:
            _walk(root_it)

        if best_path is not None:
            sel = self.toc_tree.get_selection()
            sel.select_path(best_path)

    def _on_toc_row_activated(self, tree, path, column):
        viewer = self.get_current_viewer()
        if not viewer:
            return
        model = tree.get_model()
        it = model.get_iter(path)
        if not it:
            return
        line = model.get_value(it, 1)
        page = model.get_value(it, 2)
        if page > 0 and viewer.doc_type == "pdf":
            viewer.set_view_mode("rendered")
            viewer.goto_pdf_page(page)
        else:
            viewer.set_view_mode("source")
            self._updating_mode_toggle = True
            self.btn_mode_rendered.set_active(False)
            self._updating_mode_toggle = False
            viewer.goto_line(line)

    # --------------------------------------------------------------------------
    # Event Handlers & Actions
    # --------------------------------------------------------------------------
    def _on_cursor_moved(self, viewer, line, col):
        if viewer == self.get_current_viewer():
            self.lbl_cursor.set_text(f"Ln {line}, Col {col}")

    def _on_content_modified(self, viewer):
        self._update_tab_title(viewer)
        if self._stats_timeout_id is None:
            self._stats_timeout_id = GLib.timeout_add(160, self._debounced_stats_and_outline)

    def _debounced_stats_and_outline(self):
        self._stats_timeout_id = None
        viewer = self.get_current_viewer()
        if viewer:
            self._refresh_stats_now(viewer)
            self.refresh_outline()
            if viewer.get_view_mode() == "rendered":
                viewer.refresh_rendered_preview()
        return False

    def _update_zoom_ui(self, viewer: Optional[DocViewer] = None):
        if viewer is None:
            viewer = self.get_current_viewer()
        if not viewer:
            return
        pct = viewer.get_zoom_percent()
        self.lbl_zoom.set_text(f"Retina: {pct}%")
        self.btn_zoom_reset.set_label(f"{pct}%")
        self.btn_zoom_reset.set_tooltip_text(f"Reset Retina Scaling to 100% (currently {pct}%) (Ctrl+0)")
        if hasattr(self, "btn_zoom_out"):
            self.btn_zoom_out.set_sensitive(viewer.can_zoom_out())
            self.btn_zoom_out.set_tooltip_text(f"Retina Zoom Out (Ctrl+-) [Current: {pct}%]")
        if hasattr(self, "btn_zoom_in"):
            self.btn_zoom_in.set_sensitive(viewer.can_zoom_in())
            self.btn_zoom_in.set_tooltip_text(f"Retina Zoom In (Ctrl++) [Current: {pct}%]")

    def _refresh_stats_now(self, viewer: DocViewer):
        lines, words, chars, read_min = viewer.get_document_statistics()
        if viewer.doc_type == "pdf" and viewer.total_pdf_pages > 0:
            self.lbl_stats.set_text(f"PDF: {viewer.total_pdf_pages} page(s) | {words} words")
        else:
            self.lbl_stats.set_text(f"{lines} lines, {words} words, {chars} chars (~{read_min} min read)")
        self.lbl_format.set_text(f"{viewer.doc_type.upper()} | {viewer.file_encoding}")
        self._update_zoom_ui(viewer)

    def _on_zoom_changed(self, viewer, pct):
        if viewer == self.get_current_viewer():
            self._update_zoom_ui(viewer)

    def _on_toggle_toc(self, btn):
        self.toc_box.set_visible(btn.get_active())

    def _on_mode_toggled(self, btn):
        if getattr(self, "_updating_mode_toggle", False):
            return
        viewer = self.get_current_viewer()
        if not viewer:
            return
        viewer.set_view_mode("rendered" if btn.get_active() else "source")

    def _trigger_toggle_mode(self):
        self.btn_mode_rendered.set_active(not self.btn_mode_rendered.get_active())

    def _on_read_only_toggled(self, btn):
        if getattr(self, "_updating_mode_toggle", False):
            return
        viewer = self.get_current_viewer()
        if viewer:
            viewer.set_read_only(btn.get_active())

    def _on_toggle_line_numbers(self, btn):
        viewer = self.get_current_viewer()
        if viewer:
            viewer.set_line_numbers(btn.get_active())

    def _on_toggle_wrap(self, btn):
        viewer = self.get_current_viewer()
        if viewer:
            viewer.set_word_wrap(btn.get_active())

    def _on_dark_mode_toggled(self, btn):
        if getattr(self, "_updating_theme_toggle", False):
            return
        self.set_dark_mode(btn.get_active())

    def toggle_dark_mode(self):
        self.set_dark_mode(not getattr(self, "dark_mode", True))

    def set_dark_mode(self, enabled: bool):
        """Toggle Dark Mode for documents (PDF/HTML/Markdown/Source) while keeping the dark neon UI shell."""
        enabled = bool(enabled)
        self.dark_mode = enabled
        self.pdf_dark_mode = enabled

        self._updating_theme_toggle = True
        try:
            if hasattr(self, "btn_dark_mode"):
                self.btn_dark_mode.set_active(enabled)
            if hasattr(self, "chk_dark_mode"):
                self.chk_dark_mode.set_active(enabled)
            if hasattr(self, "chk_pdf_dark"):
                self.chk_pdf_dark.set_active(enabled)
            if hasattr(self, "_dark_mode_icon"):
                self._dark_mode_icon.set_from_icon_name(
                    "weather-clear-night-symbolic" if enabled else "weather-clear-symbolic",
                    Gtk.IconSize.BUTTON,
                )
        finally:
            self._updating_theme_toggle = False

        for i in range(self.notebook_tabs.get_n_pages()):
            v = self.notebook_tabs.get_nth_page(i)
            if isinstance(v, DocViewer):
                v.set_dark_mode(enabled)
                v.set_pdf_dark_mode(enabled)

    def _on_menu_pdf_dark_toggled(self, btn):
        if getattr(self, "_updating_theme_toggle", False) or getattr(self, "_updating_mode_toggle", False):
            return
        self.set_pdf_dark_mode(btn.get_active())

    def toggle_pdf_dark_mode(self):
        viewer = self.get_current_viewer()
        target = not bool(getattr(viewer, "pdf_dark_mode", self.pdf_dark_mode))
        self.set_pdf_dark_mode(target)

    def set_pdf_dark_mode(self, enabled: bool):
        enabled = bool(enabled)
        self.pdf_dark_mode = enabled
        viewer = self.get_current_viewer()
        if viewer:
            viewer.set_pdf_dark_mode(enabled)
        self._sync_pdf_dark_ui(enabled)

    def _on_viewer_pdf_dark_changed(self, viewer, enabled: bool):
        if viewer == self.get_current_viewer():
            self.pdf_dark_mode = bool(enabled)
            self._sync_pdf_dark_ui(enabled)

    def _sync_pdf_dark_ui(self, enabled: bool):
        if hasattr(self, "chk_pdf_dark") and self.chk_pdf_dark.get_active() != bool(enabled):
            self._updating_theme_toggle = True
            try:
                self.chk_pdf_dark.set_active(bool(enabled))
            finally:
                self._updating_theme_toggle = False

    def zoom_active(self, out=False, reset=False):
        viewer = self.get_current_viewer()
        if not viewer:
            return
        if reset:
            viewer.zoom_reset()
        elif out:
            viewer.zoom_out()
        else:
            viewer.zoom_in()
        self._update_zoom_ui(viewer)

    def zoom_fit_page(self):
        viewer = self.get_current_viewer()
        if not viewer:
            return
        if viewer.get_view_mode() == "pdf":
            viewer.fit_to_page()
        else:
            viewer.zoom_reset()
        self._update_zoom_ui(viewer)

    # --------------------------------------------------------------------------
    # File Dialogs & Companion Integration
    # --------------------------------------------------------------------------
    def on_open(self):
        dialog = Gtk.FileChooserDialog(
            title="Open Document in DocuPanel",
            parent=self,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )

        flt_docs = Gtk.FileFilter()
        flt_docs.set_name("All Supported Documents (.md, .pdf, .rst, .html, .odt, .docx, .csv, .json, .txt)")
        for pat in ("*.md", "*.markdown", "*.rst", "*.pdf", "*.html", "*.htm", "*.odt", "*.docx", "*.csv", "*.tsv", "*.json", "*.xml", "*.txt", "*.log"):
            flt_docs.add_pattern(pat)
        dialog.add_filter(flt_docs)

        flt_all = Gtk.FileFilter()
        flt_all.set_name("All Files")
        flt_all.add_pattern("*")
        dialog.add_filter(flt_all)

        if dialog.run() == Gtk.ResponseType.OK:
            path = dialog.get_filename()
            dialog.destroy()
            if path:
                cur = self.get_current_viewer()
                if cur and not cur.file_path and not cur.is_modified() and not cur.get_text().strip():
                    try:
                        cur.load_file(path)
                        self._sync_ui_to_viewer(cur)
                    except Exception as e:
                        self._show_error(f"Could not open document:\n{e}")
                else:
                    try:
                        self.new_tab(path)
                    except Exception as e:
                        self._show_error(f"Could not open document:\n{e}")
        else:
            dialog.destroy()

    def on_save(self, viewer=None) -> bool:
        target = viewer or self.get_current_viewer()
        if not target:
            return False
        if not target.file_path or target.doc_type in ("pdf", "odt", "docx"):
            return self.on_save_as(target)
        try:
            target.save_file()
            self._sync_ui_to_viewer(target)
            return True
        except Exception as e:
            self._show_error(f"Error saving document:\n{e}")
            return False

    def on_save_as(self, viewer=None) -> bool:
        target = viewer or self.get_current_viewer()
        if not target:
            return False

        dialog = Gtk.FileChooserDialog(
            title="Save Document As",
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog.set_do_overwrite_confirmation(True)
        if target.file_path:
            dialog.set_current_name(os.path.basename(target.file_path))
        else:
            dialog.set_current_name("document.md")

        res = dialog.run()
        path = dialog.get_filename()
        dialog.destroy()

        if res == Gtk.ResponseType.OK and path:
            try:
                target.save_file(path)
                self._sync_ui_to_viewer(target)
                return True
            except Exception as e:
                self._show_error(f"Error saving document:\n{e}")
        return False

    def on_export_html(self):
        viewer = self.get_current_viewer()
        if not viewer:
            return
        dialog = Gtk.FileChooserDialog(
            title="Export Rendered HTML",
            parent=self,
            action=Gtk.FileChooserAction.SAVE
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_SAVE, Gtk.ResponseType.OK
        )
        dialog.set_do_overwrite_confirmation(True)
        base_name = os.path.splitext(os.path.basename(viewer.file_path or "document"))[0] + ".html"
        dialog.set_current_name(base_name)

        res = dialog.run()
        out_path = dialog.get_filename()
        dialog.destroy()

        if res == Gtk.ResponseType.OK and out_path:
            try:
                html_doc = DocumentParser.render_markdown_to_html(
                    viewer.get_text(),
                    viewer.doc_type,
                    viewer.file_path,
                    dark_mode=getattr(self, "dark_mode", True),
                )
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(html_doc)
            except Exception as e:
                self._show_error(f"Could not export HTML:\n{e}")

    def on_open_in_pythonpanel(self):
        """Launch companion PythonPanel IDE with the current file."""
        viewer = self.get_current_viewer()
        cmd = ["pythonpanel"]
        if viewer and viewer.file_path:
            cmd.append(viewer.file_path)
        try:
            import subprocess
            env = os.environ.copy()
            env["PATH"] = os.path.expanduser("~/.local/bin") + ":" + env.get("PATH", "")
            subprocess.Popen(cmd, env=env, start_new_session=True)
        except Exception as e:
            self._show_error(f"Could not launch PythonPanel:\n{e}")

    def on_show_find(self):
        viewer = self.get_current_viewer()
        if viewer:
            self.search_bar.set_viewer(viewer)
            self.search_bar.show_find()

    def on_show_replace(self):
        viewer = self.get_current_viewer()
        if viewer:
            self.search_bar.set_viewer(viewer)
            self.search_bar.show_replace()

    def on_goto_line(self):
        viewer = self.get_current_viewer()
        if not viewer:
            return
        dialog = Gtk.Dialog(
            title="Go to Line",
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            buttons=("Cancel", Gtk.ResponseType.CANCEL, "Go", Gtk.ResponseType.OK)
        )
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)
        entry = Gtk.Entry()
        entry.set_placeholder_text("Line number...")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        dialog.show_all()
        if dialog.run() == Gtk.ResponseType.OK:
            try:
                line = int(entry.get_text().strip())
                viewer.set_view_mode("source")
                viewer.goto_line(line)
            except ValueError:
                pass
        dialog.destroy()

    def on_select_font(self):
        viewer = self.get_current_viewer()
        if not viewer:
            return
        dialog = Gtk.FontChooserDialog(title="Select Document Font", parent=self)
        dialog.set_font(viewer.get_font_string())
        if dialog.run() == Gtk.ResponseType.OK:
            font_str = dialog.get_font()
            if font_str:
                for i in range(self.notebook_tabs.get_n_pages()):
                    self.notebook_tabs.get_nth_page(i).set_font_from_string(font_str)
        dialog.destroy()

    def on_about(self):
        dialog = Gtk.AboutDialog(transient_for=self, modal=True)
        dialog.set_program_name(self.APP_NAME)
        dialog.set_version(self.APP_VERSION)
        dialog.set_comments(
            "Retina Document Viewer & Reader Companion for PythonPanel.\n"
            "Supports Markdown, PDF, reStructuredText, HTML, ODT, DOCX, CSV/TSV, JSON, and Text."
        )
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docupanel.svg")
        if os.path.exists(icon_path):
            try:
                dialog.set_logo(GdkPixbuf.Pixbuf.new_from_file_at_scale(icon_path, 96, 96, True))
            except Exception:
                pass
        dialog.run()
        dialog.destroy()

    def _show_error(self, msg):
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text="DocuPanel Error"
        )
        dialog.format_secondary_text(str(msg))
        dialog.run()
        dialog.destroy()

    def _setup_shortcuts(self):
        accel = Gtk.AccelGroup()
        self.add_accel_group(accel)

        shortcuts = [
            ("n", Gdk.ModifierType.CONTROL_MASK, lambda: self.new_tab()),
            ("t", Gdk.ModifierType.CONTROL_MASK, lambda: self.new_tab()),
            ("o", Gdk.ModifierType.CONTROL_MASK, lambda: self.on_open()),
            ("s", Gdk.ModifierType.CONTROL_MASK, lambda: self.on_save()),
            ("s", Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, lambda: self.on_save_as()),
            ("w", Gdk.ModifierType.CONTROL_MASK, lambda: self.close_tab()),
            ("q", Gdk.ModifierType.CONTROL_MASK, lambda: self.close()),
            ("f", Gdk.ModifierType.CONTROL_MASK, lambda: self.on_show_find()),
            ("h", Gdk.ModifierType.CONTROL_MASK, lambda: self.on_show_replace()),
            ("g", Gdk.ModifierType.CONTROL_MASK, lambda: self.on_goto_line()),
            ("p", Gdk.ModifierType.CONTROL_MASK, lambda: self._trigger_toggle_mode()),
            ("r", Gdk.ModifierType.CONTROL_MASK, lambda: self.btn_read_only.set_active(not self.btn_read_only.get_active())),
            ("d", Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK, lambda: self.toggle_dark_mode()),
            ("i", Gdk.ModifierType.CONTROL_MASK, lambda: self.toggle_pdf_dark_mode()),
            ("F6", 0, lambda: self._trigger_toggle_mode()),
            ("F9", 0, lambda: self.btn_toggle_toc.set_active(not self.btn_toggle_toc.get_active())),
            ("F5", 0, lambda: self.zoom_fit_page()),
            ("plus", Gdk.ModifierType.CONTROL_MASK, lambda: self.zoom_active(out=False)),
            ("equal", Gdk.ModifierType.CONTROL_MASK, lambda: self.zoom_active(out=False)),
            ("minus", Gdk.ModifierType.CONTROL_MASK, lambda: self.zoom_active(out=True)),
            ("0", Gdk.ModifierType.CONTROL_MASK, lambda: self.zoom_active(reset=True)),
            ("9", Gdk.ModifierType.CONTROL_MASK, lambda: self.zoom_fit_page()),
        ]
        for key_str, mod, callback in shortcuts:
            kv = Gdk.keyval_from_name(key_str)
            if kv != 0:
                accel.connect(kv, mod, Gtk.AccelFlags.VISIBLE, lambda ag, w, k, m, cb=callback: cb())

    def _on_delete_event(self, widget, event):
        for i in range(self.notebook_tabs.get_n_pages()):
            v = self.notebook_tabs.get_nth_page(i)
            if v.is_modified():
                self.notebook_tabs.set_current_page(i)
                fname = os.path.basename(v.file_path) if v.file_path else "Untitled.md"
                dialog = Gtk.MessageDialog(
                    transient_for=self,
                    flags=0,
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.NONE,
                    text=f"Save changes to \"{fname}\" before quitting?"
                )
                dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
                dialog.add_button("Discard", Gtk.ResponseType.NO)
                dialog.add_button("Save", Gtk.ResponseType.YES)
                resp = dialog.run()
                dialog.destroy()

                if resp == Gtk.ResponseType.YES:
                    if not self.on_save(v):
                        return True
                elif resp != Gtk.ResponseType.NO:
                    return True

        for i in range(self.notebook_tabs.get_n_pages()):
            v = self.notebook_tabs.get_nth_page(i)
            if v.pdf_renderer:
                v.pdf_renderer.cleanup()
        return False


class DocuPanelApp(Gtk.Application):
    def __init__(self):
        super().__init__(
            application_id="org.linuxmint.docupanel",
            flags=Gio.ApplicationFlags.HANDLES_OPEN | Gio.ApplicationFlags.NON_UNIQUE
        )
        self.files_to_open = []

    def do_activate(self):
        win = DocuPanelWindow(self, initial_files=self.files_to_open)
        win.present()

    def do_open(self, files, n_files, hint):
        paths = [f.get_path() for f in files if f.get_path()]
        win = DocuPanelWindow(self, initial_files=paths)
        win.present()


def main():
    GLib.set_prgname("docupanel")
    GLib.set_application_name("DocuPanel")
    app = DocuPanelApp()
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        app.files_to_open = [os.path.abspath(f) for f in sys.argv[1:]]
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
