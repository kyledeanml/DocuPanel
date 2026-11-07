#!/usr/bin/env python3
"""
DocPad - Integrated Search and Replace Bar
Provides fast text search & replace with case-sensitivity, regex, and whole-word options.
"""

import gi

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')
gi.require_version('GtkSource', '4')

from gi.repository import Gtk, Gdk, GtkSource


class SearchBar(Gtk.Revealer):
    """Collapsible search and replace toolbar for DocViewer."""

    def __init__(self, viewer=None):
        super().__init__()
        self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.set_transition_duration(150)

        self.viewer = None
        self.search_settings = GtkSource.SearchSettings()
        self.search_settings.set_wrap_around(True)
        self.search_context = None
        self._occ_signal_id = None

        self._build_ui()
        if viewer:
            self.set_viewer(viewer)

    def _build_ui(self):
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        main_box.set_margin_start(10)
        main_box.set_margin_end(10)
        main_box.set_margin_top(6)
        main_box.set_margin_bottom(6)

        find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        lbl_find = Gtk.Label(label="Find:")
        lbl_find.set_width_chars(7)
        lbl_find.set_xalign(1.0)
        find_row.pack_start(lbl_find, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search in document...")
        self.search_entry.set_width_chars(28)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_entry.connect("activate", lambda w: self.find_next(advance=True))
        self.search_entry.connect("key-press-event", self._on_key_press)
        find_row.pack_start(self.search_entry, False, False, 0)

        btn_prev = Gtk.Button.new_from_icon_name("go-up-symbolic", Gtk.IconSize.BUTTON)
        btn_prev.set_tooltip_text("Find Previous (Shift+Enter)")
        btn_prev.connect("clicked", lambda b: self.find_previous())
        find_row.pack_start(btn_prev, False, False, 0)

        btn_next = Gtk.Button.new_from_icon_name("go-down-symbolic", Gtk.IconSize.BUTTON)
        btn_next.set_tooltip_text("Find Next (Enter)")
        btn_next.connect("clicked", lambda b: self.find_next(advance=True))
        find_row.pack_start(btn_next, False, False, 0)

        self.chk_case = Gtk.ToggleButton(label="Aa")
        self.chk_case.set_tooltip_text("Match Case")
        self.chk_case.connect("toggled", self._on_options_toggled)
        find_row.pack_start(self.chk_case, False, False, 0)

        self.chk_word = Gtk.ToggleButton(label="\\b")
        self.chk_word.set_tooltip_text("Whole Words Only")
        self.chk_word.connect("toggled", self._on_options_toggled)
        find_row.pack_start(self.chk_word, False, False, 0)

        self.chk_regex = Gtk.ToggleButton(label=".*")
        self.chk_regex.set_tooltip_text("Regular Expression")
        self.chk_regex.connect("toggled", self._on_options_toggled)
        find_row.pack_start(self.chk_regex, False, False, 0)

        self.lbl_status = Gtk.Label(label="")
        self.lbl_status.set_margin_start(8)
        find_row.pack_start(self.lbl_status, False, False, 0)

        btn_close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.BUTTON)
        btn_close.set_relief(Gtk.ReliefStyle.NONE)
        btn_close.set_tooltip_text("Close Search Bar (Esc)")
        btn_close.connect("clicked", lambda b: self.hide_bar())
        find_row.pack_end(btn_close, False, False, 0)

        main_box.pack_start(find_row, False, False, 0)

        self.replace_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        lbl_replace = Gtk.Label(label="Replace:")
        lbl_replace.set_width_chars(7)
        lbl_replace.set_xalign(1.0)
        self.replace_row.pack_start(lbl_replace, False, False, 0)

        self.replace_entry = Gtk.Entry()
        self.replace_entry.set_placeholder_text("Replace with...")
        self.replace_entry.set_width_chars(28)
        self.replace_entry.connect("activate", lambda w: self.replace_current())
        self.replace_entry.connect("key-press-event", self._on_key_press)
        self.replace_row.pack_start(self.replace_entry, False, False, 0)

        btn_replace = Gtk.Button(label="Replace")
        btn_replace.connect("clicked", lambda b: self.replace_current())
        self.replace_row.pack_start(btn_replace, False, False, 0)

        btn_replace_all = Gtk.Button(label="Replace All")
        btn_replace_all.connect("clicked", lambda b: self.replace_all())
        self.replace_row.pack_start(btn_replace_all, False, False, 0)

        main_box.pack_start(self.replace_row, False, False, 0)
        self.add(main_box)

    def _disconnect_context(self):
        if self.search_context is not None:
            if self._occ_signal_id is not None:
                try:
                    self.search_context.disconnect(self._occ_signal_id)
                except Exception:
                    pass
                self._occ_signal_id = None
            try:
                self.search_context.set_highlight(False)
            except Exception:
                pass
            self.search_context = None

    def set_viewer(self, viewer):
        self._disconnect_context()
        self.viewer = viewer
        if self.viewer:
            self.search_context = GtkSource.SearchContext.new(
                self.viewer.source_buffer,
                self.search_settings
            )
            self.search_context.set_highlight(self.get_reveal_child())
            self._occ_signal_id = self.search_context.connect(
                "notify::occurrences-count",
                lambda ctx, pspec: self._update_match_count()
            )
            if self.get_reveal_child():
                self._update_match_count()

    def show_find(self):
        self.set_reveal_child(True)
        self.replace_row.set_visible(False)
        if self.search_context:
            self.search_context.set_highlight(True)
        if self.viewer:
            buf = self.viewer.source_buffer
            bounds = buf.get_selection_bounds()
            if bounds:
                selected_text = buf.get_text(bounds[0], bounds[1], True)
                if "\n" not in selected_text and len(selected_text) < 200:
                    self.search_entry.set_text(selected_text)
        self.search_entry.grab_focus()
        self.search_entry.select_region(0, -1)

    def show_replace(self):
        self.show_find()
        self.replace_row.set_visible(True)

    def hide_bar(self):
        self.set_reveal_child(False)
        if self.search_context:
            self.search_context.set_highlight(False)
        if self.viewer:
            self.viewer.source_view.grab_focus()

    def _on_key_press(self, widget, event):
        if event.keyval == Gdk.KEY_Escape:
            self.hide_bar()
            return True
        if event.keyval == Gdk.KEY_Return and (event.state & Gdk.ModifierType.SHIFT_MASK):
            self.find_previous()
            return True
        return False

    def _on_options_toggled(self, button):
        self.search_settings.set_case_sensitive(self.chk_case.get_active())
        self.search_settings.set_at_word_boundaries(self.chk_word.get_active())
        self.search_settings.set_regex_enabled(self.chk_regex.get_active())
        self._on_search_changed(self.search_entry)

    def _on_search_changed(self, entry):
        query = entry.get_text()
        self.search_settings.set_search_text(query if query else None)
        if not query:
            self.lbl_status.set_text("")
            return
        self.find_next(advance=False)

    def find_next(self, advance=True):
        if not self.search_context or not self.viewer:
            return
        query = self.search_settings.get_search_text()
        if not query:
            self.lbl_status.set_text("")
            return

        if self.viewer.get_view_mode() != "source":
            self.viewer.set_view_mode("source")

        buf = self.viewer.source_buffer
        bounds = buf.get_selection_bounds()
        if bounds:
            start_iter = bounds[1] if advance else bounds[0]
        else:
            start_iter = buf.get_iter_at_mark(buf.get_insert())

        found, match_start, match_end, _ = self.search_context.forward(start_iter)
        if found:
            buf.select_range(match_start, match_end)
            self.viewer.source_view.scroll_to_iter(match_start, 0.2, False, 0.0, 0.0)
            self._update_match_count()
        else:
            self.lbl_status.set_text("No matches")

    def find_previous(self):
        if not self.search_context or not self.viewer:
            return
        query = self.search_settings.get_search_text()
        if not query:
            return

        if self.viewer.get_view_mode() != "source":
            self.viewer.set_view_mode("source")

        buf = self.viewer.source_buffer
        bounds = buf.get_selection_bounds()
        start_iter = bounds[0] if bounds else buf.get_iter_at_mark(buf.get_insert())

        found, match_start, match_end, _ = self.search_context.backward(start_iter)
        if found:
            buf.select_range(match_start, match_end)
            self.viewer.source_view.scroll_to_iter(match_start, 0.2, False, 0.0, 0.0)
            self._update_match_count()
        else:
            self.lbl_status.set_text("No matches")

    def replace_current(self):
        if not self.search_context or not self.viewer or self.viewer.read_only:
            return
        buf = self.viewer.source_buffer
        bounds = buf.get_selection_bounds()
        if bounds:
            replacement = self.replace_entry.get_text()
            try:
                self.search_context.replace(bounds[0], bounds[1], replacement, -1)
            except Exception:
                pass
        self.find_next(advance=True)

    def replace_all(self):
        if not self.search_context or not self.viewer or self.viewer.read_only:
            return
        replacement = self.replace_entry.get_text()
        try:
            count = self.search_context.replace_all(replacement, -1)
            self.lbl_status.set_text(f"Replaced {count} occurrence(s)")
        except Exception:
            pass

    def _update_match_count(self):
        if not self.search_context:
            return
        query = self.search_settings.get_search_text()
        if not query:
            self.lbl_status.set_text("")
            return
        count = self.search_context.get_occurrences_count()
        if count > 0:
            self.lbl_status.set_text(f"{count} match(es)")
        elif count == 0:
            self.lbl_status.set_text("No matches")
