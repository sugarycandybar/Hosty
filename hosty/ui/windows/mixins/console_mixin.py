"""
Console mixin — server console with syntax-highlighted output and command input.
"""

from __future__ import annotations

import re
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class ConsoleMixin:
    """Mixin providing a monospace console with syntax highlighting."""

    def _build_console_tab(self) -> None:
        tab = QWidget(self._tabs)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Console output
        self._console_output = QPlainTextEdit(tab)
        self._console_output.setReadOnly(True)
        self._console_output.setFont(
            QFont(["Cascadia Code", "JetBrains Mono", "Consolas", "monospace"], 13)
        )
        self._console_output.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        layout.addWidget(self._console_output, 1)

        # Input bar
        input_bar = QWidget(tab)
        input_layout = QHBoxLayout(input_bar)
        input_layout.setContentsMargins(8, 6, 8, 8)
        input_layout.setSpacing(6)

        self._command_input = QLineEdit(input_bar)
        self._command_input.setPlaceholderText("Type a command…")
        self._command_input.setFont(
            QFont(["Cascadia Code", "JetBrains Mono", "Consolas", "monospace"], 13)
        )
        self._command_input.returnPressed.connect(self._send_command)
        input_layout.addWidget(self._command_input, 1)

        send_btn = QPushButton("Send")
        send_btn.clicked.connect(self._send_command)
        input_layout.addWidget(send_btn)

        layout.addWidget(input_bar)
        self._tabs.addTab(tab, "Console")

    def _on_process_output(self, _process, text: str) -> None:
        """Append output with syntax highlighting."""
        line = text.rstrip("\n")
        if not line:
            return

        cursor = self._console_output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        fmt = QTextCharFormat()
        fmt.setFontFamily("Cascadia Code, JetBrains Mono, Consolas, monospace")

        # Determine color based on content
        if line.startswith("[Hosty]"):
            fmt.setForeground(QColor("#7c6bf0"))
            fmt.setFontWeight(QFont.Weight.Bold)
        elif "WARN" in line:
            fmt.setForeground(QColor("#e0af68"))
        elif "ERROR" in line or "Exception" in line:
            fmt.setForeground(QColor("#f7768e"))
        elif "INFO" in line:
            fmt.setForeground(QColor("#7aa2f7"))

        if self._console_output.document().characterCount() > 1:
            cursor.insertText("\n")
        cursor.insertText(line, fmt)

        # Auto-scroll to bottom
        scrollbar = self._console_output.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _send_command(self) -> None:
        text = self._command_input.text().strip()
        if not text:
            return

        if self._selected_process:
            # Echo command
            cursor = self._console_output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#7c6bf0"))
            fmt.setFontWeight(QFont.Weight.Bold)
            if self._console_output.document().characterCount() > 1:
                cursor.insertText("\n")
            cursor.insertText(f"> {text}", fmt)

            scrollbar = self._console_output.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

            self._selected_process.send_command(text)
        else:
            cursor = self._console_output.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            fmt = QTextCharFormat()
            fmt.setForeground(QColor("#f7768e"))
            if self._console_output.document().characterCount() > 1:
                cursor.insertText("\n")
            cursor.insertText("[Hosty] No server process connected", fmt)

        self._command_input.clear()
