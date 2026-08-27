"""
PerformanceView - Server performance monitoring (CPU, RAM, TPS).
"""

import math

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import cairo
from gi.repository import Adw, GLib, Gtk

try:
    import psutil

    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from hosty.shared.backend.server_process import ServerProcess

# Corner radius of libadwaita "card" containers (px)
CARD_RADIUS = 12.0


class SparklineWidget(Gtk.DrawingArea):
    """A small sparkline chart widget rendered natively using Cairo."""

    def __init__(self, color_rgb=(0.22, 0.53, 0.91), max_points=60):
        super().__init__()
        self._data = [0.0] * max_points
        self._max_points = max_points
        self._color = color_rgb
        self.add_css_class("sparkline")
        self.set_draw_func(self._draw_func, None)

    def add_value(self, value):
        self._data.pop(0)
        self._data.append(value)
        self.queue_draw()

    def get_data(self) -> list[float]:
        """Return a copy of the plotted values."""
        return list(self._data)

    def set_data(self, data: list[float]):
        """Replace the plotted values (padded/truncated to max_points)."""
        data = [max(0.0, min(100.0, float(v))) for v in (data or [])]
        padded = [0.0] * (self._max_points - len(data)) + data[-self._max_points :]
        self._data = padded
        self.queue_draw()

    def clear(self):
        self._data = [0.0] * self._max_points
        self.queue_draw()

    def _clip_rounded_top(self, cr, width, height):
        """Clip painting to the card's rounded top corners."""
        r = CARD_RADIUS
        cr.move_to(0, height)
        cr.line_to(0, r)
        cr.arc(r, r, r, math.pi, 1.5 * math.pi)
        cr.line_to(width - r, 0)
        cr.arc(width - r, r, r, 1.5 * math.pi, 2.0 * math.pi)
        cr.line_to(width, height)
        cr.close_path()
        cr.clip()

    def _draw_func(self, area, cr, width, height, user_data):
        r, g, b = self._color

        cr.save()
        self._clip_rounded_top(cr, width, height)

        # 1. Fill background with subtle alpha
        cr.set_source_rgba(r, g, b, 20.0 / 255.0)
        cr.paint()

        denom = max(1, self._max_points - 1)
        points = []
        for i, value in enumerate(self._data):
            x = (i / denom) * (width - 1)
            y = height - 3 - (max(0.0, min(100.0, value)) / 100.0) * (height - 8)
            points.append((x, y))

        if points:
            # 2. Fill the polygon under the line
            cr.move_to(0, height)
            for x, y in points:
                cr.line_to(x, y)
            cr.line_to(width, height)
            cr.close_path()
            cr.set_source_rgba(r, g, b, 58.0 / 255.0)
            cr.fill()

            # 3. Draw the line on top
            cr.move_to(points[0][0], points[0][1])
            for x, y in points[1:]:
                cr.line_to(x, y)
            cr.set_source_rgba(r, g, b, 1.0)
            cr.set_line_width(3.0)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.stroke()

        cr.restore()


class MetricCard(Gtk.Box):
    def __init__(self, title, subtitle_text, unit, color_rgb=(0.22, 0.53, 0.91), max_value=100.0):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        self.set_margin_bottom(16)

        self._unit = unit
        self._max_value = max_value

        # Graph (top half)
        self._sparkline = SparklineWidget(color_rgb)
        self._sparkline.set_size_request(-1, 120)
        self.append(self._sparkline)

        # Value text (bottom half)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        text_box.set_margin_top(12)
        text_box.set_margin_bottom(16)
        text_box.set_margin_start(16)
        text_box.set_margin_end(16)

        self._title_label = Gtk.Label(label=subtitle_text)
        self._title_label.add_css_class("dim-label")
        self._title_label.set_halign(Gtk.Align.START)

        self._value_label = Gtk.Label(label=f"- {unit}")
        self._value_label.add_css_class("title-3")
        self._value_label.set_halign(Gtk.Align.START)

        text_box.append(self._title_label)
        text_box.append(self._value_label)

        self.append(text_box)

    def set_max_value(self, max_value):
        self._max_value = max_value

    def add_value(self, value, text):
        norm = (value / self._max_value) * 100 if self._max_value > 0 else 0
        norm = max(0, min(100, norm))
        self._sparkline.add_value(norm)
        self._value_label.set_label(f"{text} {self._unit}")

    def get_history(self) -> tuple[list[float], str]:
        """Return (plotted values, current label text) for session restore."""
        return self._sparkline.get_data(), self._value_label.get_label()

    def restore(self, history: list[float], text: str):
        """Restore plotted values and label text (e.g. after switching servers)."""
        self._sparkline.set_data(history)
        self._value_label.set_label(text or f"- {self._unit}")

    def reset(self):
        self._sparkline.clear()
        self._value_label.set_label(f"- {self._unit}")


