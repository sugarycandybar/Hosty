"""
ConsoleView - Server console/terminal view with command input.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, GLib, Pango

from hosty.shared.backend.server_process import ServerProcess


class ConsoleView(Gtk.Box):
    """Server console with output display and command input."""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._process = None
        self._output_handler_id = None
        self._auto_scroll = True
        
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
        input_bar.set_margin_bottom(8)
        input_bar.set_margin_top(4)
        
        # Command entry
        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Type a command...")
        self._entry.set_hexpand(True)
        self._entry.add_css_class("console-input")
        self._entry.connect("activate", self._on_entry_activate)
        input_bar.append(self._entry)
        
        # Send button
        send_btn = Gtk.Button(icon_name="mail-send-symbolic")
        send_btn.set_tooltip_text("Send command")
        send_btn.add_css_class("flat")
        send_btn.connect("clicked", self._on_send_clicked)
        input_bar.append(send_btn)

        input_shell.append(input_bar)
        self.append(input_shell)
    
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
    
