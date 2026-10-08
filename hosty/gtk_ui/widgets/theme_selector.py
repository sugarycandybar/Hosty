"""
HostyThemeSelector - Theme selector (System / Light / Dark circles).

This is a Python port of Ptyxis's PtyxisThemeSelector, which is what the
screenshot in the feature request shows at the top of the hamburger menu:
three circular checkbuttons (split system, white light, black dark) with an
accent-ring + checkmark on the active one.

Original source (LGPL-3.0-or-later, (c) Christian Hergert):
  - src/ptyxis-theme-selector.c / .h  (Ptyxis, gitlab.gnome.org/chergert/ptyxis)
  - src/ptyxis-theme-selector.ui template:
      GtkBox(horizontal, spacing=12) with three GtkCheckButtons:
        follow: classes theme-selector+follow, action-target 'default',
                tooltip "Follow System Style"
        light:  classes theme-selector+light,  action-target 'force-light',
                tooltip "Light Style"
        dark:   classes theme-selector+dark,   action-target 'force-dark',
                tooltip "Dark Style", group=light
  - CSS node name "themeselector" (gtk_widget_class_set_css_name) with the
    style.css block copied verbatim into hosty/gtk_ui/style.css.
  - Embedded via gtk_popover_menu_add_child(popover, selector, "interface-style")
    where the GMenu contains <attribute name="custom">interface-style</attribute>.

Behavioral port notes:
  - Ptyxis binds the three buttons to a single stateful action
    ("win.interface-style", a GPropertyAction over settings->interface-style
    with values 'default'/'force-light'/'force-dark'). Hosty instead binds
    directly to PreferencesManager.theme ('system'/'light'/'dark') and
    Adw.StyleManager, which is Hosty's existing persistence model.
  - The follow button is hidden when the system does not provide a color-scheme
    preference (adw_style_manager_get_system_supports_color_schemes), exactly
    like Ptyxis's on_notify_system_supports_color_schemes_cb.
  - A "dark" css class is kept on the widget following StyleManager:dark,
    like Ptyxis's on_notify_dark_cb.
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk


class ThemeSelector(Gtk.Box):
    """Three-circle theme switcher. Visual clone of PtyxisThemeSelector."""

    __gtype_name__ = "HostyThemeSelector"

    def __init__(self, preferences=None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_hexpand(True)

        self._preferences = preferences
        self._updating = False

        # --- follow (system) ---
        self._follow_btn = Gtk.CheckButton()
        self._follow_btn.add_css_class("theme-selector")
        self._follow_btn.add_css_class("follow")
        self._follow_btn.set_hexpand(True)
        self._follow_btn.set_halign(Gtk.Align.CENTER)
        self._follow_btn.set_focus_on_click(False)
        self._follow_btn.set_tooltip_text(_("Follow System Style"))
        try:
            self._follow_btn.update_property(Gtk.AccessibleProperty.LABEL, _("Follow system style"))
        except Exception:
            pass
        self.append(self._follow_btn)

        # --- light ---
        self._light_btn = Gtk.CheckButton()
        self._light_btn.add_css_class("theme-selector")
        self._light_btn.add_css_class("light")
        self._light_btn.set_hexpand(True)
        self._light_btn.set_halign(Gtk.Align.CENTER)
        self._light_btn.set_focus_on_click(False)
        self._light_btn.set_tooltip_text(_("Light Style"))
        try:
            self._light_btn.update_property(Gtk.AccessibleProperty.LABEL, _("Light style"))
        except Exception:
            pass
        self.append(self._light_btn)

        # --- dark (grouped with light, like Ptyxis groups dark->light) ---
        self._dark_btn = Gtk.CheckButton()
        self._dark_btn.add_css_class("theme-selector")
        self._dark_btn.add_css_class("dark")
        self._dark_btn.set_hexpand(True)
        self._dark_btn.set_halign(Gtk.Align.CENTER)
        self._dark_btn.set_focus_on_click(False)
        self._dark_btn.set_tooltip_text(_("Dark Style"))
        try:
            self._dark_btn.update_property(Gtk.AccessibleProperty.LABEL, _("Dark style"))
        except Exception:
            pass
        self.append(self._dark_btn)

        # Radio behavior across all three (Ptyxis gets this via the shared
        # stateful action; we use an explicit CheckButton group).
        try:
            self._light_btn.set_group(self._follow_btn)
            self._dark_btn.set_group(self._follow_btn)
        except Exception:
            pass

        self._follow_btn.connect("toggled", self._on_button_toggled, "system")
        self._light_btn.connect("toggled", self._on_button_toggled, "light")
        self._dark_btn.connect("toggled", self._on_button_toggled, "dark")

        # Follow system-visibility + dark-class tracking, like Ptyxis.
        try:
            style_manager = Adw.StyleManager.get_default()
            style_manager.connect(
                "notify::system-supports-color-schemes",
                self._on_system_supports_changed,
            )
            style_manager.connect("notify::dark", self._on_dark_changed)
            self._on_system_supports_changed(style_manager, None)
            self._on_dark_changed(style_manager, None)
        except Exception:
            pass

        self.sync_from_preferences()

    # -- Ptyxis: on_notify_system_supports_color_schemes_cb --
    def _on_system_supports_changed(self, style_manager, _pspec):
        try:
            visible = style_manager.get_system_supports_color_schemes()
        except Exception:
            visible = True
        self._follow_btn.set_visible(bool(visible))

    # -- Ptyxis: on_notify_dark_cb --
    def _on_dark_changed(self, style_manager, _pspec):
        try:
            dark = style_manager.get_dark()
        except Exception:
            dark = False
        if dark:
            self.add_css_class("dark")
        else:
            self.remove_css_class("dark")

    def _apply_theme(self, key: str) -> None:
        try:
            style_manager = Adw.StyleManager.get_default()
            if key == "light":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
            elif key == "dark":
                style_manager.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            else:
                style_manager.set_color_scheme(Adw.ColorScheme.DEFAULT)
        except Exception:
            pass
        if self._preferences is not None:
            try:
                self._preferences.theme = key
            except Exception:
                pass

    def _on_button_toggled(self, _btn, key: str) -> None:
        if self._updating:
            return
        btn = {
            "system": self._follow_btn,
            "light": self._light_btn,
            "dark": self._dark_btn,
        }[key]
        if not btn.get_active():
            return
        self._apply_theme(key)

    def sync_from_preferences(self) -> None:
        """Reflect the stored preference in the checked circle."""
        key = "system"
        try:
            if self._preferences is not None:
                key = self._preferences.theme or "system"
        except Exception:
            pass
        if key not in ("system", "light", "dark"):
            key = "system"
        self._updating = True
        try:
            self._follow_btn.set_active(key == "system")
            self._light_btn.set_active(key == "light")
            self._dark_btn.set_active(key == "dark")
        finally:
            self._updating = False


# Same CSS node name Ptyxis uses ("themeselector"), so the style.css block
# copied verbatim from Ptyxis matches: `themeselector checkbutton...`.
try:
    ThemeSelector.set_css_name("themeselector")
except Exception:
    pass
