"""
PropertiesView - GUI editor for server.properties.
Uses Adw.PreferencesPage with typed rows.
"""
from typing import Optional

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw

from hosty.backend.config_manager import ConfigManager
from hosty.backend.server_manager import ServerManager, ServerInfo
from hosty.utils.constants import (
    DIFFICULTIES,
    GAMEMODES,
    LEVEL_TYPES,
    LEVEL_TYPE_NAMES,
    MIN_RAM_MB,
    MAX_RAM_MB,
    DEFAULT_RAM_MB,
)


class PropertiesView(Gtk.Box):
    """GUI editor for server.properties using Adwaita preference widgets."""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._config: Optional[ConfigManager] = None
        self._server_manager: Optional[ServerManager] = None
        self._server_info: Optional[ServerInfo] = None
        self._widgets: dict = {}
        self._ram_row: Optional[Adw.SpinRow] = None
        self._suppress_changes = False
        
        # Restart banner
        self._banner = Adw.Banner()
        self._banner.set_title("Restart the server to apply changes")
        self._banner.set_button_label("Dismiss")
        self._banner.set_revealed(False)
        self._banner.connect("button-clicked", lambda b: b.set_revealed(False))
        self.append(self._banner)
        
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        page = Adw.PreferencesPage()
        
        # ===== General Group =====
        general = Adw.PreferencesGroup(title="General")

        self._widgets["motd"] = self._add_entry_row(
            general, "Message of the Day", "motd", "a hosty server"
        )
        
        self._widgets["max-players"] = self._add_spin_row(
            general, "Max Players", "max-players", 1, 1000, 20
        )
        self._widgets["difficulty"] = self._add_combo_row(
            general, "Difficulty", "difficulty", DIFFICULTIES, "easy"
        )
        self._widgets["gamemode"] = self._add_combo_row(
            general, "Default Gamemode", "gamemode", GAMEMODES, "survival"
        )
        
        page.add(general)
        
        # ===== Resources (Hosty — not in server.properties) =====
        resources = Adw.PreferencesGroup(title="Resources")
        ram_adj = Gtk.Adjustment(
            value=DEFAULT_RAM_MB,
            lower=MIN_RAM_MB,
            upper=MAX_RAM_MB,
            step_increment=256,
            page_increment=1024,
        )
        self._ram_row = Adw.SpinRow(
            title="Allocated RAM (MB)",
            adjustment=ram_adj,
        )
        self._ram_row.set_tooltip_text(
            f"Megabytes for the Java heap. Range {MIN_RAM_MB}–{MAX_RAM_MB}. "
            "Restart the server for a change to apply."
        )
        resources.add(self._ram_row)
        page.add(resources)
        
        # ===== World Group =====
        world = Adw.PreferencesGroup(title="World")
        
        self._widgets["level-seed"] = self._add_entry_row(
            world, "World Seed", "level-seed", ""
        )
        self._widgets["level-type"] = self._add_combo_row(
            world, "World Type", "level-type",
            [LEVEL_TYPE_NAMES.get(t, t) for t in LEVEL_TYPES],
            "Default"
        )
        self._widgets["view-distance"] = self._add_spin_row(
            world, "View Distance", "view-distance", 2, 32, 10
        )
        self._widgets["simulation-distance"] = self._add_spin_row(
            world, "Simulation Distance", "simulation-distance", 2, 32, 10
        )
        self._widgets["spawn-protection"] = self._add_spin_row(
            world, "Spawn Protection Radius", "spawn-protection", 0, 256, 16
        )
        self._widgets["max-world-size"] = self._add_spin_row(
            world, "Max World Size", "max-world-size", 1000, 29999984, 29999984
        )
        
        page.add(world)
        
        # ===== Network Group =====
        network = Adw.PreferencesGroup(title="Network")
        
        self._widgets["server-port"] = self._add_spin_row(
            network, "Server Port", "server-port", 1024, 65535, 25565
        )
        self._widgets["online-mode"] = self._add_switch_row(
            network, "Online Mode", "online-mode", True, ""
        )
        self._widgets["enable-query"] = self._add_switch_row(
            network, "Enable Query", "enable-query", False, ""
        )
        
        page.add(network)
        
        # ===== Players Group =====
        players = Adw.PreferencesGroup(title="Players")
        
        self._widgets["pvp"] = self._add_switch_row(
            players, "PvP", "pvp", True, ""
        )
        self._widgets["allow-flight"] = self._add_switch_row(
            players, "Allow Flight", "allow-flight", False, ""
        )
        self._widgets["white-list"] = self._add_switch_row(
            players, "Whitelist", "white-list", False, ""
        )
        
        page.add(players)
        
        # ===== Advanced Group =====
        advanced = Adw.PreferencesGroup(title="Advanced")
        
        self._widgets["enable-command-block"] = self._add_switch_row(
            advanced, "Command Blocks", "enable-command-block", False, ""
        )
        self._widgets["allow-nether"] = self._add_switch_row(
            advanced, "Allow Nether", "allow-nether", True, ""
        )
        self._widgets["hardcore"] = self._add_switch_row(
            advanced, "Hardcore Mode", "hardcore", False, ""
        )
        self._widgets["enable-rcon"] = self._add_switch_row(
            advanced, "Enable RCON", "enable-rcon", False, ""
        )
        
        page.add(advanced)
        
        scrolled.set_child(page)
        self.append(scrolled)
        
        self._connect_auto_save_signals()

    def _connect_auto_save_signals(self):
        for widget in self._widgets.values():
            if isinstance(widget, Adw.EntryRow):
                widget.connect("changed", self._on_widget_changed)
            elif isinstance(widget, Adw.SpinRow):
                widget.connect("changed", self._on_widget_changed)
            elif isinstance(widget, Adw.SwitchRow):
                widget.connect("notify::active", self._on_widget_changed)
            elif isinstance(widget, Adw.ComboRow):
                widget.connect("notify::selected", self._on_widget_changed)

        if self._ram_row:
            self._ram_row.connect("changed", self._on_widget_changed)
    
    def _add_entry_row(self, group, title, key, default):
        """Add an Adw.EntryRow to a group."""
        row = Adw.EntryRow(title=title)
        row.set_text(default)
        row._prop_key = key
        group.add(row)
        return row
    
    def _add_spin_row(self, group, title, key, min_val, max_val, default):
        """Add an Adw.SpinRow to a group."""
        adj = Gtk.Adjustment(
            value=default, lower=min_val, upper=max_val,
            step_increment=1, page_increment=10
        )
        row = Adw.SpinRow(title=title, adjustment=adj)
        row._prop_key = key
        group.add(row)
        return row
    
    def _add_switch_row(self, group, title, key, default, subtitle=""):
        """Add an Adw.SwitchRow to a group."""
        row = Adw.SwitchRow(title=title)
        if subtitle:
            row.set_subtitle(subtitle)
        row.set_active(default)
        row._prop_key = key
        group.add(row)
        return row
    
    def _add_combo_row(self, group, title, key, options, default):
        """Add an Adw.ComboRow to a group."""
        string_list = Gtk.StringList.new(options)
        row = Adw.ComboRow(title=title, model=string_list)
        row._prop_key = key
        row._options = options
        
        # Set default selection
        try:
            idx = options.index(default)
            row.set_selected(idx)
        except ValueError:
            row.set_selected(0)
        
        group.add(row)
        return row
    
    def set_config(
        self,
        config: ConfigManager,
        server_manager: Optional[ServerManager] = None,
        server_info: Optional[ServerInfo] = None,
    ):
        """Load a server's config into the view."""
        self._config = config
        self._server_manager = server_manager
        self._server_info = server_info
        if config:
            config.load()
            self._populate()
    
    def _populate(self):
        """Populate widgets from config."""
        if not self._config:
            return
        
        self._suppress_changes = True
        
        if self._ram_row and self._server_info:
            self._ram_row.set_value(float(self._server_info.ram_mb))
        elif self._ram_row:
            self._ram_row.set_value(float(DEFAULT_RAM_MB))
        
        for key, widget in self._widgets.items():
            if isinstance(widget, Adw.EntryRow):
                val = self._config.get(key, "")
                widget.set_text(val)
            elif isinstance(widget, Adw.SpinRow):
                val = self._config.get_int(key, int(widget.get_adjustment().get_value()))
                widget.set_value(val)
            elif isinstance(widget, Adw.SwitchRow):
                val = self._config.get_bool(key, widget.get_active())
                widget.set_active(val)
            elif isinstance(widget, Adw.ComboRow):
                val = self._config.get(key, "")
                options = widget._options
                # For level-type, map from raw value to display name
                if key == "level-type":
                    display_val = LEVEL_TYPE_NAMES.get(val, val)
                    try:
                        idx = options.index(display_val)
                        widget.set_selected(idx)
                    except ValueError:
                        widget.set_selected(0)
                else:
                    try:
                        idx = options.index(val)
                        widget.set_selected(idx)
                    except ValueError:
                        widget.set_selected(0)
        
        self._suppress_changes = False
    
    def _on_widget_changed(self, *_args):
        if self._suppress_changes:
            return
        self._save_properties()

    def _save_properties(self):
        """Save properties to file."""
        if not self._config:
            return
        
        for key, widget in self._widgets.items():
            if isinstance(widget, Adw.EntryRow):
                self._config.set_value(key, widget.get_text())
            elif isinstance(widget, Adw.SpinRow):
                self._config.set_value(key, int(widget.get_value()))
            elif isinstance(widget, Adw.SwitchRow):
                self._config.set_value(key, widget.get_active())
            elif isinstance(widget, Adw.ComboRow):
                idx = widget.get_selected()
                options = widget._options
                if key == "level-type":
                    # Map display name back to raw value
                    display_name = options[idx] if idx < len(options) else options[0]
                    raw_val = next(
                        (k for k, v in LEVEL_TYPE_NAMES.items() if v == display_name),
                        LEVEL_TYPES[0]
                    )
                    self._config.set_value(key, raw_val)
                else:
                    val = options[idx] if idx < len(options) else options[0]
                    self._config.set_value(key, val)
        
        self._config.save()
        running = False
        if self._server_manager and self._server_info and self._ram_row:
            ram_mb = int(self._ram_row.get_value())
            if ram_mb != int(self._server_info.ram_mb):
                self._server_manager.update_server_ram(self._server_info.id, ram_mb)

            process = self._server_manager.get_process(self._server_info.id)
            if process:
                process.set_max_players(self._config.get_int("max-players", 20))
                running = bool(process.is_running)

            self._server_manager.emit_on_main_thread("server-changed", self._server_info.id)

        self._banner.set_revealed(running)

    def focus_save_button(self):
        """Compatibility no-op after removing explicit save button."""
        return
