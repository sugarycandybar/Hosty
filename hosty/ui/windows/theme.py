"""
Theme system for Hosty Windows UI.

Detects the Windows system theme (light/dark) and provides matching
QSS stylesheets with a modern, rounded, beginner-friendly design.
"""

from __future__ import annotations

import os
import sys
import threading
import urllib.request
import weakref
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QMainWindow, QDialog

from PySide6.QtCore import QTimer, QEvent, QObject
from PySide6.QtGui import QFontDatabase, QPalette, QColor
from PySide6.QtWidgets import QApplication, QMainWindow, QDialog

from hosty.backend.preferences_manager import PreferencesManager

from .utils import apply_window_theme


def is_system_dark() -> bool:
    """Detect whether Windows is using dark mode."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        winreg.CloseKey(key)
        return value == 0
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Color tokens
# Light: "Warm Light" — cream, sage green, soft wood-tones
# Dark:  "Comfortable Dark" — deep warm-grey, amber, terracotta
# ---------------------------------------------------------------------------

_DARK = {
    "bg":               "#2A2520",
    "bg_secondary":     "#332D27",
    "bg_card":          "#3B342D",
    "bg_card_hover":    "#453D35",
    "bg_input":         "#231F1A",
    "bg_header":        "#2A2520",
    "bg_sidebar":       "#332D27",
    "bg_sidebar_hover": "#453D35",
    "bg_sidebar_sel":   "#3B342D",
    "border":           "#52483D",
    "border_focus":     "#D4944C",
    "text":             "#E8DDD0",
    "text_secondary":   "#C4B5A3",
    "text_dim":         "#887A6B",
    "accent":           "#D4944C",
    "accent_hover":     "#E0A35A",
    "accent_pressed":   "#B87E3A",
    "destructive":      "#C0614A",
    "destructive_hover":"#D4735C",
    "success":          "#7A9E65",
    "warning":          "#D4944C",
    "info_tag":         "#C4B5A3",
    "warn_tag":         "#D4944C",
    "error_tag":        "#C0614A",

    "scrollbar":        "#6B5F52",
    "scrollbar_hover":  "#7D6F60",
    "tab_bg":           "#332D27",
    "tab_sel":          "#2A2520",
    "tab_hover":        "#3B342D",
    "tab_border":       "#52483D",

    "status_running":   "#7A9E65",
    "status_starting":  "#D4944C",
    "status_stopped":   "#887A6B",

    "tooltip_bg":       "#3B342D",
    "tooltip_text":     "#E8DDD0",

    "btn_start_bg":     "#7A9E65",
    "btn_start_text":   "#E8DDD0",
    "btn_stop_bg":      "#C0614A",
    "btn_stop_text":    "#ffffff",
    "btn_default_bg":   "#453D35",
    "btn_default_hover":"#52483D",
    "btn_default_text": "#E8DDD0",

    "console_bg":       "#231F1A",
    "console_text":     "#E8DDD0",

    "sparkline_cpu":    "212, 148, 76",
    "sparkline_ram":    "192, 97, 74",
    "sparkline_tps":    "122, 158, 101",
}

_LIGHT = {
    "bg":               "#FAF6F0",
    "bg_secondary":     "#F0EBE3",
    "bg_card":          "#FFFFFF",
    "bg_card_hover":    "#F5F2EC",
    "bg_input":         "#FFFFFF",
    "bg_header":        "#F0EBE3",
    "bg_sidebar":       "#EDE8DF",
    "bg_sidebar_hover": "#E5DFD5",
    "bg_sidebar_sel":   "#FFFFFF",
    "border":           "#D4C9B8",
    "border_focus":     "#6B8F5E",
    "text":             "#3D3429",
    "text_secondary":   "#6B5D4F",
    "text_dim":         "#9A8D7F",
    "accent":           "#6B8F5E",
    "accent_hover":     "#5A7A4F",
    "accent_pressed":   "#4A6641",
    "destructive":      "#C0614A",
    "destructive_hover":"#A8523E",
    "success":          "#6B8F5E",
    "warning":          "#C49A3C",
    "info_tag":         "#6B5D4F",
    "warn_tag":         "#C49A3C",
    "error_tag":        "#C0614A",

    "scrollbar":        "#C2B8A8",
    "scrollbar_hover":  "#B0A494",
    "tab_bg":           "#F0EBE3",
    "tab_sel":          "#FAF6F0",
    "tab_hover":        "#E5DFD5",
    "tab_border":       "#D4C9B8",

    "status_running":   "#6B8F5E",
    "status_starting":  "#C49A3C",
    "status_stopped":   "#9A8D7F",

    "tooltip_bg":       "#3D3429",
    "tooltip_text":     "#FAF6F0",

    "btn_start_bg":     "#6B8F5E",
    "btn_start_text":   "#FFFFFF",
    "btn_stop_bg":      "#C0614A",
    "btn_stop_text":    "#ffffff",
    "btn_default_bg":   "#EDE8DF",
    "btn_default_hover":"#E5DFD5",
    "btn_default_text": "#3D3429",

    "console_bg":       "#FFFFFF",
    "console_text":     "#3D3429",

    "sparkline_cpu":    "107, 143, 94",
    "sparkline_ram":    "192, 97, 74",
    "sparkline_tps":    "196, 154, 60",
}



def _build_qss(c: dict) -> str:
    """Build a complete QSS stylesheet from a color token dict."""
    assets_dir = Path(__file__).parent / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    check_svg_path = assets_dir / "check.svg"
    check_svg_path.write_text("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='white'><path d='M9 16.17L4.83 12l-1.42 1.41L9 19 21 7l-1.41-1.41L9 16.17z'/></svg>")
    check_url = check_svg_path.as_posix()

    down_svg_path = assets_dir / "down.svg"
    down_svg_path.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='{c['text_secondary']}'><path d='M7 10l5 5 5-5z'/></svg>")
    down_url = down_svg_path.as_posix()

    up_svg_path = assets_dir / "up.svg"
    up_svg_path.write_text(f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='{c['text_secondary']}'><path d='M7 14l5-5 5 5z'/></svg>")
    up_url = up_svg_path.as_posix()

    return f"""
