"""
ConsoleView - Server console/terminal view with command input.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Gdk', '4.0')
from gi.repository import Gtk, GLib, Pango, Gdk

from hosty.backend.server_process import ServerProcess


class ConsoleView(Gtk.Box):
    """Server console with output display and command input."""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._process = None
        self._output_handler_id = None
        self._auto_scroll = True
        self._tab_context = ""
        self._tab_matches: list[str] = []
        self._tab_index = -1
        self._tab_mode = ""
        self._tab_base_command = ""
        self._tab_has_slash = False
        self._applying_completion = False
        self._command_words = sorted({
            "advancement", "attribute", "ban", "ban-ip", "banlist", "bossbar", "clear",
            "clone", "data", "datapack", "debug", "deop", "difficulty", "effect",
            "enchant", "execute", "experience", "fill", "forceload", "function",
            "gamemode", "gamerule", "give", "help", "item", "jfr", "kick", "kill",
            "list", "locate", "loot", "me", "msg", "op", "pardon", "pardon-ip",
            "particle", "perf", "place", "playsound", "publish", "recipe", "reload",
            "save-all", "save-off", "save-on", "say", "schedule", "scoreboard", "seed",
            "setblock", "setidletimeout", "setworldspawn", "spawnpoint", "spectate",
            "spreadplayers", "stop", "stopsound", "summon", "tag", "team", "teammsg",
            "teleport", "tell", "tellraw", "time", "title", "tm", "tp", "trigger",
            "w", "weather", "whitelist", "worldborder", "xp",
        })
        self._subcommand_words = {
            "banlist": ["ips", "players"],
            "datapack": ["disable", "enable", "list"],
            "difficulty": ["easy", "normal", "hard", "peaceful"],
            "gamerule": ["doDaylightCycle", "keepInventory", "mobGriefing", "doMobSpawning"],
            "help": ["1", "2", "3", "4", "5"],
            "save-all": ["flush"],
            "time": ["add", "query", "set"],
            "weather": ["clear", "rain", "thunder"],
            "whitelist": ["add", "list", "off", "on", "reload", "remove"],
            "worldborder": ["add", "center", "damage", "get", "set", "warning"],
        }
        
        # ===== Console output area =====
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_hexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        
        self._textview = Gtk.TextView()
        self._textview.set_editable(False)
        self._textview.set_cursor_visible(False)
        self._textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._textview.set_monospace(True)
        self._textview.set_top_margin(12)
        self._textview.set_bottom_margin(12)
        self._textview.set_left_margin(16)
        self._textview.set_right_margin(16)
        self._textview.add_css_class("console-view")
        
        self._buffer = self._textview.get_buffer()
        
        # Create text tags for coloring
        self._create_tags()
        
        self._scrolled.set_child(self._textview)
        self.append(self._scrolled)
        
        # ===== Command input bar =====
        input_shell = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        input_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        input_bar.add_css_class("console-input-bar")
        input_bar.set_margin_start(8)
        input_bar.set_margin_end(8)
        input_bar.set_margin_bottom(0)
        input_bar.set_margin_top(4)
        
        # Command entry
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Type a command... (Tab to autocomplete)")
        self._entry.set_hexpand(True)
        self._entry.add_css_class("console-input")
        self._entry.connect("activate", self._on_entry_activate)
        self._entry.connect("changed", self._on_entry_changed)
        key_controller = Gtk.EventControllerKey()
        key_controller.connect("key-pressed", self._on_entry_key_pressed)
        self._entry.add_controller(key_controller)
        input_bar.append(self._entry)
        
        # Send button
        send_btn = Gtk.Button(icon_name="mail-send-symbolic")
        send_btn.set_tooltip_text("Send command")
        send_btn.add_css_class("flat")
        send_btn.connect("clicked", self._on_send_clicked)
        input_bar.append(send_btn)

        self._autocomplete_hint = Gtk.Label()
        self._autocomplete_hint.set_halign(Gtk.Align.START)
        self._autocomplete_hint.set_xalign(0.0)
        self._autocomplete_hint.add_css_class("dim-label")
        self._autocomplete_hint.add_css_class("console-autocomplete-hint")
        self._autocomplete_hint.set_margin_start(10)
        self._autocomplete_hint.set_margin_end(10)
        self._autocomplete_hint.set_margin_bottom(8)

        input_shell.append(input_bar)
        input_shell.append(self._autocomplete_hint)
        self.append(input_shell)
        self._update_autocomplete_hint("")
    
    def _create_tags(self):
        """Create text tags for console output coloring."""
        self._tag_hosty = self._buffer.create_tag(
            "hosty", foreground="#7c6bf0", weight=Pango.Weight.BOLD
        )
        self._tag_info = self._buffer.create_tag(
            "info", foreground="#7aa2f7"
        )
        self._tag_warn = self._buffer.create_tag(
            "warn", foreground="#e0af68"
        )
        self._tag_error = self._buffer.create_tag(
            "error", foreground="#f7768e"
        )
        self._tag_time = self._buffer.create_tag(
            "time", foreground="#565f89"
        )
    
    def set_process(self, process: ServerProcess):
        """Connect to a server process for I/O."""
        # Disconnect old process
        if self._process and self._output_handler_id:
            try:
                self._process.disconnect(self._output_handler_id)
            except Exception:
                pass
        
        self._process = process
        if process:
            self._output_handler_id = process.connect(
                'output-received', self._on_output_received
            )
    
    def clear(self):
        """Clear console output."""
        self._buffer.set_text("")
    
    def append_text(self, text: str):
        """Append text to console with syntax highlighting."""
        end_iter = self._buffer.get_end_iter()
        
        # Determine tag based on content
        tag = None
        if text.startswith("[Hosty]"):
            tag = self._tag_hosty
        elif "WARN" in text:
            tag = self._tag_warn
        elif "ERROR" in text or "Exception" in text:
            tag = self._tag_error
        elif "INFO" in text:
            tag = self._tag_info
        
        if tag:
            self._buffer.insert_with_tags(end_iter, text, tag)
        else:
            self._buffer.insert(end_iter, text)
        
        # Auto-scroll to bottom
        if self._auto_scroll:
            GLib.idle_add(self._scroll_to_bottom)
    
    def _scroll_to_bottom(self):
        """Scroll to the bottom of the console."""
        end_iter = self._buffer.get_end_iter()
        self._textview.scroll_to_iter(end_iter, 0.0, True, 0.0, 1.0)
    
    def _on_output_received(self, process, text):
        """Handle output from server process."""
        self.append_text(text)
    
    def _on_entry_activate(self, entry):
        """Handle Enter key in command entry."""
        self._send_command()

    def _on_entry_changed(self, entry):
        if self._applying_completion:
            return
        self._tab_context = ""
        self._tab_matches = []
        self._tab_index = -1
        self._tab_mode = ""
        self._tab_base_command = ""
        self._tab_has_slash = False
        self._update_autocomplete_hint(entry.get_text())

    def _completion_candidates(self, text: str):
        stripped = text.strip()
        if not stripped:
            return None

        body = stripped[1:] if stripped.startswith("/") else stripped
        if not body:
            return None

        ends_with_space = text.endswith(" ")
        parts = body.split()
        if not parts:
            return None

        if len(parts) == 1 and not ends_with_space:
            prefix = parts[0].lower()
            matches = [cmd for cmd in self._command_words if cmd.startswith(prefix)]
            return {
                "mode": "command",
                "prefix": prefix,
                "command": "",
                "matches": matches,
            }

        if len(parts) > 2:
            return None

        command = parts[0].lower()
        subcommands = self._subcommand_words.get(command, [])
        if not subcommands:
            return None

        sub_prefix = "" if ends_with_space or len(parts) == 1 else parts[1].lower()
        matches = [sub for sub in subcommands if sub.startswith(sub_prefix)]
        return {
            "mode": "subcommand",
            "prefix": sub_prefix,
            "command": command,
            "matches": matches,
        }

    def _update_autocomplete_hint(self, text: str):
        default_tip = "Tip: Press Tab to autocomplete. Press Shift+Tab to cycle backwards."
        candidates = self._completion_candidates(text)
        if not candidates:
            self._autocomplete_hint.set_label(default_tip)
            return

        matches = candidates.get("matches", [])
        if not matches:
            self._autocomplete_hint.set_label("No autocomplete matches")
            return

        preview = ", ".join(matches[:4])
        suffix = "..." if len(matches) > 4 else ""
        if candidates.get("mode") == "subcommand":
            cmd = candidates.get("command", "")
            self._autocomplete_hint.set_label(
                f"Autocomplete {cmd}: {preview}{suffix}"
            )
            return
        self._autocomplete_hint.set_label(f"Autocomplete commands: {preview}{suffix}")

    def _apply_tab_completion(self, backwards: bool):
        if not self._tab_matches:
            return True

        step = -1 if backwards else 1
        self._tab_index = (self._tab_index + step) % len(self._tab_matches)
        token = self._tab_matches[self._tab_index]

        if self._tab_mode == "subcommand":
            completed = f"{self._tab_base_command} {token}".strip()
        else:
            completed = token

        if self._tab_has_slash:
            completed = f"/{completed}"

        self._applying_completion = True
        self._entry.set_text(completed)
        self._entry.set_position(-1)
        self._applying_completion = False
        return True

    def _prepare_tab_completion(self, text: str) -> bool:
        stripped = text.strip()
        if not stripped:
            return False

        has_slash = stripped.startswith("/")
        candidates = self._completion_candidates(text)
        if not candidates:
            return False

        prefix = str(candidates.get("prefix", ""))
        command = str(candidates.get("command", ""))
        matches = list(candidates.get("matches", []))
        mode = str(candidates.get("mode", ""))

        if not matches:
            return True

        if mode == "subcommand":
            self._tab_context = f"sub:{command}:{prefix}:{int(has_slash)}"
            self._tab_mode = "subcommand"
            self._tab_base_command = command
        else:
            self._tab_context = f"cmd:{prefix}:{int(has_slash)}"
            self._tab_mode = "command"
            self._tab_base_command = ""

        self._tab_has_slash = has_slash
        self._tab_matches = matches
        self._tab_index = -1
        return True

    def _on_entry_key_pressed(self, _controller, keyval, _keycode, state):
        if keyval not in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            return False

        text = self._entry.get_text()
        if not text.strip():
            return False

        if not self._tab_matches:
            if not self._prepare_tab_completion(text):
                return False

        backwards = bool(state & Gdk.ModifierType.SHIFT_MASK)
        return self._apply_tab_completion(backwards)
    
    def _on_send_clicked(self, button):
        """Handle send button click."""
        self._send_command()
    
    def _send_command(self):
        """Send the current command to the server."""
        text = self._entry.get_text().strip()
        if not text:
            return
        
        if self._process:
            # Echo the command in console
            self.append_text(f"> {text}\n")
            self._process.send_command(text)
        else:
            self.append_text("[Hosty] No server process connected\n")
        
        self._entry.set_text("")
        self._tab_context = ""
        self._tab_matches = []
        self._tab_index = -1
        self._tab_mode = ""
        self._tab_base_command = ""
        self._tab_has_slash = False
        self._update_autocomplete_hint("")
    
