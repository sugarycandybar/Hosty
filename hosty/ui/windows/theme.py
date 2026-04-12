"""
Theme system for Hosty Windows UI.

Detects the Windows system theme (light/dark) and provides matching
QSS stylesheets with a modern, polished design.
"""

from __future__ import annotations

import sys
from typing import Optional

from PySide6.QtCore import QTimer
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QApplication


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
        return True  # Default to dark if we can't detect


# ---------------------------------------------------------------------------
# Color tokens
# ---------------------------------------------------------------------------

_DARK = {
    "bg":               "#1a1b26",
    "bg_secondary":     "#1f2133",
    "bg_card":          "#24263a",
    "bg_card_hover":    "#2a2d44",
    "bg_input":         "#1f2133",
    "bg_header":        "#16172a",
    "bg_sidebar":       "#16172a",
    "bg_sidebar_hover": "#1f2133",
    "bg_sidebar_sel":   "#2d3154",
    "border":           "#2f3146",
    "border_focus":     "#7c6bf0",
    "text":             "#e0e2f0",
    "text_secondary":   "#9396b0",
    "text_dim":         "#5e6182",
    "accent":           "#7c6bf0",
    "accent_hover":     "#8e7ff7",
    "accent_pressed":   "#6b5bd9",
    "destructive":      "#e5534b",
    "destructive_hover":"#f07068",
    "success":          "#2ec27e",
    "warning":          "#e5a50a",
    "info_tag":         "#7aa2f7",
    "warn_tag":         "#e0af68",
    "error_tag":        "#f7768e",

    "scrollbar":        "#3a3d56",
    "scrollbar_hover":  "#4d5070",
    "tab_bg":           "#1a1b26",
    "tab_sel":          "#24263a",
    "tab_hover":        "#1f2133",
    "tab_border":       "#2f3146",

    "status_running":   "#2ec27e",
    "status_starting":  "#e5a50a",
    "status_stopped":   "#5e6182",

    "tooltip_bg":       "#2a2d44",
    "tooltip_text":     "#e0e2f0",

    "btn_start_bg":     "#2ec27e",
    "btn_start_text":   "#0e1a13",
    "btn_stop_bg":      "#e5534b",
    "btn_stop_text":    "#ffffff",
    "btn_default_bg":   "#2f3146",
    "btn_default_hover":"#3a3d56",
    "btn_default_text": "#e0e2f0",

    "console_bg":       "#11131e",
    "console_text":     "#c8cad8",

    "sparkline_cpu":    "56, 135, 232",
    "sparkline_ram":    "122, 107, 240",
    "sparkline_tps":    "247, 165, 36",
}

_LIGHT = {
    "bg":               "#f8f9fc",
    "bg_secondary":     "#f0f1f6",
    "bg_card":          "#ffffff",
    "bg_card_hover":    "#f5f6fa",
    "bg_input":         "#ffffff",
    "bg_header":        "#eef0f5",
    "bg_sidebar":       "#eef0f5",
    "bg_sidebar_hover": "#e4e6ee",
    "bg_sidebar_sel":   "#dcdff0",
    "border":           "#d4d7e2",
    "border_focus":     "#7c6bf0",
    "text":             "#1a1c2e",
    "text_secondary":   "#5e6182",
    "text_dim":         "#9396b0",
    "accent":           "#7c6bf0",
    "accent_hover":     "#6b5bd9",
    "accent_pressed":   "#5a4bc2",
    "destructive":      "#d32f2f",
    "destructive_hover":"#c62828",
    "success":          "#1b8c5a",
    "warning":          "#c68a08",
    "info_tag":         "#2563eb",
    "warn_tag":         "#b47d00",
    "error_tag":        "#dc2626",

    "scrollbar":        "#c4c7d6",
    "scrollbar_hover":  "#a8abb8",
    "tab_bg":           "#f0f1f6",
    "tab_sel":          "#ffffff",
    "tab_hover":        "#e8e9f0",
    "tab_border":       "#d4d7e2",

    "status_running":   "#1b8c5a",
    "status_starting":  "#c68a08",
    "status_stopped":   "#b0b3c4",

    "tooltip_bg":       "#1a1c2e",
    "tooltip_text":     "#f8f9fc",

    "btn_start_bg":     "#1b8c5a",
    "btn_start_text":   "#ffffff",
    "btn_stop_bg":      "#d32f2f",
    "btn_stop_text":    "#ffffff",
    "btn_default_bg":   "#e4e6ee",
    "btn_default_hover":"#d4d7e2",
    "btn_default_text": "#1a1c2e",

    "console_bg":       "#fafbff",
    "console_text":     "#1a1c2e",

    "sparkline_cpu":    "37, 99, 235",
    "sparkline_ram":    "122, 107, 240",
    "sparkline_tps":    "202, 138, 8",
}