/* ===== Global ===== */
* {{
    font-family: "JetBrains Mono", "Cascadia Code", "Consolas", monospace;
    font-size: 13px;
    outline: none;
}}

QMainWindow, QDialog, QStackedWidget > QWidget, QScrollArea > QWidget > QWidget {{
    background: {c["bg"]};
}}

QTabWidget::pane {{
    background: {c["bg"]};
    border: none;
    border-top: 1px solid {c["tab_border"]};
}}

QWidget {{
    color: {c["text"]};
}}

/* ===== Scroll bars ===== */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {c["scrollbar"]};
    border-radius: 5px;
    min-height: 32px;
}}
QScrollBar::handle:vertical:hover {{
    background: {c["scrollbar_hover"]};
}}
QScrollBar::add-line:vertical,
QScrollBar::sub-line:vertical,
QScrollBar::add-page:vertical,
QScrollBar::sub-page:vertical {{
    background: transparent;
    height: 0px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {c["scrollbar"]};
    border-radius: 5px;
    min-width: 32px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {c["scrollbar_hover"]};
}}
QScrollBar::add-line:horizontal,
QScrollBar::sub-line:horizontal,
QScrollBar::add-page:horizontal,
QScrollBar::sub-page:horizontal {{
    background: transparent;
    width: 0px;
}}

