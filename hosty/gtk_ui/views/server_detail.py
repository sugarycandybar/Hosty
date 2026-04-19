"""
ServerDetailView - Main detail container with ViewStack for Console, Performance, Properties.
Uses Adw.ToolbarView for proper Adwaita header bar integration.
"""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from typing import Optional

from gi.repository import Gtk, Adw, GLib

from hosty.gtk_ui.views.console_view import ConsoleView
from hosty.gtk_ui.views.connect import ConnectView
from hosty.gtk_ui.views.performance_view import PerformanceView
from hosty.gtk_ui.views.properties_view import PropertiesView
from hosty.gtk_ui.views.files import FilesView
from hosty.shared.backend.server_manager import ServerManager, ServerInfo
from hosty.shared.backend.server_process import ServerProcess
from hosty.shared.utils.constants import ServerStatus


class ServerDetailView(Gtk.Box):
    """
    Detail view for a selected server.
    Uses Adw.ToolbarView with ViewSwitcherTitle for proper Adwaita integration.
    """
    
    def __init__(self, server_manager: ServerManager):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._server_manager = server_manager
        self._current_server: ServerInfo = None
        self._selected_process: ServerProcess = None
        self._console_attached_process: ServerProcess = None
        self._selected_status_handler_id = None
        self._running_status_handler_id = None
        self._running_watched_process: Optional[ServerProcess] = None
        self._mods_operation_handler_id = None
        
        self._toolbar_view = Adw.ToolbarView()
        self._toolbar_view.set_hexpand(True)
        self._toolbar_view.set_vexpand(True)
        self.append(self._toolbar_view)
        
        # ===== Header Bar =====
        self._header = Adw.HeaderBar()
        self._header.set_show_start_title_buttons(False)
        
        # View switcher title — handles both title display and view switching
        self._view_switcher_title = Adw.ViewSwitcherTitle()
        self._view_switcher_title.set_title("Server")
        self._header.set_title_widget(self._view_switcher_title)
        
        # Start/Stop button — use standard Adwaita suggested-action / destructive-action
        self._toggle_btn = Gtk.Button(label="Start")
        self._toggle_btn.add_css_class("suggested-action")
        self._toggle_btn.connect("clicked", self._on_toggle_clicked)
        self._header.pack_end(self._toggle_btn)
        
        self._toolbar_view.add_top_bar(self._header)
        
        # ===== Content: shared banner (all tabs) + view stack =====
        self._selection_banner = Adw.Banner()
        self._selection_banner.add_css_class("selection-context-banner")
        self._selection_banner.set_revealed(False)
        self._selection_banner.set_visible(False)
        
        self._view_stack = Adw.ViewStack()
        self._view_stack.set_vexpand(True)
        self._view_stack.connect("notify::visible-child-name", self._on_tab_changed)
        self._view_switcher_title.set_stack(self._view_stack)
        
        # Console view
        self._console_view = ConsoleView()
        self._view_stack.add_titled_with_icon(
            self._console_view, "console", "Console", "utilities-terminal-symbolic"
        )

        self._connect_view = ConnectView()
        self._view_stack.add_titled_with_icon(
            self._connect_view, "connect", "Connect", "network-workgroup-symbolic"
        )
        
        # Performance view
        self._perf_view = PerformanceView()
        self._view_stack.add_titled_with_icon(
            self._perf_view, "performance", "Performance", "computer-symbolic"
        )
        
        # Properties view
        self._props_view = PropertiesView()
        self._view_stack.add_titled_with_icon(
            self._props_view, "properties", "Properties", "emblem-system-symbolic"
        )
        
        self._files_view = FilesView()
        self._view_stack.add_titled_with_icon(
            self._files_view, "files", "Files", "folder-symbolic"
        )
        self._view_stack.set_visible_child_name("connect")
        
        self._detail_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._detail_content.set_hexpand(True)
        self._detail_content.set_vexpand(True)
        self._detail_content.append(self._view_stack)
        self._toolbar_view.set_content(self._detail_content)
        
        # Bottom view switcher bar (for narrow layouts)
        self._switcher_bar = Adw.ViewSwitcherBar()
        self._switcher_bar.set_stack(self._view_stack)
        self._switcher_bar.set_reveal(False)
        self._view_switcher_title.connect(
            "notify::title-visible", self._on_switcher_title_visible_changed
        )
        self._toolbar_view.add_bottom_bar(self._switcher_bar)
        GLib.idle_add(self._sync_switcher_bar_reveal)

        self._mods_operation_handler_id = self._server_manager.connect(
            "mods-operation-changed", self._on_mods_operation_changed
        )

    def _on_switcher_title_visible_changed(self, *_args):
        """Reveal the bottom switcher only in compact layouts."""
        self._sync_switcher_bar_reveal()

    def _sync_switcher_bar_reveal(self):
        """Keep bottom switcher visibility in sync with title visibility."""
        self._switcher_bar.set_reveal(self._view_switcher_title.get_title_visible())
        return False
    
    def load_server(self, server_info: ServerInfo):
        """Load a server's details into the view."""
        self._current_server = server_info
        
        if not server_info:
            return
        
        # Update title
        self._view_switcher_title.set_title(
            f"{server_info.name} · {server_info.mc_version}"
        )
        self._view_switcher_title.set_subtitle("")
        
        # Get/create the server process for the selected server (start/stop, status row)
        selected = self._server_manager.get_process(server_info.id)
        self._set_selected_process(selected)
        self._attach_io_to_running_or_selected(server_info)
        
        # Load properties
        config = self._server_manager.get_config(server_info.id)
        self._props_view.set_config(config, self._server_manager, server_info)
        self._files_view.set_server(server_info, self._server_manager)
        self._connect_view.set_server(server_info, self._server_manager)
        
        # Update toggle from the selected server's process
        self._update_toggle_for_selected(
            selected.status if selected else ServerStatus.STOPPED
        )

    def _set_selected_process(self, process: ServerProcess):
        """Connect status updates for the sidebar-selected server (Start/Stop UI)."""
        if self._selected_process and self._selected_status_handler_id:
            try:
                self._selected_process.disconnect(self._selected_status_handler_id)
            except Exception:
                pass
        self._selected_status_handler_id = None
        self._selected_process = process
        if process:
            self._selected_status_handler_id = process.connect(
                "status-changed", self._on_selected_status_changed
            )

    def _set_running_status_watch(self, process: Optional[ServerProcess]):
        """When viewing a different server than the one running, watch the runner for stop."""
        if self._running_status_handler_id and self._running_watched_process:
            try:
                self._running_watched_process.disconnect(self._running_status_handler_id)
            except Exception:
                pass
        self._running_status_handler_id = None
        self._running_watched_process = None
        if process and process is not self._selected_process:
            self._running_watched_process = process
            self._running_status_handler_id = process.connect(
                "status-changed", self._on_running_status_changed
            )

    def _on_running_status_changed(self, process, status):
        """When the running server stops while another is selected, refresh attachment."""
        if self._current_server and not process.is_running:
            GLib.idle_add(
                lambda: self.load_server(self._current_server)
                if self._current_server
                else None
            )

    def _set_selection_context_banner(
        self, running_name: Optional[str], selected_name: Optional[str]
    ):
        """One line under the header, visible on Console, Performance, and Properties."""
        if running_name and selected_name:
            self._selection_banner.set_title(
                f'Console and performance use "{running_name}" (running). '
                f'Sidebar selection is "{selected_name}".'
            )
            self._selection_banner.set_visible(True)
            self._selection_banner.set_revealed(True)
        else:
            self._selection_banner.set_revealed(False)
            self._selection_banner.set_visible(False)

    def _attach_io_to_running_or_selected(self, server_info: ServerInfo):
        """
        Console and Performance follow the running server if exactly one is running
        and the user selected a different server — only one process can run at a time.
        """
        running_id = self._server_manager.get_running_server_id()
        if running_id and running_id != server_info.id:
            io_process = self._server_manager.get_process(running_id)
            running_info = self._server_manager.get_server(running_id)
            rn = running_info.name if running_info else "Server"
            self._set_selection_context_banner(rn, server_info.name)
            self._set_running_status_watch(io_process)
        else:
            self._set_selection_context_banner(None, None)
            io_process = self._selected_process
            self._set_running_status_watch(None)

        if io_process is None:
            return

        # Clear console only when switching to a different attached process object
        prev = self._console_attached_process
        if prev is not None and prev is not io_process:
            self._console_view.clear()
        self._console_attached_process = io_process

        self._console_view.set_process(io_process)
        self._perf_view.set_process(io_process)
        self._sync_perf_with_io_process()

    def _sync_perf_with_io_process(self):
        """Perf follows the process attached to the console (running server or selection)."""
        p = self._console_attached_process
        if p and p.is_running:
            self._perf_view.start_monitoring()
        else:
            self._perf_view.stop_monitoring()
            self._perf_view.reset()

    def _on_selected_status_changed(self, process, status):
        """Handle selected server's process status (Start/Stop button)."""
        self._update_toggle_for_selected(status)
        self._sync_perf_with_io_process()

    def _update_toggle_for_selected(self, status: str):
        """Update Start/Stop from the sidebar-selected server's process."""
        self._toggle_btn.remove_css_class("hosty-starting-button")
        selected_id = self._current_server.id if self._current_server else ""
        mods_busy = bool(selected_id) and self._server_manager.is_mod_operation_active(selected_id)

        if status == ServerStatus.STARTING:
            self._toggle_btn.set_label("Starting")
            self._toggle_btn.remove_css_class("suggested-action")
            self._toggle_btn.remove_css_class("destructive-action")
            self._toggle_btn.add_css_class("hosty-starting-button")
            self._toggle_btn.set_sensitive(False)
            self._toggle_btn.set_tooltip_text("Wait for the server to finish starting")
            return

        if status == ServerStatus.RUNNING:
            self._toggle_btn.set_label("Stop")
            self._toggle_btn.remove_css_class("suggested-action")
            self._toggle_btn.add_css_class("destructive-action")
            self._toggle_btn.set_sensitive(True)
            self._toggle_btn.set_tooltip_text(None)
        else:
            self._toggle_btn.set_label("Start")
            self._toggle_btn.remove_css_class("destructive-action")
            self._toggle_btn.add_css_class("suggested-action")
            blocked = (
                self._server_manager.is_any_server_running()
                and self._selected_process is not None
                and not self._selected_process.is_running
            )
            self._toggle_btn.set_sensitive((not blocked) and (not mods_busy))
            if mods_busy:
                self._toggle_btn.set_tooltip_text("Mods are currently installing/updating")
            elif blocked:
                self._toggle_btn.set_tooltip_text("Another server is already running")
            else:
                self._toggle_btn.set_tooltip_text(None)

    def _on_mods_operation_changed(self, _manager, server_id: str, _active: bool, _count: int):
        if not self._current_server:
            return
        if self._current_server.id != server_id:
            return
        status = self._selected_process.status if self._selected_process else ServerStatus.STOPPED
        self._update_toggle_for_selected(status)

    def _on_tab_changed(self, stack, _pspec):
        """Keep tab navigation predictable when changing pages."""
        tab_name = stack.get_visible_child_name()
        if tab_name == "performance":
            self._perf_view.scroll_to_top()
        elif tab_name == "properties":
            self._props_view.reload_from_disk()
            GLib.idle_add(self._props_view.focus_save_button)
        elif tab_name == "files":
            self._files_view.refresh_worlds_if_changed(force=True)

    def poll_runtime_state(self) -> None:
        """Refresh lightweight live UI bits from the window polling loop."""
        if self._view_stack.get_visible_child_name() == "files":
            self._files_view.refresh_worlds_if_changed()
    
    def _on_toggle_clicked(self, button):
        """Handle start/stop button click."""
        if not self._selected_process:
            return

        if self._selected_process.status == ServerStatus.STARTING:
            return
        
        if self._selected_process.is_running:
            self._selected_process.stop()
        else:
            if self._current_server and self._server_manager.is_mod_operation_active(self._current_server.id):
                dialog = Adw.AlertDialog.new(
                    "Cannot Start Server",
                    "Mods are currently being installed or updated. Please wait for the operation to finish.",
                )
                dialog.add_response("ok", "OK")
                dialog.present(self.get_root())
                return

            if self._server_manager.is_any_server_running():
                dialog = Adw.AlertDialog.new(
                    "Cannot Start Server",
                    "Another server is already running. Please stop it first before starting a new one."
                )
                dialog.add_response("ok", "OK")
                dialog.present(self.get_root())
                return
                
            self._selected_process.start()
    
    def get_console_view(self) -> ConsoleView:
        return self._console_view
    
    def get_perf_view(self) -> PerformanceView:
        return self._perf_view
    
    def get_props_view(self) -> PropertiesView:
        return self._props_view
    
    def get_files_view(self) -> FilesView:
        return self._files_view
