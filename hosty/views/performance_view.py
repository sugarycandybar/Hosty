"""
PerformanceView - Server performance monitoring (CPU, RAM, TPS).
"""
import math
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib, Gdk

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from hosty.backend.server_process import ServerProcess


class SparklineWidget(Gtk.DrawingArea):
    """A small sparkline chart widget drawn with Cairo."""
    
    def __init__(self, color_rgb=(0.22, 0.53, 0.91), max_points=60):
        super().__init__()
        self._data = [0.0] * max_points
        self._max_points = max_points
        self._color = color_rgb
        # Used to give it a slightly darker distinct background like in GNOME system monitor
        self.add_css_class("view") 
        self.set_draw_func(self._draw)
    
    def add_value(self, value):
        self._data.pop(0)
        self._data.append(value)
        self.queue_draw()
    
    def clear(self):
        self._data = [0.0] * self._max_points
        self.queue_draw()
        
    def _draw(self, area, cr, width, height):
        r, g, b = self._color
        
        # Clip top corners to match Adwaita card styling
        radius = 12
        cr.new_path()
        cr.move_to(0, radius)
        cr.arc(radius, radius, radius, math.pi, 1.5 * math.pi)
        cr.line_to(width - radius, 0)
        cr.arc(width - radius, radius, radius, 1.5 * math.pi, 2 * math.pi)
        cr.line_to(width, height)
        cr.line_to(0, height)
        cr.close_path()
        cr.clip()
        
        # Tinted background matching the metric's specific color
        cr.set_source_rgba(r, g, b, 0.08)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        
        # Filled area under the line
        cr.set_source_rgba(r, g, b, 0.25)
        cr.move_to(0, height)
        
        for i, val in enumerate(self._data):
            x = (i / (self._max_points - 1)) * width
            y = height - 1.5 - (val / 100.0) * (height * 0.95 - 2)
            cr.line_to(x, y)
            
        cr.line_to(width, height)
        cr.close_path()
        cr.fill()
        
        # The line stroke (always visible even at 0)
        cr.set_source_rgba(r, g, b, 1.0)
        cr.set_line_width(2)
        cr.move_to(0, height - 1.5 - (self._data[0] / 100.0) * (height * 0.95 - 2))
        
        for i, val in enumerate(self._data):
            x = (i / (self._max_points - 1)) * width
            y = height - 1.5 - (val / 100.0) * (height * 0.95 - 2)
            cr.line_to(x, y)
            
        cr.stroke()


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
        
        self._value_label = Gtk.Label(label=f"— {unit}")
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
        
    def reset(self):
        self._sparkline.clear()
        self._value_label.set_label(f"— {self._unit}")


class PerformanceView(Gtk.Box):
    """Server performance monitoring view using native Adwaita aesthetics."""
    
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self._process = None
        self._timer_id = None
        self._psutil_process = None
        self._tps_value = 20.0
        self._tps_handler_id = None
        
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        content.set_margin_top(24)
        content.set_margin_bottom(24)
        content.set_margin_start(24)
        content.set_margin_end(24)
        
        # CPU Metric
        cpu_title = Gtk.Label(label="CPU Usage", xalign=0)
        cpu_title.add_css_class("title-4")
        cpu_title.set_margin_bottom(4)
        content.append(cpu_title)
        self._cpu_card = MetricCard("CPU", "Total Usage", "%", (0.22, 0.53, 0.91), 100.0)
        content.append(self._cpu_card)
        
        # RAM Metric
        ram_title = Gtk.Label(label="Memory Usage", xalign=0)
        ram_title.add_css_class("title-4")
        ram_title.set_margin_bottom(4)
        content.append(ram_title)
        self._ram_card = MetricCard("RAM", "Allocated RAM Consumed", "GB", (0.48, 0.42, 0.94), 100.0)
        content.append(self._ram_card)
        
        # TPS Metric
        tps_title = Gtk.Label(label="Ticks Per Second", xalign=0)
        tps_title.add_css_class("title-4")
        tps_title.set_margin_bottom(4)
        content.append(tps_title)
        self._tps_card = MetricCard("TPS", "Server Ticks", "t/s", (0.97, 0.65, 0.14), 20.0)
        content.append(self._tps_card)
        
        # Process Info group
        self._info_group = Adw.PreferencesGroup(title="Process Information")
        self._pid_row = Adw.ActionRow(title="Process ID", subtitle="—")
        self._pid_row.set_activatable(False)
        self._info_group.add(self._pid_row)
        
        self._uptime_row = Adw.ActionRow(title="Uptime", subtitle="—")
        self._uptime_row.set_activatable(False)
        self._info_group.add(self._uptime_row)
        
        self._ram_alloc_row = Adw.ActionRow(title="RAM Allocation", subtitle="—")
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
    
    def set_process(self, process: ServerProcess):
        """Connect to a server process for monitoring."""
        self._process = process
        self._psutil_process = None
        
        if process:
            if self._tps_handler_id:
                try:
                     self._process.disconnect(self._tps_handler_id)
                except Exception:
                     pass
            self._tps_handler_id = process.connect(
                'output-received', self._on_output_for_tps
            )
            
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
    
    def reset(self):
        """Reset all stats to empty state."""
        self._cpu_card.reset()
        self._ram_card.reset()
        self._tps_card.reset()
        
        self._pid_row.set_subtitle("—")
        self._uptime_row.set_subtitle("—")
        self._psutil_process = None
    
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
                max_ram_mb = self._process.ram_mb
                
                self._ram_card.add_value(rss_mb, f"{rss_gb:.2f}")
                
                # Uptime
                import time
                create_time = self._psutil_process.create_time()
                uptime_secs = time.time() - create_time
                hours = int(uptime_secs // 3600)
                mins = int((uptime_secs % 3600) // 60)
                secs = int(uptime_secs % 60)
                self._uptime_row.set_subtitle(f"{hours}h {mins}m {secs}s")
                
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self._psutil_process = None
        
        # TPS
        self._tps_card.add_value(self._tps_value, f"{self._tps_value:.1f}")
        
        return True
    
    def _on_output_for_tps(self, process, text):
        """Parse server output for TPS information."""
        import re
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