/* ===== Buttons ===== */
QPushButton {{
    background: {c["btn_default_bg"]};
    color: {c["btn_default_text"]};
    border: none;
    border-radius: 12px;
    padding: 8px 18px;
    font-weight: 700;
    font-size: 14px;
    min-height: 24px;
}}
QPushButton:hover {{
    background: {c["btn_default_hover"]};
}}
QPushButton:pressed {{
    background: {c["border"]};
}}
QPushButton:disabled {{
    background: {c["bg_secondary"]};
    color: {c["text_dim"]};
}}
QPushButton[class="accent"] {{
    background: {c["accent"]};
    color: #ffffff;
}}
QPushButton[class="accent"]:hover {{
    background: {c["accent_hover"]};
}}
QPushButton[class="accent"]:pressed {{
    background: {c["accent_pressed"]};
}}
QPushButton[class="start"] {{
    background: {c["btn_start_bg"]};
    color: {c["btn_start_text"]};
}}
QPushButton[class="start"]:hover {{
    background: {c["success"]};
}}
QPushButton[class="stop"] {{
    background: {c["btn_stop_bg"]};
    color: {c["btn_stop_text"]};
}}
QPushButton[class="stop"]:hover {{
    background: {c["destructive_hover"]};
}}
QPushButton[class="destructive"] {{
    background: {c["destructive"]};
    color: #ffffff;
}}
QPushButton[class="destructive"]:hover {{
    background: {c["destructive_hover"]};
}}
QPushButton[class="flat"] {{
    background: transparent;
    border: none;
    padding: 6px;
    border-radius: 8px;
}}
QPushButton[class="flat"]:hover {{
    background: {c["bg_card_hover"]};
}}

/* ===== Tab widget ===== */
QTabWidget::pane {{
    background: {c["bg"]};
    border: 1px solid {c["tab_border"]};
    border-top: none;
    border-radius: 0 0 16px 16px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: {c["tab_bg"]};
    color: {c["text_secondary"]};
    border: 1px solid {c["tab_border"]};
    border-bottom: none;
    padding: 10px 22px;
    margin-right: -1px;
    font-weight: 700;
    font-size: 14px;
}}
QTabBar::tab:first {{
    border-radius: 12px 0 0 0;
}}
QTabBar::tab:last {{
    border-radius: 0 12px 0 0;
    margin-right: 0;
}}
QTabBar::tab:only-one {{
    border-radius: 12px 12px 0 0;
}}
QTabBar::tab:selected {{
    background: {c["bg"]};
    color: {c["text"]};
    border-bottom: 3px solid {c["accent"]};
}}
QTabBar::tab:hover:!selected {{
    background: {c["tab_hover"]};
    color: {c["text"]};
}}

/* ===== Line edits ===== */
QLineEdit {{
    background: {c["bg_input"]};
    color: {c["text"]};
    border: 2px solid {c["border"]};
    border-radius: 10px;
    padding: 8px 12px;
    selection-background-color: {c["accent"]};
}}
QLineEdit:focus {{
    border-color: {c["border_focus"]};
}}
QLineEdit:disabled {{
    background: {c["bg_secondary"]};
    color: {c["text_dim"]};
}}

/* ===== SpinBox ===== */
QSpinBox {{
    background: {c["bg_input"]};
    color: {c["text"]};
    border: 2px solid {c["border"]};
    border-radius: 10px;
    padding: 6px 10px;
}}
QSpinBox:focus {{
    border-color: {c["border_focus"]};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {c["btn_default_bg"]};
    border: none;
    width: 24px;
    border-radius: 6px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {c["btn_default_hover"]};
}}
QSpinBox::up-arrow {{
    image: url("{up_url}");
    width: 12px;
    height: 12px;
}}
QSpinBox::down-arrow {{
    image: url("{down_url}");
    width: 12px;
    height: 12px;
}}

