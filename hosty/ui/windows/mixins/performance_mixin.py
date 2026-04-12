"""
Performance mixin — CPU, RAM, TPS sparkline charts and process info.
"""

from __future__ import annotations

import math
import re
import time
from typing import Optional

from PySide6.QtCore import Qt, QRectF, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..utils import HAS_PSUTIL, _format_uptime

if HAS_PSUTIL:
    import psutil


class SparklineWidget(QWidget):
    """A sparkline chart widget drawn with QPainter."""

    def __init__(self, color_rgb: tuple = (56, 135, 232), max_points: int = 60, parent=None):
        super().__init__(parent)
        self._data = [0.0] * max_points
        self._max_points = max_points
        self._color = QColor(*color_rgb)
        self.setMinimumHeight(100)
        self.setMaximumHeight(120)

    def add_value(self, value: float):
        self._data.pop(0)
        self._data.append(max(0.0, min(100.0, value)))
        self.update()

    def clear(self):
        self._data = [0.0] * self._max_points
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        w = self.width()
        h = self.height()
        r, g, b = self._color.red(), self._color.green(), self._color.blue()

        # Background with rounded top corners
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(r, g, b, 20))
        painter.drawRoundedRect(0, 0, w, h, 10, 10)

        if w < 2 or h < 2:
            painter.end()
            return

        # Build points
        points = []
        for i, val in enumerate(self._data):
            x = (i / (self._max_points - 1)) * w
            y = h - 2 - (val / 100.0) * (h - 4)
            points.append((x, y))

        if not points:
            painter.end()
            return

        # Filled area
        path = QPainterPath()
        path.moveTo(0, h)
        for x, y in points:
            path.lineTo(x, y)
        path.lineTo(w, h)
        path.closeSubpath()

        grad = QLinearGradient(0, 0, 0, h)
        grad.setColorAt(0, QColor(r, g, b, 80))
        grad.setColorAt(1, QColor(r, g, b, 10))
        painter.setBrush(grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawPath(path)

        # Line stroke
        line_path = QPainterPath()
        line_path.moveTo(points[0][0], points[0][1])
        for x, y in points[1:]:
            line_path.lineTo(x, y)

        pen = QPen(QColor(r, g, b, 220), 2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(line_path)

        painter.end()


class MetricCard(QWidget):
    """A card showing a sparkline and a text value."""

    def __init__(self, title: str, subtitle: str, unit: str, color_rgb: tuple, max_value: float = 100.0, parent=None):
        super().__init__(parent)
        self._unit = unit
        self._max_value = max_value

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Sparkline
        self._sparkline = SparklineWidget(color_rgb)
        layout.addWidget(self._sparkline)

        # Text area
        text_widget = QWidget()
        text_layout = QVBoxLayout(text_widget)
        text_layout.setContentsMargins(14, 10, 14, 12)
        text_layout.setSpacing(2)

        self._subtitle_label = QLabel(subtitle)
        self._subtitle_label.setProperty("class", "dim")
        self._subtitle_label.setStyleSheet("font-size: 12px;")
        text_layout.addWidget(self._subtitle_label)

        self._value_label = QLabel(f"— {unit}")
        self._value_label.setStyleSheet("font-size: 20px; font-weight: 700;")
        text_layout.addWidget(self._value_label)

        layout.addWidget(text_widget)

    def set_max_value(self, max_value: float):
        self._max_value = max_value

    def add_value(self, value: float, text: str):
        norm = (value / self._max_value) * 100 if self._max_value > 0 else 0
        norm = max(0, min(100, norm))
        self._sparkline.add_value(norm)
        self._value_label.setText(f"{text} {self._unit}")

    def reset(self):
        self._sparkline.clear()
        self._value_label.setText(f"— {self._unit}")


class PerformanceMixin:
    """Mixin providing performance monitoring with sparkline charts."""

    def _build_performance_tab(self) -> None:
        tab = QWidget(self._tabs)
        outer = QVBoxLayout(tab)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(tab)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # CPU Card
        cpu_title = QLabel("CPU Usage")
        cpu_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(cpu_title)

        self._cpu_card = MetricCard("CPU", "Total Usage", "%", (56, 135, 232), 100.0)
        layout.addWidget(self._cpu_card)

        # RAM Card
        ram_title = QLabel("Memory Usage")
        ram_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(ram_title)

        self._ram_card = MetricCard("RAM", "Allocated RAM Consumed", "GB", (122, 107, 240), 100.0)
        layout.addWidget(self._ram_card)

        # TPS Card
        tps_title = QLabel("Ticks Per Second")
        tps_title.setStyleSheet("font-size: 14px; font-weight: 700;")
        layout.addWidget(tps_title)

        self._tps_card = MetricCard("TPS", "Server Ticks", "t/s", (247, 165, 36), 20.0)
        layout.addWidget(self._tps_card)

        # Process Info Group
        info_group = QGroupBox("Process Information")
        info_layout = QVBoxLayout(info_group)
        info_layout.setSpacing(8)

        self._perf_pid = self._make_info_row(info_layout, "Process ID", "—")
        self._perf_uptime = self._make_info_row(info_layout, "Uptime", "—")
        self._perf_ram_alloc = self._make_info_row(info_layout, "RAM Allocation", "—")

        layout.addWidget(info_group)
        layout.addStretch()

        scroll.setWidget(content)
        outer.addWidget(scroll)
        self._tabs.addTab(tab, "Performance")

        # TPS tracking
        self._tps_value = 20.0
        self._tps_handler_id = None
        self._psutil_process = None

    def _make_info_row(self, layout, label_text: str, value_text: str) -> QLabel:
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setProperty("class", "dim")
        row.addWidget(label)
        value = QLabel(value_text)
        value.setAlignment(Qt.AlignmentFlag.AlignRight)
        row.addWidget(value)
        layout.addLayout(row)
        return value

    def _on_stats_tick(self) -> None:
        self._refresh_server_rows_status()
        self._refresh_performance()

    def _refresh_performance(self) -> None:
        process = self._selected_process
        if not process or not process.is_running:
            self._cpu_card.reset()
            self._ram_card.reset()
            self._tps_card.reset()
            self._perf_pid.setText("—")
            self._perf_uptime.setText("—")
            self._perf_ram_alloc.setText("—")
            self._psutil_process = None
            return

        pid = process.pid
        if pid:
            self._perf_pid.setText(str(pid))

        max_ram_mb = process.ram_mb
        self._ram_card.set_max_value(max_ram_mb)
        max_ram_gb = max_ram_mb / 1024.0
        if max_ram_gb >= 1.0:
            self._perf_ram_alloc.setText(f"{max_ram_gb:.1f} GB")
        else:
            self._perf_ram_alloc.setText(f"{max_ram_mb} MB")

        if HAS_PSUTIL and pid:
            try:
                if self._psutil_process is None or self._psutil_process.pid != pid:
                    self._psutil_process = psutil.Process(pid)

                # CPU
                raw_cpu = self._psutil_process.cpu_percent(interval=None)
                cpu_count = psutil.cpu_count() or 1
                cpu = raw_cpu / float(cpu_count)
                cpu = max(0.0, min(100.0, cpu))
                self._cpu_card.add_value(cpu, f"{cpu:.1f}")

                # Memory
                mem_info = self._psutil_process.memory_info()
                rss_mb = mem_info.rss / (1024 * 1024)
                rss_gb = rss_mb / 1024.0
                self._ram_card.add_value(rss_mb, f"{rss_gb:.2f}")

                # Uptime
                create_time = self._psutil_process.create_time()
                uptime_secs = time.time() - create_time
                hours = int(uptime_secs // 3600)
                mins = int((uptime_secs % 3600) // 60)
                secs = int(uptime_secs % 60)
                self._perf_uptime.setText(f"{hours}h {mins}m {secs}s")

            except Exception:
                self._psutil_process = None

        # TPS
        self._tps_card.add_value(self._tps_value, f"{self._tps_value:.1f}")

    def _parse_tps(self, text: str) -> None:
        """Parse server output for TPS information."""
        match = re.search(r"Running (\d+)ms behind", text)
        if match:
            behind_ms = int(match.group(1))
            tick_time = 50 + behind_ms / 20
            self._tps_value = min(20.0, 1000.0 / max(1, tick_time))
            return

        match = re.search(r"TPS.*?(\d+\.?\d*)", text)
        if match:
            try:
                self._tps_value = min(20.0, float(match.group(1)))
            except ValueError:
                pass
            return

        if "Done" in text and "For help" in text:
            self._tps_value = 20.0
