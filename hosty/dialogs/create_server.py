"""
CreateServerDialog - Multi-step dialog for creating a new Fabric server.
"""
from pathlib import Path
import threading

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, GObject, Gio

from hosty.backend.server_manager import ServerManager
from hosty.utils.image_utils import convert_to_png
from hosty.utils.constants import (
    MIN_RAM_MB, MAX_RAM_MB, get_required_java_version, DEFAULT_SERVER_PROPERTIES
)


OPTIMISATION_MODS = [
    ("lithium", "Lithium"),
    ("ferrite-core", "FerriteCore"),
    ("c2me-fabric", "Concurrent Chunk Management Engine"),
    ("fast-noise", "Fast Noise"),
    ("vmp-fabric", "Very Many Players"),
    ("scalablelux", "ScalableLux"),
    ("krypton", "Krypton"),
    ("modernfix", "ModernFix"),
]


class CreateServerDialog(Adw.Dialog):
    """Dialog for creating a new Fabric Minecraft server."""
    
    __gsignals__ = {
        'server-created': (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }
    
    def __init__(self, server_manager: ServerManager):
        super().__init__()
        self._server_manager = server_manager
        self._game_versions: list[str] = []
        self._loader_versions: list[str] = []
        self._icon_source_path: str = ""
        
        self.set_title("Create Server")
        self.set_content_width(500)
        self.set_content_height(600)
        
        # Main content
        self._toolbar_view = Adw.ToolbarView()
        
        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(False)
        header.set_show_end_title_buttons(False)
        
        self._cancel_btn = Gtk.Button(label="Cancel")
        self._cancel_btn.connect("clicked", self._on_cancel_clicked)
        header.pack_start(self._cancel_btn)
        
        self._create_btn = Gtk.Button(label="Next")
        self._create_btn.add_css_class("suggested-action")
        self._create_btn.set_sensitive(False)
        self._create_btn.connect("clicked", self._on_primary_clicked)
        header.pack_end(self._create_btn)
        
        self._toolbar_view.add_top_bar(header)
        
        # Stack for config vs progress
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        
        # ===== First Step =====
        details_page = self._build_details_page()
        self._stack.add_named(details_page, "details")

        # ===== Second Step =====
        runtime_page = self._build_runtime_page()
        self._stack.add_named(runtime_page, "runtime")
        
        # ===== Progress Page =====
        progress_page = self._build_progress_page()
        self._stack.add_named(progress_page, "progress")

        self._stack.connect("notify::visible-child-name", self._on_page_changed)
        
        self._toolbar_view.set_content(self._stack)
        self.set_child(self._toolbar_view)
        
        # Fetch versions
        self._fetch_versions()
    
    def _build_details_page(self) -> Gtk.Widget:
        """Build step 1: basic identity and legal confirmation."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Adw.PreferencesPage()

        info_group = Adw.PreferencesGroup(
            title="Server Info",
            description="Name your server and configure initial world details",
        )

        self._name_entry = Adw.EntryRow(title="Server Name")
        self._name_entry.set_text("My Server")
        self._name_entry.connect("changed", self._validate)
        info_group.add(self._name_entry)

        self._seed_entry = Adw.EntryRow(title="World Seed")
        self._seed_entry.set_text("")
        self._seed_entry.set_show_apply_button(False)
        info_group.add(self._seed_entry)

        self._icon_row = Adw.ActionRow(
            title="Server Icon",
            subtitle="No icon selected",
        )
        choose_icon_btn = Gtk.Button(label="Choose…")
        choose_icon_btn.add_css_class("pill")
        choose_icon_btn.connect("clicked", self._on_choose_icon)
        self._icon_row.add_suffix(choose_icon_btn)
        self._icon_row.set_activatable_widget(choose_icon_btn)
        info_group.add(self._icon_row)

        page.add(info_group)

        legal_group = Adw.PreferencesGroup(
            title="Minecraft EULA",
        )

        self._eula_row = Adw.SwitchRow(
            title="I agree to Minecraft EULA",
            subtitle="Required to complete server creation",
        )
        self._eula_row.set_active(False)
        self._eula_row.connect("notify::active", self._validate)
        legal_group.add(self._eula_row)

        page.add(legal_group)

        scrolled.set_child(page)
        return scrolled

    def _build_runtime_page(self) -> Gtk.Widget:
        """Build step 2: versions, runtime, and optional optimizations."""
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        page = Adw.PreferencesPage()

        version_group = Adw.PreferencesGroup(
            title="Runtime",
            description="Choose Minecraft and Fabric versions",
        )

        self._mc_version_list = Gtk.StringList.new(["Loading..."])
        self._mc_version_row = Adw.ComboRow(
            title="Minecraft version",
            model=self._mc_version_list,
        )
        self._mc_version_row.set_sensitive(False)
        self._mc_version_row.connect("notify::selected", self._on_mc_version_changed)
        version_group.add(self._mc_version_row)

        self._loader_version_list = Gtk.StringList.new(["Loading..."])
        self._loader_version_row = Adw.ComboRow(
            title="Fabric loader",
            model=self._loader_version_list,
        )
        self._loader_version_row.set_sensitive(False)
        self._loader_version_row.connect("notify::selected", self._validate)
        version_group.add(self._loader_version_row)

        self._java_info_row = Adw.ActionRow(
            title="Java Runtime",
            subtitle="Detecting...",
        )
        self._java_info_row.set_activatable(False)
        version_group.add(self._java_info_row)

        page.add(version_group)

        resources_group = Adw.PreferencesGroup(
            title="Resources",
            description="Server resource allocation",
        )

        ram_adj = Gtk.Adjustment(
            value=self._server_manager.preferences.default_ram_mb,
            lower=MIN_RAM_MB,
            upper=MAX_RAM_MB,
            step_increment=256,
            page_increment=1024,
        )
        self._ram_row = Adw.SpinRow(
            title="RAM (MB)",
            subtitle="Memory allocated to the server",
            adjustment=ram_adj,
        )
        resources_group.add(self._ram_row)

        page.add(resources_group)

        mods_group = Adw.PreferencesGroup(
            title="Optional setup",
        )
        self._optimise_row = Adw.SwitchRow(
            title="Install server-optimising mods",
            subtitle="Includes Lithium, FerriteCore, Fast Noise, Very Many Players, ScalableLux, and more",
        )
        self._optimise_row.set_active(False)
        mods_group.add(self._optimise_row)
        page.add(mods_group)

        scrolled.set_child(page)
        return scrolled
    
    def _build_progress_page(self) -> Gtk.Widget:
        """Build the progress/installation page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_valign(Gtk.Align.CENTER)
        box.set_halign(Gtk.Align.CENTER)
        box.set_margin_start(40)
        box.set_margin_end(40)
        
        self._progress_status = Adw.StatusPage()
        self._progress_status.set_icon_name("folder-download-symbolic")
        self._progress_status.set_title("Creating Server")
        self._progress_status.set_description("Preparing...")
        
        # Progress bar
        self._progress_bar = Gtk.ProgressBar()
        self._progress_bar.set_show_text(True)
        self._progress_bar.set_margin_start(40)
        self._progress_bar.set_margin_end(40)
        self._progress_bar.add_css_class("hosty-progress")
        
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        progress_box.append(self._progress_bar)
        
        self._progress_label = Gtk.Label(label="")
        self._progress_label.add_css_class("dim-label")
        progress_box.append(self._progress_label)
        
        self._progress_status.set_child(progress_box)
        
        box.append(self._progress_status)
        return box
    
    def _fetch_versions(self):
        """Fetch available versions from Fabric Meta API."""
        def on_versions(game_vers, loader_vers):
            self._game_versions = game_vers
            self._loader_versions = loader_vers
            GLib.idle_add(self._populate_versions)
        
        self._server_manager.download_manager.fetch_all_versions_async(on_versions)
    
    def _populate_versions(self):
        """Populate version dropdowns (called on main thread)."""
        if self._game_versions:
            new_list = Gtk.StringList.new(self._game_versions)
            self._mc_version_row.set_model(new_list)
            self._mc_version_row.set_sensitive(True)
            self._mc_version_row.set_selected(0)
            self._on_mc_version_changed(self._mc_version_row, None)

        if self._loader_versions:
            loader_list = Gtk.StringList.new(self._loader_versions)
            self._loader_version_row.set_model(loader_list)
            self._loader_version_row.set_sensitive(True)
            self._loader_version_row.set_selected(0)

        self._validate()
    
    def _on_mc_version_changed(self, row, _pspec):
        """Handle MC version selection change."""
        idx = row.get_selected()
        if idx < len(self._game_versions):
            mc_ver = self._game_versions[idx]
            java_ver = get_required_java_version(mc_ver)
            java_mgr = self._server_manager.java_manager
            
            available = java_mgr.is_java_available(java_ver)
            system_ver = java_mgr.system_java_version
            
            if available:
                self._java_info_row.set_subtitle(f"Java {java_ver} ✓ Available")
            elif system_ver and system_ver >= java_ver:
                self._java_info_row.set_subtitle(
                    f"Java {java_ver} needed — system Java {system_ver} can be used"
                )
            else:
                self._java_info_row.set_subtitle(
                    f"Java {java_ver} needed — will be downloaded automatically"
                )

        self._validate()

    def _on_cancel_clicked(self, button):
        if self._stack.get_visible_child_name() == "runtime":
            self._stack.set_visible_child_name("details")
            self._validate()
            return
        self.close()

    def _on_choose_icon(self, *_args):
        dialog = Gtk.FileDialog()
        dialog.set_title("Select Server Icon")

        image_filter = Gtk.FileFilter()
        image_filter.set_name("Images")
        image_filter.add_mime_type("image/png")
        image_filter.add_mime_type("image/jpeg")
        image_filter.add_mime_type("image/webp")
        image_filter.add_mime_type("image/bmp")

        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        dialog.set_filters(filters)
        dialog.set_default_filter(image_filter)

        dialog.open(self.get_root(), None, self._on_icon_file_chosen)

    def _on_icon_file_chosen(self, dialog, result):
        try:
            selected = dialog.open_finish(result)
            if not selected:
                return
            path = selected.get_path() or ""
            if not path:
                return
            self._icon_source_path = path
            self._icon_row.set_subtitle(Path(path).name)
        except GLib.Error:
            return

    def _on_page_changed(self, *_args):
        self._validate()
    
    def _validate(self, *args):
        """Validate current step and update primary action state."""
        name = self._name_entry.get_text().strip()
        has_versions = len(self._game_versions) > 0
        has_loaders = len(self._loader_versions) > 0
        page = self._stack.get_visible_child_name()

        if page == "details":
            has_eula = self._eula_row.get_active()
            self._cancel_btn.set_label("Cancel")
            self._cancel_btn.set_sensitive(True)
            self._create_btn.set_label("Next")
            self._create_btn.set_sensitive(bool(name) and has_eula)
            return

        if page == "runtime":
            self._cancel_btn.set_label("Back")
            self._cancel_btn.set_sensitive(True)
            self._create_btn.set_label("Create")
            self._create_btn.set_sensitive(bool(name) and has_versions and has_loaders)
            return

        self._cancel_btn.set_label("Cancel")
        self._cancel_btn.set_sensitive(False)
        self._create_btn.set_label("Create")
        self._create_btn.set_sensitive(False)

    def _on_primary_clicked(self, button):
        """Move to next step or start creation on the final step."""
        page = self._stack.get_visible_child_name()
        if page == "details":
            self._stack.set_visible_child_name("runtime")
            self._validate()
            return

        if page != "runtime":
            return

        name = self._name_entry.get_text().strip()
        mc_idx = self._mc_version_row.get_selected()
        loader_idx = self._loader_version_row.get_selected()
        mc_version = self._game_versions[mc_idx] if mc_idx < len(self._game_versions) else ""
        loader_version = self._loader_versions[loader_idx] if loader_idx < len(self._loader_versions) else ""
        ram_mb = int(self._ram_row.get_value())
        seed = self._seed_entry.get_text().strip()
        eula_accepted = self._eula_row.get_active()
        install_optimisations = bool(self._optimise_row.get_active())

        if not name or not mc_version or not loader_version or not eula_accepted:
            return

        # Switch to progress page
        self._stack.set_visible_child_name("progress")
        self._create_btn.set_sensitive(False)

        # Run installation in background
        thread = threading.Thread(
            target=self._install_thread,
            args=(
                name,
                mc_version,
                loader_version,
                ram_mb,
                seed,
                eula_accepted,
                self._icon_source_path,
                install_optimisations,
            ),
            daemon=True,
        )
        thread.start()

    def _install_thread(
        self,
        name,
        mc_version,
        loader_version,
        ram_mb,
        seed,
        eula_accepted,
        icon_source_path,
        install_optimisations,
    ):
        """Background installation thread."""
        try:
            java_ver = get_required_java_version(mc_version)
            java_mgr = self._server_manager.java_manager
            dl_mgr = self._server_manager.download_manager

            # Step 1: Ensure JRE is available
            if not java_mgr.is_java_available(java_ver):
                self._update_progress(0.05, "Downloading Java Runtime...", f"JRE {java_ver} for MC {mc_version}")

                success, msg = java_mgr.download_jre_sync(
                    java_ver,
                    progress_callback=lambda frac, msg: self._update_progress(
                        0.05 + frac * 0.20, msg, f"JRE {java_ver}"
                    ),
                )

                if not success:
                    self._show_error(f"Failed to download JRE: {msg}")
                    return

            self._update_progress(0.28, "Downloading Fabric installer...", "")

            # Step 2: Download Fabric installer
            installer_path = dl_mgr.download_installer(
                progress_callback=lambda frac, msg: self._update_progress(
                    0.28 + frac * 0.14, msg, ""
                ),
            )

            if not installer_path:
                self._show_error("Failed to download Fabric installer")
                return

            # Step 3: Create server entry
            self._update_progress(0.44, "Creating server...", "")
            server_info = self._server_manager.add_server(
                name=name,
                mc_version=mc_version,
                loader_version=loader_version,
                ram_mb=ram_mb,
            )

            # Step 3.5: Download vanilla server.jar from Mojang
            self._update_progress(0.48, "Downloading Minecraft server.jar...", f"MC {mc_version}")
            success, msg = dl_mgr.download_server_jar(
                mc_version=mc_version,
                server_dir=str(server_info.server_dir),
                progress_callback=lambda frac, msg: self._update_progress(
                    0.48 + frac * 0.12, msg, f"MC {mc_version}"
                ),
            )

            if not success:
                self._show_error(f"Failed to download server.jar: {msg}")
                return

            # Step 4: Install Fabric
            self._update_progress(0.62, "Installing Fabric server...", f"MC {mc_version}")

            java_path = java_mgr.get_java_path(java_ver)
            if not java_path:
                java_path = java_mgr.get_java_for_mc(mc_version) or "java"

            success, msg = dl_mgr.install_fabric_server(
                java_path=java_path,
                installer_jar=installer_path,
                mc_version=mc_version,
                server_dir=str(server_info.server_dir),
                loader_version=loader_version if loader_version else None,
                progress_callback=lambda frac, msg: self._update_progress(
                    0.62 + frac * 0.24, msg, ""
                ),
            )

            if not success:
                self._show_error(f"Fabric installation failed: {msg}")
                return

            # Step 5: Accept EULA
            self._update_progress(0.88, "Applying server settings...", "")
            from hosty.backend.config_manager import ConfigManager
            config = ConfigManager(str(server_info.server_dir))
            config.load()
            config.set_value("motd", DEFAULT_SERVER_PROPERTIES.get("motd", "a hosty server"))
            config.set_value("level-seed", seed)
            config.save()
            config.set_eula(bool(eula_accepted))

            # Step 6: Save icon if selected
            if icon_source_path:
                self._update_progress(0.92, "Applying server icon...", "")
                try:
                    icon_output = server_info.server_dir / "icon.png"
                    convert_to_png(icon_source_path, str(icon_output), size=128)
                    self._server_manager.set_server_icon(server_info.id, str(icon_output))
                except Exception:
                    pass

            # Step 7: Optional performance mods
            if install_optimisations:
                self._update_progress(0.94, "Installing server-optimising mods...", "0/0")
                self._install_optimising_mods(server_info.server_dir, mc_version)

            # Done!
            self._show_success(server_info.id)

        except Exception as e:
            self._show_error(f"Unexpected error: {e}")

    def _install_optimising_mods(self, server_dir: Path, mc_version: str) -> None:
        from hosty.backend import modrinth_client

        mods_dir = Path(server_dir) / "mods"
        mods_dir.mkdir(parents=True, exist_ok=True)
        installed = {p.name.lower() for p in mods_dir.glob("*.jar")}

        total = len(OPTIMISATION_MODS)
        done = 0
        for slug, title in OPTIMISATION_MODS:
            done += 1
            progress = 0.94 + (done / max(1, total)) * 0.05
            self._update_progress(progress, "Installing server-optimising mods...", f"{done}/{total} · {title}")
            try:
                version = self._find_supported_optimisation_version(
                    modrinth_client,
                    slug,
                    mc_version,
                )
                if not version:
                    continue
                if version.filename.lower() in installed:
                    continue
                modrinth_client.download_to(version.download_url, mods_dir / version.filename)
                installed.add(version.filename.lower())
            except Exception:
                continue

    def _find_supported_optimisation_version(self, modrinth_client, project_id: str, mc_version: str):
        """Return a Fabric version only when it explicitly supports the selected MC version."""
        versions = modrinth_client.get_project_versions(project_id)
        if not versions:
            return None

        for version in versions:
            has_mc = mc_version in (version.game_versions or [])
            has_loader = "fabric" in [x.lower() for x in (version.loaders or [])]
            if has_mc and has_loader:
                return version
        return None
    
    def _update_progress(self, fraction, title, detail):
        """Update progress on the main thread."""
        def _update():
            self._progress_bar.set_fraction(min(1.0, fraction))
            self._progress_status.set_description(title)
            self._progress_label.set_label(detail)
        GLib.idle_add(_update)
    
    def _show_error(self, message):
        """Show error state on the main thread."""
        def _update():
            self._progress_status.set_icon_name("dialog-error-symbolic")
            self._progress_status.set_title("Creation Failed")
            self._progress_status.set_description(message)
            self._progress_bar.set_fraction(0)
            self._progress_label.set_label("Please try again")
        GLib.idle_add(_update)
    
    def _show_success(self, server_id):
        """Show success state and close dialog."""
        def _update():
            self._progress_status.set_icon_name("object-select-symbolic")
            self._progress_status.set_title("Server Created!")
            self._progress_status.set_description("Your Fabric server is ready to start")
            self._progress_bar.set_fraction(1.0)
            self._progress_label.set_label("")
            
            # Auto-close after 1.5 seconds
            GLib.timeout_add(1500, lambda: self._finish(server_id))
        
        GLib.idle_add(_update)
    
    def _finish(self, server_id):
        """Close dialog and emit signal."""
        self.emit('server-created', server_id)
        self.close()
        return False
