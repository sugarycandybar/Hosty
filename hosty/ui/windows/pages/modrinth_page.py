"""
Modrinth page for the Files tab.

Full feature parity with the Linux GTK4 version:
- Category / sort / type filters
- Pagination (prev/next)
- Version picker per result
- Async icon loading
- Author + download count display
- "Open page" link
- Dependency resolution
- Installed detection
"""

from __future__ import annotations

import threading
import webbrowser
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QProgressBar,
    QScrollArea,
)
from ..components import SmoothScrollArea

from hosty.backend.server_manager import ServerInfo, ServerManager
from hosty.backend import modrinth_client as modrinth
from hosty.core.events import dispatch_on_main_thread

PAGE_SIZE = 10


def _format_compact(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class ModrinthPage(QWidget):
    def __init__(self, server_manager: ServerManager, back_callback):
        super().__init__()
        self._server_manager = server_manager
        self._server_info: Optional[ServerInfo] = None
        self._search_thread = None
        self._install_thread = None
        self._offset = 0
        self._total_results = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header.setProperty("class", "header-bar")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(16, 12, 16, 12)

        back_btn = QPushButton("← Back")
        back_btn.setProperty("class", "flat")
        back_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        back_btn.clicked.connect(back_callback)
        header_layout.addWidget(back_btn)

        title = QLabel("Modrinth")
        title.setProperty("class", "title")
        header_layout.addWidget(title)
        header_layout.addStretch()
        layout.addWidget(header)

        # Search + Filters row
        filters = QWidget()
        filters_layout = QHBoxLayout(filters)
        filters_layout.setContentsMargins(24, 16, 24, 0)
        filters_layout.setSpacing(8)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search Fabric mods…")
        filters_layout.addWidget(self._search_input, 1)

        self._type_combo = QComboBox()
        self._type_combo.addItems(["Mods", "Modpacks"])
        self._type_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        filters_layout.addWidget(self._type_combo)

        self._category_combo = QComboBox()
        self._category_combo.addItems([
            "Any category", "Optimization", "Utility", "Technology",
            "Adventure", "Decoration", "Magic", "Storage", "Worldgen", "Library",
        ])
        self._category_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        filters_layout.addWidget(self._category_combo)

        self._sort_combo = QComboBox()
        self._sort_combo.addItems(["Relevance", "Downloads", "Follows", "Newest", "Recently updated"])
        self._sort_combo.setCurrentIndex(1)  # Default to Downloads
        self._sort_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        filters_layout.addWidget(self._sort_combo)

        layout.addWidget(filters)

        # Pagination + status row
        pager = QWidget()
        pager_layout = QHBoxLayout(pager)
        pager_layout.setContentsMargins(24, 8, 24, 0)
        pager_layout.setSpacing(8)

        self._prev_btn = QPushButton("← Prev")
        self._prev_btn.setProperty("class", "flat")
        self._prev_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._prev_btn.setEnabled(False)
        self._prev_btn.clicked.connect(self._on_prev)
        pager_layout.addWidget(self._prev_btn)

        self._next_btn = QPushButton("Next →")
        self._next_btn.setProperty("class", "flat")
        self._next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._next_btn.setEnabled(False)
        self._next_btn.clicked.connect(self._on_next)
        pager_layout.addWidget(self._next_btn)

        self._page_label = QLabel("Page 1/1")
        self._page_label.setProperty("class", "dim")
        pager_layout.addWidget(self._page_label)

        pager_layout.addStretch()

        self._results_label = QLabel("")
        self._results_label.setProperty("class", "dim")
        pager_layout.addWidget(self._results_label)

        layout.addWidget(pager)

        # Progress bar for installs
        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setFixedHeight(6)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setVisible(False)
        layout.addWidget(self._progress_bar)

        self._status_lbl = QLabel("")
        self._status_lbl.setProperty("class", "dim")
        self._status_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._status_lbl)

        # Search Results
        self._scroll = SmoothScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        self._content = QWidget()
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(24, 16, 24, 24)
        self._content_layout.setSpacing(12)
        self._content_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self._scroll.setWidget(self._content)
        layout.addWidget(self._scroll, 1)

        # Debounce timer
        self._search_timer = QTimer()
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(500)
        self._search_timer.timeout.connect(lambda: self._do_search(reset=True))

        self._search_input.textChanged.connect(lambda text: self._search_timer.start())
        self._search_input.returnPressed.connect(lambda: self._do_search(reset=True))
        self._type_combo.currentIndexChanged.connect(lambda idx: self._on_type_changed())
        self._category_combo.currentIndexChanged.connect(lambda idx: self._search_timer.start())
        self._sort_combo.currentIndexChanged.connect(lambda idx: self._search_timer.start())

    def _on_type_changed(self) -> None:
        if self._type_combo.currentIndex() == 1:
            self._search_input.setPlaceholderText("Search Fabric modpacks…")
        else:
            self._search_input.setPlaceholderText("Search Fabric mods…")
        self._search_timer.start()

    def load_server(self, info: ServerInfo) -> None:
        if self._server_info and self._server_info.id == info.id:
            return
        self._server_info = info
        self._do_search(reset=True)

    def _selected_category(self) -> Optional[str]:
        cats = ["", "optimization", "utility", "technology", "adventure",
                "decoration", "magic", "storage", "worldgen", "library"]
        idx = self._category_combo.currentIndex()
        if 0 <= idx < len(cats):
            c = cats[idx]
            return c if c else None
        return None

    def _selected_sort(self) -> str:
        sorts = ["relevance", "downloads", "follows", "newest", "updated"]
        idx = self._sort_combo.currentIndex()
        if 0 <= idx < len(sorts):
            return sorts[idx]
        return "downloads"

    def _selected_type(self) -> str:
        return "mod" if self._type_combo.currentIndex() == 0 else "modpack"

    def _update_pager(self) -> None:
        total = max(0, self._total_results)
        page = (self._offset // PAGE_SIZE) + 1
        max_page = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        self._page_label.setText(f"Page {page}/{max_page}")
        self._prev_btn.setEnabled(self._offset > 0)
        self._next_btn.setEnabled(self._offset + PAGE_SIZE < total)

    def _on_prev(self) -> None:
        if self._offset >= PAGE_SIZE:
            self._offset -= PAGE_SIZE
            self._do_search(reset=False)

    def _on_next(self) -> None:
        if self._offset + PAGE_SIZE < self._total_results:
            self._offset += PAGE_SIZE
            self._do_search(reset=False)

    def _installed_mod_names(self) -> set:
        if not self._server_info:
            return set()
        mods_dir = self._server_info.server_dir / "mods"
        if not mods_dir.is_dir():
            return set()
        return {p.name.lower() for p in mods_dir.glob("*.jar")}

    def _do_search(self, reset: bool = False) -> None:
        if not self._server_info:
            return
        if reset:
            self._offset = 0

        query = self._search_input.text().strip()
        project_type = self._selected_type()
        game_version = self._server_info.mc_version
        category = self._selected_category()
        sort_key = self._selected_sort()

        self._clear_results()
        self._results_label.setText("Searching…")

        def worker():
            try:
                hits, total = modrinth.search_mods(
                    query=query,
                    limit=PAGE_SIZE,
                    offset=self._offset,
                    sort=sort_key,
                    game_version=game_version,
                    category=category,
                    loader="fabric",
                    server_side_only=True,
                    project_type=project_type,
                )
                dispatch_on_main_thread(lambda: self._populate_results(hits, total, project_type))
            except Exception as e:
                err_msg = str(e)
                dispatch_on_main_thread(lambda: self._show_search_error(err_msg))

        self._search_thread = threading.Thread(target=worker, daemon=True)
        self._search_thread.start()

    def _clear_results(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_search_error(self, err: str) -> None:
        self._clear_results()
        self._results_label.setText("Search failed")
        lbl = QLabel(f"Could not fetch results: {err}")
        lbl.setProperty("class", "dim")
        lbl.setWordWrap(True)
        self._content_layout.addWidget(lbl)

    def _populate_results(self, hits: list, total: int, project_type: str) -> None:
        self._clear_results()
        self._total_results = total
        self._update_pager()
        self._results_label.setText(f"{total:,} results")

        if not hits:
            mc = self._server_info.mc_version if self._server_info else "?"
            lbl = QLabel(f"No {project_type}s found for MC {mc}.")
            lbl.setProperty("class", "dim")
            self._content_layout.addWidget(lbl)
            return

        installed = self._installed_mod_names()
        for hit in hits:
            self._content_layout.addWidget(self._build_result_card(hit, installed))

    def _build_result_card(self, hit, installed: set) -> QWidget:
        is_modpack = str(getattr(hit, "project_type", "mod")).lower() == "modpack"
        card = QWidget()
        card.setProperty("class", "card")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(14, 12, 14, 12)
        outer.setSpacing(10)

        # Icon
        icon_lbl = QLabel()
        icon_lbl.setFixedSize(48, 48)
        icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_lbl.setStyleSheet("border-radius: 10px; background: rgba(0,0,0,0.05);")
        icon_lbl.setText("📦")
        outer.addWidget(icon_lbl, 0, Qt.AlignmentFlag.AlignTop)

        if hit.icon_url:
            self._load_icon_async(icon_lbl, hit.icon_url)

        # Content column
        content = QVBoxLayout()
        content.setSpacing(4)

        # Title + author row
        top_row = QHBoxLayout()
        top_row.setSpacing(8)

        title_lbl = QLabel(hit.title)
        title_lbl.setProperty("class", "title")
        top_row.addWidget(title_lbl, 1)

        author = getattr(hit, "author", None) or "Unknown"
        author_lbl = QLabel(f"by {author}")
        author_lbl.setProperty("class", "dim")
        top_row.addWidget(author_lbl)

        downloads = int(getattr(hit, "downloads", 0) or 0)
        dl_lbl = QLabel(f"↓ {_format_compact(downloads)}")
        dl_lbl.setProperty("class", "dim")
        top_row.addWidget(dl_lbl)

        content.addLayout(top_row)

        # Description
        desc_text = (hit.description or "No description available.").strip()
        desc = QLabel(desc_text)
        desc.setProperty("class", "dim")
        desc.setWordWrap(True)
        desc.setMaximumHeight(40)
        content.addWidget(desc)

        # Version picker + action row
        action_row = QHBoxLayout()
        action_row.setSpacing(8)

        version_combo = QComboBox()
        version_combo.addItem("Loading versions…")
        version_combo.setMinimumWidth(160)
        version_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        action_row.addWidget(version_combo, 1)

        install_btn = QPushButton("Install pack" if is_modpack else "Install")
        install_btn.setProperty("class", "accent")
        install_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        action_row.addWidget(install_btn)

        # Detect if already installed
        slug = (getattr(hit, "slug", "") or "").strip().lower()
        title_key = (hit.title or "").strip().lower().replace(" ", "-")
        looks_installed = (
            (slug and any(slug in n for n in installed))
            or (title_key and any(title_key in n for n in installed))
        )
        if looks_installed:
            install_btn.setText("Installed")
            install_btn.setEnabled(False)

        open_btn = QPushButton("Open page")
        open_btn.setProperty("class", "flat")
        open_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        open_btn.clicked.connect(
            lambda *_, s=hit.slug or hit.project_id, mp=is_modpack: webbrowser.open(
                f"https://modrinth.com/{'modpack' if mp else 'mod'}/{s}"
            )
        )
        action_row.addWidget(open_btn)

        content.addLayout(action_row)
        outer.addLayout(content, 1)

        # Load versions asynchronously
        version_objs = []
        mc_version = self._server_info.mc_version if self._server_info else ""

        def load_versions():
            if not mc_version:
                dispatch_on_main_thread(lambda: version_combo.clear())
                dispatch_on_main_thread(lambda: version_combo.addItem("No server version"))
                return
            try:
                versions = modrinth.find_compatible_versions(
                    hit.project_id, mc_version, loader="fabric", limit=5
                )
                if not versions:
                    dispatch_on_main_thread(lambda: version_combo.clear())
                    dispatch_on_main_thread(lambda: version_combo.addItem("No compatible versions"))
                    return

                names = []
                seen = set()
                for v in versions:
                    vn = (v.version_number or v.name or "").strip()
                    if not vn or vn in seen:
                        continue
                    seen.add(vn)
                    names.append(vn)
                    version_objs.append(v)

                if not names:
                    dispatch_on_main_thread(lambda: version_combo.clear())
                    dispatch_on_main_thread(lambda: version_combo.addItem("No compatible versions"))
                    return

                def ui_set():
                    version_combo.clear()
                    for name in names:
                        version_combo.addItem(name)
                    version_combo.setCurrentIndex(0)

                    # Check if first version file is already installed
                    if version_objs and not is_modpack:
                        fname = version_objs[0].filename.lower() if version_objs[0].filename else ""
                        if fname in installed:
                            install_btn.setText("Installed")
                            install_btn.setEnabled(False)

                dispatch_on_main_thread(ui_set)
            except Exception:
                dispatch_on_main_thread(lambda: version_combo.clear())
                dispatch_on_main_thread(lambda: version_combo.addItem("Version lookup failed"))

        threading.Thread(target=load_versions, daemon=True).start()

        # Install handler
        def on_install():
            if not self._server_info:
                return
            proc = self._server_manager.get_process(self._server_info.id)
            if proc and proc.is_running:
                self._status_lbl.setText("⚠ Stop the server before installing mods.")
                return
            if self._progress_bar.isVisible():
                self._status_lbl.setText("⚠ An installation is already in progress.")
                return

            idx = version_combo.currentIndex()
            if idx < 0 or idx >= len(version_objs):
                self._status_lbl.setText("⚠ No compatible version selected.")
                return

            chosen = version_objs[idx]
            install_btn.setText("Installing…")
            install_btn.setEnabled(False)
            self._progress_bar.setVisible(True)
            self._progress_bar.setValue(0)
            self._status_lbl.setText(f"Preparing to install {hit.title}...")

            def install_worker():
                try:
                    server_dir = self._server_info.server_dir
                    if is_modpack:
                        def cb(progress, total, name):
                            rate = int((progress / max(total, 1)) * 100)
                            dispatch_on_main_thread(lambda: self._progress_bar.setValue(rate))
                            dispatch_on_main_thread(lambda: self._status_lbl.setText(
                                f"Extracting {name} ({progress}/{total})"
                            ))
                        modrinth.install_modpack(chosen.version_id, server_dir, progress_callback=cb)
                    else:
                        dispatch_on_main_thread(lambda: self._status_lbl.setText("Downloading mod file…"))
                        mod_dir = server_dir / "mods"
                        mod_dir.mkdir(exist_ok=True)
                        modrinth.download_to(chosen.download_url, mod_dir / chosen.filename)

                        # Auto dependencies
                        if self._server_manager.preferences.auto_resolve_mod_dependencies:
                            dispatch_on_main_thread(lambda: self._status_lbl.setText("Resolving dependencies…"))
                            deps = modrinth.resolve_required_dependencies(
                                chosen.version_id, self._server_info.mc_version, "fabric"
                            )
                            installed_local = {p.name.lower() for p in mod_dir.glob("*.jar")}
                            for dep in deps:
                                dep_name = dep.filename.lower()
                                if dep_name in installed_local or dep_name == chosen.filename.lower():
                                    continue
                                dispatch_on_main_thread(
                                    lambda n=dep.name: self._status_lbl.setText(f"Downloading {n}…")
                                )
                                try:
                                    modrinth.download_to(dep.download_url, mod_dir / dep.filename)
                                except Exception:
                                    pass

                    dispatch_on_main_thread(lambda: self._finish_install(hit.title, True, "", install_btn))
                except Exception as e:
                    err_msg = str(e)
                    dispatch_on_main_thread(lambda: self._finish_install(hit.title, False, err_msg, install_btn))

            self._install_thread = threading.Thread(target=install_worker, daemon=True)
            self._install_thread.start()

        install_btn.clicked.connect(on_install)
        return card

    def _finish_install(self, name: str, ok: bool, msg: str, btn: QPushButton) -> None:
        self._progress_bar.setVisible(False)
        if ok:
            self._status_lbl.setText(f"✓ Successfully installed '{name}'.")
            btn.setText("Installed")
            btn.setEnabled(False)
        else:
            self._status_lbl.setText(f"✗ Failed to install '{name}': {msg}")
            btn.setText("Install")
            btn.setEnabled(True)

        # Clear the status after a delay
        QTimer.singleShot(5000, lambda: (
            self._status_lbl.setText("") if self._status_lbl.text().startswith(("✓", "✗")) else None
        ))

    def _load_icon_async(self, label: QLabel, url: str) -> None:
        """Load an icon from URL asynchronously and set it on a QLabel."""
        def worker():
            try:
                import urllib.request
                req = urllib.request.Request(url, headers={"User-Agent": "Hosty/1.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = resp.read()
                img = QImage()
                img.loadFromData(data)
                if img.isNull():
                    return
                pixmap = QPixmap.fromImage(img).scaled(
                    44, 44, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )

                def ui_set():
                    label.setPixmap(pixmap)
                    label.setText("")

                dispatch_on_main_thread(ui_set)
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()