/* ===== ComboBox ===== */
QComboBox {{
    background: {c["bg_input"]};
    color: {c["text"]};
    border: 2px solid {c["border"]};
    border-radius: 10px;
    padding: 8px 12px;
    min-width: 140px;
    font-weight: 600;
}}
QComboBox:focus {{
    border-color: {c["border_focus"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 28px;
}}
QComboBox::down-arrow {{
    image: url("{down_url}");
    width: 14px;
    height: 14px;
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {c["bg_card"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 10px;
    selection-background-color: {c["accent"]};
    selection-color: #ffffff;
    padding: 6px;
    outline: none;
}}

/* ===== CheckBox ===== */
QCheckBox {{
    spacing: 12px;
    color: {c["text"]};
    font-weight: 600;
}}
QCheckBox::indicator {{
    width: 22px;
    height: 22px;
    border: 2px solid {c["border"]};
    border-radius: 6px;
    background: {c["bg_input"]};
}}
QCheckBox::indicator:checked {{
    background: {c["accent"]};
    border-color: {c["accent"]};
    image: url("{check_url}");
}}
QCheckBox::indicator:hover {{
    border-color: {c["accent"]};
}}

/* ===== GroupBox ===== */
QGroupBox {{
    background: {c["bg_card"]};
    border: 1px solid {c["border"]};
    border-radius: 14px;
    margin-top: 24px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 0 4px;
    font-weight: 700;
    color: {c["text_secondary"]};
    top: -24px;
    left: 8px;
}}

/* ===== List widgets ===== */
QListWidget {{
    background: {c["bg_sidebar"]};
    border: none;
    border-radius: 0;
    outline: none;
}}
QListWidget::item {{
    padding: 12px 16px;
    border: none;
    border-radius: 12px;
    margin: 2px 8px;
    color: {c["text"]};
    font-weight: 700;
}}
QListWidget::item:hover {{
    background: {c["bg_sidebar_hover"]};
}}
QListWidget::item:selected {{
    background: {c["bg_sidebar_sel"]};
    color: {c["text"]};
}}

/* ===== Text editors / Console ===== */
QPlainTextEdit {{
    background: {c["console_bg"]};
    color: {c["console_text"]};
    border: none;
    border-radius: 14px;
    padding: 12px;
    font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
    font-size: 14px;
    selection-background-color: {c["accent"]};
}}
QTextEdit {{
    background: {c["bg_input"]};
    color: {c["text"]};
    border: 2px solid {c["border"]};
    border-radius: 14px;
    padding: 12px;
    selection-background-color: {c["accent"]};
}}

/* ===== Progress bar ===== */
QProgressBar {{
    background: {c["bg_secondary"]};
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    background: {c["accent"]};
    border-radius: 6px;
}}

/* ===== Splitter ===== */
QSplitter::handle {{
    background: {c["border"]};
    width: 1px;
}}

/* ===== Labels ===== */
QLabel {{
    background: transparent;
    color: {c["text"]};
}}
QLabel[class="header"] {{
    font-size: 22px;
    font-weight: 800;
}}
QLabel[class="title"] {{
    font-size: 16px;
    font-weight: 800;
}}
QLabel[class="subtitle"] {{
    font-size: 14px;
    color: {c["text_secondary"]};
    font-weight: 600;
}}
QLabel[class="dim"] {{
    color: {c["text_dim"]};
    font-size: 13px;
}}
QLabel[class="group-title"] {{
    font-size: 15px;
    font-weight: 800;
    color: {c["text_secondary"]};
    padding: 0;
    margin: 0;
}}

/* ===== Material Symbols Text Icon ===== */
QLabel[class="icon"] {{
    font-family: "Material Symbols Rounded";
    font-size: 20px;
    font-weight: 400;
}}

/* ===== Group Box ===== */
QGroupBox {{
    background: {c["bg_card"]};
    border: 2px solid {c["border"]};
    border-radius: 16px;
    margin-top: 10px;
    padding: 20px 18px 18px 18px;
    font-weight: 800;
    font-size: 14px;
    color: {c["text"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 14px;
    color: {c["text_secondary"]};
    font-size: 13px;
    font-weight: 800;
}}

/* ===== Tool tips ===== */
QToolTip {{
    background: {c["tooltip_bg"]};
    color: {c["tooltip_text"]};
    border: 1px solid {c["border"]};
    border-radius: 8px;
    padding: 8px 12px;
    font-size: 13px;
    font-weight: 700;
}}

/* ===== Menu ===== */
QMenu {{
    background: {c["bg_card"]};
    color: {c["text"]};
    border: 2px solid {c["border"]};
    border-radius: 12px;
    padding: 6px;
}}
QMenu::item {{
    padding: 10px 28px;
    border-radius: 8px;
    font-weight: 700;
}}
QMenu::item:selected {{
    background: {c["accent"]};
    color: #ffffff;
}}
QMenu::separator {{
    height: 1px;
    background: {c["border"]};
    margin: 6px 10px;
}}

/* ===== Dialog ===== */
QDialog {{
    background: {c["bg"]};
}}

/* ===== Message Box ===== */
QMessageBox {{
    background: {c["bg"]};
}}

/* ===== Form layout labels ===== */
QFormLayout {{
    border: none;
}}

/* ===== Stacked widget ===== */
QStackedWidget {{
    background: {c["bg"]};
}}

/* ===== Header widget ===== */
QWidget[class="header-bar"] {{
    background: {c["bg_header"]};
    border-bottom: 2px solid {c["border"]};
}}

QTabBar[class="header-tabs"] {{
    border: none;
    outline: none;
}}

QTabBar[class="header-tabs"]::tab {{
    background: transparent;
    border: none;
    border-bottom: 3px solid transparent;
    padding: 2px 16px;
    margin: 8px 4px;
    font-size: 14px;
    font-weight: 700;
    color: {c["text_secondary"]};
}}
QTabBar[class="header-tabs"]::tab:hover {{
    color: {c["text"]};
    background: {c["bg_sidebar_hover"]};
    border-radius: 0;
}}
QTabBar[class="header-tabs"]::tab:selected {{
    color: {c["accent"]};
    border-bottom: 3px solid {c["accent"]};
    background: transparent;
}}

/* ===== Card widget ===== */
QWidget[class="card"] {{
    background: {c["bg_card"]};
    border: 2px solid {c["border"]};
    border-radius: 16px;
}}

/* ===== Sidebar section ===== */
QWidget[class="sidebar"] {{
    background: {c["bg_sidebar"]};
    border-right: 2px solid {c["border"]};
}}

ServerListItemWidget {{
    background: transparent;
}}

ServerListItemWidget QLabel#server_name {{
    font-weight: 700;
    font-size: 14px;
    color: {c["text"]};
}}

ServerListItemWidget QLabel#server_subtitle {{
    font-size: 12px;
    color: {c["text_secondary"]};
}}
"""


def _download_font(url: str, dest: Path) -> None:
    if dest.exists():
        return
    try:
        from urllib.request import Request, urlopen
        req = Request(url, headers={'User-Agent': 'Mozilla/5.0'})
        with urlopen(req, timeout=10) as response, open(dest, 'wb') as out_file:
            out_file.write(response.read())
    except Exception as e:
        print(f"Font download failed for {url}: {e}")


def _load_custom_fonts() -> None:
    fonts_dir = Path(__file__).parent / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)

    jb_reg = fonts_dir / "JetBrainsMono-Regular.ttf"
    jb_bold = fonts_dir / "JetBrainsMono-Bold.ttf"
    material = fonts_dir / "MaterialSymbolsRounded.ttf"
    
    # Load them into Qt immediately if they exist (sync)
    if jb_reg.exists():
        QFontDatabase.addApplicationFont(str(jb_reg))
    if jb_bold.exists():
        QFontDatabase.addApplicationFont(str(jb_bold))
    if material.exists():
        QFontDatabase.addApplicationFont(str(material))

    # URLs
    mat_url = "https://github.com/google/material-design-icons/raw/master/variablefont/MaterialSymbolsRounded%5BFILL%2CGRAD%2Copsz%2Cwght%5D.ttf"
    jb_reg_url = "https://github.com/google/fonts/raw/main/ofl/jetbrainsmono/static/JetBrainsMono-Regular.ttf"
    jb_bold_url = "https://github.com/google/fonts/raw/main/ofl/jetbrainsmono/static/JetBrainsMono-Bold.ttf"

    def worker():
        _download_font(jb_reg_url, jb_reg)
        _download_font(jb_bold_url, jb_bold)
        _download_font(mat_url, material)
        
        # Load again in case they were just downloaded
        if jb_reg.exists():
            QFontDatabase.addApplicationFont(str(jb_reg))
        if jb_bold.exists():
            QFontDatabase.addApplicationFont(str(jb_bold))
        if material.exists():
            QFontDatabase.addApplicationFont(str(material))

    t = threading.Thread(target=worker, daemon=True)
    t.start()


def get_colors(dark: bool = True) -> dict:
    """Return the full color token dictionary for the current mode."""
    return dict(_DARK if dark else _LIGHT)


def get_stylesheet(dark: bool = True) -> str:
    """Return the full QSS for the given mode."""
    c = _DARK if dark else _LIGHT
    return _build_qss(c)

def get_material_icon(name: str, color_hex: str = "#ffffff", size: int = 24) -> "QIcon":
    from PySide6.QtGui import QIcon, QPixmap, QPainter, QFont, QColor
    from PySide6.QtCore import Qt, QRect
    
    _map = {
        "public": "\ue80b",
        "save": "\ue161",
        "extension": "\ue87b",
        "search": "\ue8b6",
        "folder_open": "\ue2c8",
        "settings": "\ue8b8",
        "home": "\ue88a",
        "arrow_forward": "\ue5c8",
        "arrow_back": "\ue5c4",
        "add": "\ue145",
        "delete": "\ue872",
        "refresh": "\ue5d5",
        "play_arrow": "\ue037",
        "stop": "\ue047",
        "restore": "\ue929",
        "edit": "\ue3c9",
        "world": "\ue80b",
        "backup": "\ue161",
        "mod": "\ue87b",
        "modrinth": "\ue8b6",
    }
    symbol = _map.get(name, name)
    
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    font = QFont("Material Symbols Rounded", int(size * 0.7))
    font.setPixelSize(int(size * 0.8))
    painter.setFont(font)
    painter.setPen(QColor(color_hex))
    painter.drawText(QRect(0, 0, size, size), Qt.AlignmentFlag.AlignCenter, symbol)
    painter.end()
    return QIcon(pixmap)

class ThemeManager(QObject):
    """
    Watches the system theme and applies the matching QSS to the QApplication.
    Respects user preferences for theme overrides.
    """

    def __init__(self, app: QApplication, prefs: PreferencesManager):
        super().__init__()
        self._app = app
        self._prefs = prefs
        self._current_dark: Optional[bool] = None
        self._windows = weakref.WeakSet()
        self._timer = QTimer()
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._check)
        
        # Load beautiful rounded fonts
        _load_custom_fonts()

        self.apply()
        self._timer.start()
        
        # Install event filter to catch new windows automatically
        self._app.installEventFilter(self)

    def apply(self):
        theme_pref = self._prefs.theme
        if theme_pref == "light":
            dark = False
        elif theme_pref == "dark":
            dark = True
        else:
            dark = is_system_dark()
            
        if dark == self._current_dark:
            return
            
        self._current_dark = dark
        self._app.setStyleSheet(get_stylesheet(dark))
        self._update_all_window_frames()

    def register_window(self, window: QMainWindow | QDialog):
        """Register a window to have its title bar matched to the theme."""
        self._windows.add(window)
        self._update_window_frame(window)

    def _update_all_window_frames(self):
        for window in self._windows:
            self._update_window_frame(window)

    def _update_window_frame(self, window: QMainWindow | QDialog):
        if not window:
            return
            
        c = self.colors
        # Darker title bar as requested
        bg = QColor(c["bg_header"]).darker(108)
        fg = QColor(c["text"])
        
        apply_window_theme(
            int(window.winId()), 
            self.is_dark,
            caption_color=bg,
            text_color=fg
        )

    @property
    def is_dark(self) -> bool:
        return self._current_dark if self._current_dark is not None else True

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Show:
            if isinstance(obj, (QMainWindow, QDialog)):
                self.register_window(obj)
        return super().eventFilter(obj, event)

    @property
    def colors(self) -> dict:
        return get_colors(self.is_dark)

    def _check(self):
        self.apply()

    def stop(self):
        self._timer.stop()