def _build_qss(c: dict) -> str:
    """Build a complete QSS stylesheet from a color token dict."""
    return f"""
/* ===== Global ===== */
* {{
    font-family: "Segoe UI Variable", "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
    outline: none;
}}

QMainWindow {{
    background: {c["bg"]};
}}

QWidget {{
    color: {c["text"]};
}}

/* ===== Scroll bars ===== */
QScrollBar:vertical {{
    background: transparent;
    width: 8px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {c["scrollbar"]};
    border-radius: 4px;
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
    height: 8px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {c["scrollbar"]};
    border-radius: 4px;
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
    border-radius: 6px;
    padding: 6px 16px;
    font-weight: 600;
    font-size: 13px;
    min-height: 20px;
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
    padding: 4px;
}}
QPushButton[class="flat"]:hover {{
    background: {c["bg_card_hover"]};
    border-radius: 4px;
}}

/* ===== Tab widget ===== */
QTabWidget::pane {{
    background: {c["bg"]};
    border: 1px solid {c["tab_border"]};
    border-top: none;
    border-radius: 0 0 8px 8px;
}}
QTabBar {{
    background: transparent;
}}
QTabBar::tab {{
    background: {c["tab_bg"]};
    color: {c["text_secondary"]};
    border: 1px solid {c["tab_border"]};
    border-bottom: none;
    padding: 8px 18px;
    margin-right: -1px;
    font-weight: 600;
    font-size: 12px;
}}
QTabBar::tab:first {{
    border-radius: 8px 0 0 0;
}}
QTabBar::tab:last {{
    border-radius: 0 8px 0 0;
    margin-right: 0;
}}
QTabBar::tab:only-one {{
    border-radius: 8px 8px 0 0;
}}
QTabBar::tab:selected {{
    background: {c["tab_sel"]};
    color: {c["text"]};
    border-bottom: 2px solid {c["accent"]};
}}
QTabBar::tab:hover:!selected {{
    background: {c["tab_hover"]};
    color: {c["text"]};
}}

/* ===== Line edits ===== */
QLineEdit {{
    background: {c["bg_input"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 6px 10px;
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
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 4px 8px;
}}
QSpinBox:focus {{
    border-color: {c["border_focus"]};
}}
QSpinBox::up-button, QSpinBox::down-button {{
    background: {c["btn_default_bg"]};
    border: none;
    width: 20px;
    border-radius: 3px;
}}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
    background: {c["btn_default_hover"]};
}}

/* ===== ComboBox ===== */
QComboBox {{
    background: {c["bg_input"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 6px 10px;
    min-width: 120px;
}}
QComboBox:focus {{
    border-color: {c["border_focus"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {c["text_secondary"]};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {c["bg_card"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    selection-background-color: {c["accent"]};
    selection-color: #ffffff;
    padding: 4px;
    outline: none;
}}

/* ===== CheckBox ===== */
QCheckBox {{
    spacing: 8px;
    color: {c["text"]};
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border: 2px solid {c["border"]};
    border-radius: 4px;
    background: {c["bg_input"]};
}}
QCheckBox::indicator:checked {{
    background: {c["accent"]};
    border-color: {c["accent"]};
}}
QCheckBox::indicator:hover {{
    border-color: {c["accent"]};
}}

/* ===== List widgets ===== */
QListWidget {{
    background: {c["bg_sidebar"]};
    border: none;
    border-radius: 0;
    outline: none;
}}
QListWidget::item {{
    padding: 10px 14px;
    border: none;
    border-radius: 0;
    color: {c["text"]};
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
    border: 1px solid {c["border"]};
    border-radius: 8px;
    padding: 8px;
    font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
    font-size: 13px;
    selection-background-color: {c["accent"]};
}}
QTextEdit {{
    background: {c["bg_input"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 8px;
    padding: 8px;
    selection-background-color: {c["accent"]};
}}

/* ===== Progress bar ===== */
QProgressBar {{
    background: {c["bg_secondary"]};
    border: none;
    border-radius: 4px;
    height: 8px;
    text-align: center;
    font-size: 0px;
}}
QProgressBar::chunk {{
    background: {c["accent"]};
    border-radius: 4px;
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
    font-size: 18px;
    font-weight: 700;
}}
QLabel[class="title"] {{
    font-size: 15px;
    font-weight: 600;
}}
QLabel[class="subtitle"] {{
    font-size: 12px;
    color: {c["text_secondary"]};
}}
QLabel[class="dim"] {{
    color: {c["text_dim"]};
    font-size: 12px;
}}
QLabel[class="group-title"] {{
    font-size: 13px;
    font-weight: 700;
    color: {c["text_secondary"]};
    padding: 0;
    margin: 0;
}}

/* ===== Group Box ===== */
QGroupBox {{
    background: {c["bg_card"]};
    border: 1px solid {c["border"]};
    border-radius: 10px;
    margin-top: 6px;
    padding: 16px 14px 14px 14px;
    font-weight: 600;
    font-size: 13px;
    color: {c["text"]};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 2px 10px;
    color: {c["text_secondary"]};
    font-size: 12px;
    font-weight: 700;
}}

/* ===== Tool tips ===== */
QToolTip {{
    background: {c["tooltip_bg"]};
    color: {c["tooltip_text"]};
    border: 1px solid {c["border"]};
    border-radius: 6px;
    padding: 6px 10px;
    font-size: 12px;
}}

/* ===== Menu ===== */
QMenu {{
    background: {c["bg_card"]};
    color: {c["text"]};
    border: 1px solid {c["border"]};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    padding: 8px 24px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {c["accent"]};
    color: #ffffff;
}}
QMenu::separator {{
    height: 1px;
    background: {c["border"]};
    margin: 4px 8px;
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
    border-bottom: 1px solid {c["border"]};
}}

/* ===== Card widget ===== */
QWidget[class="card"] {{
    background: {c["bg_card"]};
    border: 1px solid {c["border"]};
    border-radius: 10px;
}}

/* ===== Sidebar section ===== */
QWidget[class="sidebar"] {{
    background: {c["bg_sidebar"]};
    border-right: 1px solid {c["border"]};
}}

/* ===== Status dots (for dynamic painting) ===== */
"""


def get_colors(dark: bool = True) -> dict:
    """Return the full color token dictionary for the current mode."""
    return dict(_DARK if dark else _LIGHT)


def get_stylesheet(dark: bool = True) -> str:
    """Return the full QSS for the given mode."""
    c = _DARK if dark else _LIGHT
    return _build_qss(c)


class ThemeManager:
    """
    Watches the system theme and applies the matching QSS to the QApplication.
    """

    def __init__(self, app: QApplication):
        self._app = app
        self._current_dark: Optional[bool] = None
        self._timer = QTimer()
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self._check)
        self.apply()
        self._timer.start()

    def apply(self):
        dark = is_system_dark()
        if dark == self._current_dark:
            return
        self._current_dark = dark
        self._app.setStyleSheet(get_stylesheet(dark))

    @property
    def is_dark(self) -> bool:
        return self._current_dark if self._current_dark is not None else True

    @property
    def colors(self) -> dict:
        return get_colors(self.is_dark)

    def _check(self):
        self.apply()

    def stop(self):
        self._timer.stop()