class PerformanceView(Gtk.Box):
    """Server performance monitoring view using native Adwaita aesthetics."""

    # How often (in seconds) to poll the Paper `tps` command via the console
    PAPER_TPS_POLL_SECONDS = 10
    # After this many seconds without a lag event, TPS starts recovering toward 20
    TPS_RECOVER_AFTER_SECONDS = 30
    # TPS added per second during recovery
    TPS_RECOVERY_RATE = 0.5

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._process = None
        self._timer_id = None
        self._psutil_process = None
        self._tps_value = 20.0
        self._tps_handler_id = None
        self._loader_type = ""
        self._tps_poll_counter = 0
        self._tps_last_event = 0.0
        # Per-server metric history so switching servers preserves graphs
        self._server_histories: dict[str, dict] = {}
        self._current_server_id: str | None = None

        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)

        # CPU Metric
        cpu_title = Gtk.Label(label=_("CPU Usage"), xalign=0)
        cpu_title.add_css_class("title-4")
        cpu_title.set_margin_bottom(4)
        content.append(cpu_title)
        self._cpu_card = MetricCard("CPU", _("Total Usage"), "%", (0.22, 0.53, 0.91), 100.0)
        content.append(self._cpu_card)

        # RAM Metric
        ram_title = Gtk.Label(label=_("Memory Usage"), xalign=0)
        ram_title.add_css_class("title-4")
        ram_title.set_margin_bottom(4)
        content.append(ram_title)
        self._ram_card = MetricCard("RAM", _("Allocated RAM Consumed"), "GB", (0.48, 0.42, 0.94), 100.0)
        content.append(self._ram_card)

        # TPS Metric
        tps_title = Gtk.Label(label=_("Ticks Per Second"), xalign=0)
        tps_title.add_css_class("title-4")
        tps_title.set_margin_bottom(4)
        content.append(tps_title)
        self._tps_card = MetricCard("TPS", _("Server Ticks"), "t/s", (0.97, 0.65, 0.14), 20.0)
        content.append(self._tps_card)

        # Process Info group
        self._info_group = Adw.PreferencesGroup(title=_("Process Information"))
        self._pid_row = Adw.ActionRow(title=_("Process ID"), subtitle="-")
        self._pid_row.set_activatable(False)
        self._info_group.add(self._pid_row)

        self._uptime_row = Adw.ActionRow(title=_("Uptime"), subtitle="-")
        self._uptime_row.set_activatable(False)
        self._info_group.add(self._uptime_row)

        self._ram_alloc_row = Adw.ActionRow(title=_("RAM Allocation"), subtitle="-")
        self._ram_alloc_row.set_activatable(False)
        self._info_group.add(self._ram_alloc_row)

        content.append(self._info_group)

        self._scrolled.set_child(content)
        self.append(self._scrolled)
        self.reset()

    def scroll_to_top(self):
        vadj = self._scrolled.get_vadjustment()
        if vadj:
            vadj.set_value(vadj.get_lower())

    def set_process(self, process: ServerProcess, loader_type: str = "", server_id: str = ""):
        """Connect to a server process for monitoring (per-server history preserved)."""
        self._stash_current_history()
        self._current_server_id = server_id or None
        self._process = process
        self._psutil_process = None
        self._loader_type = str(loader_type or "")
        self._tps_poll_counter = 0
        self._restore_history(self._current_server_id)

        if process:
            if self._tps_handler_id:
                try:
                    self._process.disconnect(self._tps_handler_id)
                except Exception:
                    pass
            self._tps_handler_id = process.connect("output-received", self._on_output_for_tps)

            # Setup limits for RAM gauge
            max_ram_mb = process.ram_mb
            self._ram_card.set_max_value(max_ram_mb)

            max_ram_gb = max_ram_mb / 1024.0
            if max_ram_gb >= 1.0:
                self._ram_alloc_row.set_subtitle(f"{max_ram_gb:.1f} GB")
            else:
                self._ram_alloc_row.set_subtitle(f"{max_ram_mb} MB")

    def start_monitoring(self):
        """Start the monitoring timer."""
        self.stop_monitoring()
        self._timer_id = GLib.timeout_add(1000, self._update_stats)
        self._update_stats()

    def stop_monitoring(self):
        """Stop the monitoring timer."""
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _stash_current_history(self) -> None:
        """Save the current server's graphs and TPS state."""
        if not self._current_server_id:
            return
        cpu_hist, cpu_text = self._cpu_card.get_history()
        ram_hist, ram_text = self._ram_card.get_history()
        tps_hist, tps_text = self._tps_card.get_history()
        self._server_histories[self._current_server_id] = {
            "cpu": cpu_hist,
            "cpu_text": cpu_text,
            "ram": ram_hist,
            "ram_text": ram_text,
            "tps": tps_hist,
            "tps_text": tps_text,
            "tps_value": self._tps_value,
            "tps_last_event": self._tps_last_event,
        }

    def _restore_history(self, server_id: str | None) -> None:
        """Load a server's graphs and TPS state (fresh defaults when unknown)."""
        data = self._server_histories.get(server_id) if server_id else None
        if not data:
            self._cpu_card.reset()
            self._ram_card.reset()
            self._tps_card.reset()
            self._tps_value = 20.0
            self._tps_last_event = 0.0
            return
        self._cpu_card.restore(data["cpu"], data["cpu_text"])
        self._ram_card.restore(data["ram"], data["ram_text"])
        self._tps_card.restore(data["tps"], data["tps_text"])
        self._tps_value = float(data.get("tps_value", 20.0))
        self._tps_last_event = float(data.get("tps_last_event", 0.0))

    def reset(self):
        """Reset all stats for the current server to empty state."""
        self._cpu_card.reset()
        self._ram_card.reset()
        self._tps_card.reset()

        self._tps_value = 20.0
        self._tps_poll_counter = 0
        self._tps_last_event = 0.0
        self._psutil_process = None
        if self._current_server_id:
            self._server_histories.pop(self._current_server_id, None)

        self._pid_row.set_subtitle("-")
        self._uptime_row.set_subtitle("-")

    def _update_stats(self) -> bool:
        """Update performance statistics. Returns True to keep timer running."""
        if not self._process or not self._process.is_running:
            self.reset()
            return True

        pid = self._process.pid
        if pid:
            self._pid_row.set_subtitle(str(pid))

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
                import time

                create_time = self._psutil_process.create_time()
                uptime_secs = time.time() - create_time
                hours = int(uptime_secs // 3600)
                mins = int((uptime_secs % 3600) // 60)
                secs = int(uptime_secs % 60)
                self._uptime_row.set_subtitle(_("{}h {}m {}s").format(hours, mins, secs))

            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._psutil_process = None

        # TPS
        self._recover_tps()
        self._poll_paper_tps()
        self._tps_card.add_value(self._tps_value, f"{self._tps_value:.1f}")

        return True

    def _recover_tps(self) -> None:
        """Ease TPS back toward 20 after lag warnings stop (console has no all-clear)."""
        import time

        from hosty.shared.utils.constants import LOADER_PAPER, normalize_loader_type

        if self._tps_value >= 20.0:
            return
        # Paper's polled readings are authoritative; don't fight them
        if normalize_loader_type(self._loader_type) == LOADER_PAPER:
            return
        if time.monotonic() - self._tps_last_event < self.TPS_RECOVER_AFTER_SECONDS:
            return
        self._tps_value = min(20.0, self._tps_value + self.TPS_RECOVERY_RATE)

    def _poll_paper_tps(self) -> None:
        """Periodically ask Paper for real TPS via its console command."""
        from hosty.shared.utils.constants import LOADER_PAPER, ServerStatus, normalize_loader_type

        if not self._process or normalize_loader_type(self._loader_type) != LOADER_PAPER:
            return
        if self._process.status != ServerStatus.RUNNING:
            return

        self._tps_poll_counter += 1
        if self._tps_poll_counter >= self.PAPER_TPS_POLL_SECONDS:
            self._tps_poll_counter = 0
            self._process.send_command("tps")

    def _on_output_for_tps(self, process, text):
        """Parse server output for TPS information."""
        import re
        import time

        # Paper's `tps` command: "TPS from last 1m, 5m, 15m: 20.0, 19.8, 20.0"
        match = re.search(r"TPS from last[^:]*:\s*([\d.]+)", text)
        if match:
            try:
                self._tps_value = min(20.0, float(match.group(1)))
                self._tps_last_event = time.monotonic()
            except ValueError:
                pass
            return

        # Vanilla/fabric/forge lag warning:
        # "Can't keep up! ... Running 5000ms or 100 ticks behind"
        match = re.search(r"Running (\d+)ms(?: or (\d+) ticks)? behind", text)
        if match:
            behind_ms = int(match.group(1))
            ticks = int(match.group(2) or 0) or max(1, behind_ms // 50)
            # Average TPS over the lag window: ticks took (ticks*50 + behind) ms
            measured = 1000.0 * ticks / (ticks * 50.0 + behind_ms)
            self._tps_value = min(20.0, max(0.1, measured))
            self._tps_last_event = time.monotonic()
            return

        # Generic fallback (e.g. spark): require a decimal value so duration
        # suffixes like "1m" are never mistaken for a TPS reading.
        match = re.search(r"\bTPS\b[^0-9]*(\d+\.\d+)", text)
        if match:
            try:
                self._tps_value = min(20.0, float(match.group(1)))
                self._tps_last_event = time.monotonic()
            except ValueError:
                pass
            return

        if "Done" in text and "For help" in text:
            self._tps_value = 20.0
