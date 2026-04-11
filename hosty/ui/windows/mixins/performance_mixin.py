"""PySide6-based Windows frontend for Hosty."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    import psutil

    HAS_PSUTIL = True
except Exception:
    HAS_PSUTIL = False

from hosty.backend.config_manager import ConfigManager
from hosty.backend.server_manager import ServerInfo, ServerManager
from hosty.core.events import set_main_thread_dispatcher
from hosty.utils.constants import (
    DEFAULT_RAM_MB,
    DEFAULT_SERVER_PROPERTIES,
    MAX_RAM_MB,
    MIN_RAM_MB,
    ServerStatus,
    get_required_java_version,
)



from ..utils import *
from ..dialogs.create_server import CreateServerDialog

class PerformanceMixin:
    def _build_performance_tab(self) -> None:
        tab = QWidget(self._tabs)
        outer = QVBoxLayout(tab)
        layout = QFormLayout()

        self._perf_status = QLabel("Stopped", tab)
        self._perf_pid = QLabel("-", tab)
        self._perf_uptime = QLabel("-", tab)
        self._perf_cpu = QLabel("-", tab)
        self._perf_ram = QLabel("-", tab)
        self._perf_cpu_bar = QProgressBar(tab)
        self._perf_cpu_bar.setRange(0, 100)
        self._perf_cpu_bar.setValue(0)
        self._perf_ram_bar = QProgressBar(tab)
        self._perf_ram_bar.setRange(0, 100)
        self._perf_ram_bar.setValue(0)

        layout.addRow("Status", self._perf_status)
        layout.addRow("PID", self._perf_pid)
        layout.addRow("Uptime", self._perf_uptime)
        layout.addRow("CPU", self._perf_cpu)
        layout.addRow("CPU Load", self._perf_cpu_bar)
        layout.addRow("RAM", self._perf_ram)
        layout.addRow("RAM Usage", self._perf_ram_bar)

        outer.addLayout(layout)
        outer.addStretch(1)

        self._tabs.addTab(tab, "Performance")

    def _on_stats_tick(self) -> None:
        self._refresh_server_rows_status()
        self._refresh_performance()

    def _refresh_performance(self) -> None:
        process = self._selected_process
        if not process:
            self._perf_status.setText("Stopped")
            self._perf_pid.setText("-")
            self._perf_uptime.setText("-")
            self._perf_cpu.setText("-")
            self._perf_ram.setText("-")
            self._perf_cpu_bar.setValue(0)
            self._perf_ram_bar.setValue(0)
            return

        status = process.status
        self._perf_status.setText(status.capitalize())

        pid = process.pid
        self._perf_pid.setText(str(pid) if pid else "-")

        if process.is_running and self._process_start_ts is not None:
            self._perf_uptime.setText(_format_uptime(time.time() - self._process_start_ts))
        else:
            self._perf_uptime.setText("-")

        if HAS_PSUTIL and pid and process.is_running:
            try:
                p = psutil.Process(pid)
                cpu = p.cpu_percent(interval=None)
                ram_mb = p.memory_info().rss / (1024 * 1024)
                ram_pct = min(100.0, (ram_mb / max(1.0, float(process.ram_mb))) * 100.0)
                self._perf_cpu.setText(f"{cpu:.1f}%")
                self._perf_ram.setText(f"{ram_mb:.1f} MB")
                self._perf_cpu_bar.setValue(int(max(0.0, min(100.0, cpu))))
                self._perf_ram_bar.setValue(int(ram_pct))
                return
            except Exception:
                pass

        self._perf_cpu.setText("N/A")
        self._perf_ram.setText("N/A")
        self._perf_cpu_bar.setValue(0)
        self._perf_ram_bar.setValue(0)


