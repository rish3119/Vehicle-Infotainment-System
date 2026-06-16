# =============================================================================
# dashboard.py — PyQt5 Fullscreen Infotainment Dashboard
# Layout: tabbed UI — Drive | Media | Navigation | Settings
#
# Targets: 7" 800×480 RPi touchscreen  OR  1024×600 HDMI panel
# Run headless: export DISPLAY=:0 before launching main.py
# =============================================================================

from __future__ import annotations

import logging
from typing import Optional

from PyQt5.QtCore    import Qt, QTimer, QThread, pyqtSignal, QObject
from PyQt5.QtGui     import QColor, QFont, QPainter, QPen, QBrush, QPolygon, QIcon
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QStackedWidget,
    QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QSlider, QProgressBar, QListWidget, QListWidgetItem,
    QFrame, QSizePolicy, QTabWidget, QLineEdit, QDialog,
)
from PyQt5.QtCore import QPoint

from config import (
    DISPLAY_WIDTH, DISPLAY_HEIGHT, DISPLAY_FPS,
    COLOUR_BG, COLOUR_PANEL, COLOUR_ACCENT,
    COLOUR_WARNING, COLOUR_DANGER, COLOUR_TEXT, COLOUR_TEXT_DIM,
)
from can_reader       import VehicleData
from gps_reader       import GPSData
from audio_manager    import AudioManager, AudioSource
from connectivity_manager import ConnectivityManager, BTDevice

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Global stylesheet (dark automotive theme)
# ---------------------------------------------------------------------------

STYLESHEET = f"""
QMainWindow, QWidget {{
    background-color: {COLOUR_BG};
    color: {COLOUR_TEXT};
    font-family: "Segoe UI", "Helvetica Neue", Arial, sans-serif;
}}
QLabel {{
    color: {COLOUR_TEXT};
}}
QPushButton {{
    background-color: {COLOUR_PANEL};
    color: {COLOUR_TEXT};
    border: 1px solid {COLOUR_ACCENT};
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 14px;
}}
QPushButton:pressed {{
    background-color: {COLOUR_ACCENT};
    color: #000000;
}}
QPushButton:disabled {{
    color: {COLOUR_TEXT_DIM};
    border-color: {COLOUR_TEXT_DIM};
}}
QSlider::groove:horizontal {{
    height: 6px;
    background: {COLOUR_PANEL};
    border-radius: 3px;
}}
QSlider::handle:horizontal {{
    background: {COLOUR_ACCENT};
    width: 18px;
    height: 18px;
    margin: -6px 0;
    border-radius: 9px;
}}
QSlider::sub-page:horizontal {{
    background: {COLOUR_ACCENT};
    border-radius: 3px;
}}
QListWidget {{
    background-color: {COLOUR_PANEL};
    border: 1px solid {COLOUR_ACCENT};
    border-radius: 4px;
    font-size: 13px;
}}
QListWidget::item:selected {{
    background-color: {COLOUR_ACCENT};
    color: #000000;
}}
QProgressBar {{
    background-color: {COLOUR_PANEL};
    border: 1px solid #333333;
    border-radius: 4px;
    height: 10px;
    text-align: center;
}}
QProgressBar::chunk {{
    background-color: {COLOUR_ACCENT};
    border-radius: 4px;
}}
QFrame[frameShape="4"], QFrame[frameShape="5"] {{
    color: #333333;
}}
QTabWidget::pane {{
    border: 1px solid #333333;
}}
QTabBar::tab {{
    background: {COLOUR_PANEL};
    color: {COLOUR_TEXT_DIM};
    padding: 10px 20px;
    border: none;
    font-size: 14px;
}}
QTabBar::tab:selected {{
    color: {COLOUR_ACCENT};
    border-bottom: 2px solid {COLOUR_ACCENT};
}}
"""


# ---------------------------------------------------------------------------
# Reusable widget helpers
# ---------------------------------------------------------------------------

def _label(text: str, size: int = 13, bold: bool = False, colour: str = COLOUR_TEXT) -> QLabel:
    lbl = QLabel(text)
    font = QFont()
    font.setPointSize(size)
    font.setBold(bold)
    lbl.setFont(font)
    lbl.setStyleSheet(f"color: {colour};")
    return lbl


def _separator() -> QFrame:
    line = QFrame()
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


# ---------------------------------------------------------------------------
# Gauge widget — circular analogue-style dial
# ---------------------------------------------------------------------------

class GaugeWidget(QWidget):
    """
    Draws a simple arc-style gauge (0–max) with a needle and value label.
    Used for speedometer and RPM tachometer.
    """

    def __init__(
        self,
        label: str,
        max_value: float,
        unit: str,
        warn_threshold: float = 0.75,
        danger_threshold: float = 0.90,
        parent=None,
    ):
        super().__init__(parent)
        self._label     = label
        self._max       = max_value
        self._unit      = unit
        self._warn_t    = warn_threshold
        self._danger_t  = danger_threshold
        self._value     = 0.0
        self.setMinimumSize(160, 160)

    def set_value(self, value: float) -> None:
        self._value = max(0.0, min(self._max, value))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h   = self.width(), self.height()
        cx, cy = w // 2, h // 2
        radius = min(w, h) // 2 - 10

        # Background arc (270° sweep from 225° to 315°)
        START_ANGLE  = 225   # degrees (Qt uses 16ths)
        SWEEP        = 270
        rect_size    = radius * 2
        rx = cx - radius
        ry = cy - radius

        # Draw background arc
        pen = QPen(QColor("#2a2a2a"), 12, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rx, ry, rect_size, rect_size,
                        START_ANGLE * 16, -SWEEP * 16)

        # Determine colour based on level
        ratio = self._value / self._max if self._max else 0
        if ratio >= self._danger_t:
            arc_colour = QColor(COLOUR_DANGER)
        elif ratio >= self._warn_t:
            arc_colour = QColor(COLOUR_WARNING)
        else:
            arc_colour = QColor(COLOUR_ACCENT)

        # Draw value arc
        pen = QPen(arc_colour, 12, Qt.SolidLine, Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(rx, ry, rect_size, rect_size,
                        START_ANGLE * 16, -int(ratio * SWEEP) * 16)

        # Value text
        painter.setPen(QColor(COLOUR_TEXT))
        font = QFont()
        font.setPointSize(18)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(0, cy - 20, w, 40, Qt.AlignCenter,
                         f"{int(self._value)}")

        # Unit text
        font.setPointSize(9)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(COLOUR_TEXT_DIM))
        painter.drawText(0, cy + 10, w, 20, Qt.AlignCenter, self._unit)

        # Label text
        font.setPointSize(10)
        painter.setFont(font)
        painter.drawText(0, cy + 30, w, 20, Qt.AlignCenter, self._label)

        painter.end()


# ---------------------------------------------------------------------------
# Drive tab — speedometer, RPM, engine data
# ---------------------------------------------------------------------------

class DriveTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        # Main gauges row
        self._speed_gauge = GaugeWidget("SPEED", 260, "km/h", 0.70, 0.90)
        self._rpm_gauge   = GaugeWidget("RPM",  8000, "rpm",  0.75, 0.90)
        self._speed_gauge.setMinimumSize(200, 200)
        self._rpm_gauge.setMinimumSize(200, 200)

        gauges_row = QHBoxLayout()
        gauges_row.addStretch()
        gauges_row.addWidget(self._speed_gauge)
        gauges_row.addSpacing(30)
        gauges_row.addWidget(self._rpm_gauge)
        gauges_row.addStretch()

        # Secondary data grid
        self._lbl_coolant  = _label("--°C",  20, True, COLOUR_ACCENT)
        self._lbl_fuel     = _label("-- %",  20, True, COLOUR_ACCENT)
        self._lbl_load     = _label("-- %",  20, True, COLOUR_ACCENT)
        self._lbl_intake   = _label("--°C",  20, True, COLOUR_ACCENT)
        self._lbl_battery  = _label("-- V",  20, True, COLOUR_ACCENT)
        self._lbl_mil      = _label("MIL OFF", 14, False, "#00cc44")

        def _cell(title: str, value_lbl: QLabel) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(_label(title, 10, colour=COLOUR_TEXT_DIM), alignment=Qt.AlignCenter)
            col.addWidget(value_lbl, alignment=Qt.AlignCenter)
            return col

        data_row = QHBoxLayout()
        data_row.addLayout(_cell("COOLANT",  self._lbl_coolant))
        data_row.addLayout(_cell("FUEL",     self._lbl_fuel))
        data_row.addLayout(_cell("LOAD",     self._lbl_load))
        data_row.addLayout(_cell("INTAKE",   self._lbl_intake))
        data_row.addLayout(_cell("BATTERY",  self._lbl_battery))
        data_row.addWidget(self._lbl_mil)

        # Fuel bar
        fuel_row = QHBoxLayout()
        fuel_row.addWidget(_label("FUEL LEVEL", 10, colour=COLOUR_TEXT_DIM))
        self._fuel_bar = QProgressBar()
        self._fuel_bar.setRange(0, 100)
        self._fuel_bar.setValue(0)
        self._fuel_bar.setFixedHeight(12)
        fuel_row.addWidget(self._fuel_bar)

        # DTC warning
        self._lbl_dtc = _label("", 11, colour=COLOUR_WARNING)
        self._lbl_dtc.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)
        layout.addLayout(gauges_row)
        layout.addLayout(data_row)
        layout.addLayout(fuel_row)
        layout.addWidget(self._lbl_dtc)

    # ------------------------------------------------------------------

    def update_vehicle(self, data: VehicleData) -> None:
        self._speed_gauge.set_value(data.speed_kmh)
        self._rpm_gauge.set_value(data.rpm)
        self._lbl_coolant.setText(f"{data.coolant_temp_c:.0f}°C")
        self._lbl_fuel.setText(f"{data.fuel_level_pct:.0f}%")
        self._lbl_load.setText(f"{data.engine_load_pct:.0f}%")
        self._lbl_intake.setText(f"{data.intake_temp_c:.0f}°C")
        self._lbl_battery.setText(f"{data.battery_volt:.1f}V")
        self._fuel_bar.setValue(int(data.fuel_level_pct))

        # Coolant warning colour
        if data.coolant_temp_c >= 105:
            self._lbl_coolant.setStyleSheet(f"color: {COLOUR_DANGER};")
        elif data.coolant_temp_c >= 95:
            self._lbl_coolant.setStyleSheet(f"color: {COLOUR_WARNING};")
        else:
            self._lbl_coolant.setStyleSheet(f"color: {COLOUR_ACCENT};")

        # MIL / DTC
        if data.mil_on:
            self._lbl_mil.setText(f"CHECK ENGINE  ({data.dtc_count} DTC)")
            self._lbl_mil.setStyleSheet(f"color: {COLOUR_WARNING};")
            self._lbl_dtc.setText("Fault detected — visit a workshop")
        else:
            self._lbl_mil.setText("MIL OFF")
            self._lbl_mil.setStyleSheet("color: #00cc44;")
            self._lbl_dtc.setText("")


# ---------------------------------------------------------------------------
# Media tab
# ---------------------------------------------------------------------------

class MediaTab(QWidget):
    def __init__(self, audio: AudioManager, parent=None):
        super().__init__(parent)
        self._audio = audio
        self._build_ui()

    def _build_ui(self):
        # Source selector
        src_row = QHBoxLayout()
        for src, label in [
            (AudioSource.MEDIA, "USB/SD"),
            (AudioSource.BLUETOOTH, "Bluetooth"),
            (AudioSource.AUX, "AUX"),
        ]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, s=src: self._set_source(s))
            src_row.addWidget(btn)
        self._src_btns = src_row

        # Now playing
        self._lbl_track = _label("No track", 16, True)
        self._lbl_track.setAlignment(Qt.AlignCenter)

        # Playback controls
        self._btn_prev  = QPushButton("⏮")
        self._btn_play  = QPushButton("▶")
        self._btn_next  = QPushButton("⏭")
        for b in (self._btn_prev, self._btn_play, self._btn_next):
            b.setFixedSize(56, 56)

        self._btn_prev.clicked.connect(self._audio.prev_track)
        self._btn_play.clicked.connect(self._toggle_play)
        self._btn_next.clicked.connect(self._audio.next_track)

        ctrl_row = QHBoxLayout()
        ctrl_row.addStretch()
        ctrl_row.addWidget(self._btn_prev)
        ctrl_row.addWidget(self._btn_play)
        ctrl_row.addWidget(self._btn_next)
        ctrl_row.addStretch()

        # Volume slider
        self._vol_slider = QSlider(Qt.Horizontal)
        self._vol_slider.setRange(0, 100)
        self._vol_slider.setValue(self._audio.volume)
        self._vol_slider.valueChanged.connect(self._audio.set_volume)
        self._lbl_vol = _label(f"Vol: {self._audio.volume}", 11)

        vol_row = QHBoxLayout()
        vol_row.addWidget(_label("🔈", 14))
        vol_row.addWidget(self._vol_slider)
        vol_row.addWidget(_label("🔊", 14))
        vol_row.addSpacing(10)
        vol_row.addWidget(self._lbl_vol)

        btn_mute = QPushButton("Mute")
        btn_mute.clicked.connect(self._toggle_mute)

        # BT device label
        self._lbl_bt = _label("Bluetooth: not connected", 11, colour=COLOUR_TEXT_DIM)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(src_row)
        layout.addWidget(_separator())
        layout.addWidget(self._lbl_track)
        layout.addLayout(ctrl_row)
        layout.addWidget(_separator())
        layout.addLayout(vol_row)
        layout.addWidget(btn_mute, alignment=Qt.AlignCenter)
        layout.addWidget(self._lbl_bt)
        layout.addStretch()

    # ------------------------------------------------------------------

    def _set_source(self, source: str) -> None:
        self._audio.set_source(source)

    def _toggle_play(self) -> None:
        if self._audio.is_playing:
            self._audio.pause()
            self._btn_play.setText("▶")
        else:
            if self._audio.source == AudioSource.MEDIA:
                self._audio.resume() if not self._audio.is_playing else None
                self._audio.play()
            self._btn_play.setText("⏸")

    def _toggle_mute(self) -> None:
        muted = self._audio.toggle_mute()
        self._vol_slider.setEnabled(not muted)

    def update_audio(self) -> None:
        self._lbl_track.setText(self._audio.current_track)
        self._vol_slider.setValue(self._audio.volume)
        self._lbl_vol.setText(f"Vol: {self._audio.volume}")
        if self._audio.bt_connected:
            self._lbl_bt.setText(f"Bluetooth: {self._audio.bt_device}")
            self._lbl_bt.setStyleSheet(f"color: {COLOUR_ACCENT};")
        else:
            self._lbl_bt.setText("Bluetooth: not connected")
            self._lbl_bt.setStyleSheet(f"color: {COLOUR_TEXT_DIM};")


# ---------------------------------------------------------------------------
# Navigation tab
# ---------------------------------------------------------------------------

class NavigationTab(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self):
        # GPS status banner
        self._lbl_fix = _label("GPS: No fix", 13, colour=COLOUR_WARNING)
        self._lbl_fix.setAlignment(Qt.AlignCenter)

        # Coordinates
        grid = QGridLayout()
        grid.setSpacing(8)
        self._lbl_lat    = _label("--", 14, True, COLOUR_ACCENT)
        self._lbl_lon    = _label("--", 14, True, COLOUR_ACCENT)
        self._lbl_alt    = _label("--", 14, True, COLOUR_ACCENT)
        self._lbl_spd    = _label("--", 14, True, COLOUR_ACCENT)
        self._lbl_hdg    = _label("--", 14, True, COLOUR_ACCENT)
        self._lbl_sats   = _label("--", 14, True, COLOUR_ACCENT)

        for row, (title, val) in enumerate([
            ("Latitude",    self._lbl_lat),
            ("Longitude",   self._lbl_lon),
            ("Altitude (m)", self._lbl_alt),
            ("Speed (km/h)",self._lbl_spd),
            ("Heading (°)", self._lbl_hdg),
            ("Satellites",  self._lbl_sats),
        ]):
            grid.addWidget(_label(title, 11, colour=COLOUR_TEXT_DIM), row, 0, Qt.AlignRight)
            grid.addWidget(val, row, 1, Qt.AlignLeft)

        # Compass rose (simple text-based)
        self._lbl_compass = _label("N", 36, True, COLOUR_ACCENT)
        self._lbl_compass.setAlignment(Qt.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addWidget(self._lbl_fix)
        layout.addWidget(_separator())
        layout.addLayout(grid)
        layout.addWidget(_separator())
        layout.addWidget(_label("Heading", 11, colour=COLOUR_TEXT_DIM), alignment=Qt.AlignCenter)
        layout.addWidget(self._lbl_compass)
        layout.addStretch()

    # ------------------------------------------------------------------

    def update_gps(self, data: GPSData) -> None:
        if data.valid:
            self._lbl_fix.setText(f"GPS: Fix (Q={data.fix_quality}, {data.num_sats} sats)")
            self._lbl_fix.setStyleSheet(f"color: #00cc44;")
        else:
            self._lbl_fix.setText("GPS: Searching…")
            self._lbl_fix.setStyleSheet(f"color: {COLOUR_WARNING};")

        self._lbl_lat.setText(f"{data.latitude:.6f}°")
        self._lbl_lon.setText(f"{data.longitude:.6f}°")
        self._lbl_alt.setText(f"{data.altitude_m:.1f} m")
        self._lbl_spd.setText(f"{data.speed_kmh:.1f}")
        self._lbl_hdg.setText(f"{data.heading:.1f}°")
        self._lbl_sats.setText(str(data.num_sats))

        # Simple 8-point compass from heading
        points = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        idx = int((data.heading + 22.5) / 45.0) % 8
        self._lbl_compass.setText(points[idx])


# ---------------------------------------------------------------------------
# Settings tab
# ---------------------------------------------------------------------------

class SettingsTab(QWidget):
    def __init__(self, connectivity: ConnectivityManager, parent=None):
        super().__init__(parent)
        self._conn = connectivity
        self._build_ui()

    def _build_ui(self):
        # WiFi section
        wifi_title = _label("WiFi", 14, True, COLOUR_ACCENT)
        self._lbl_wifi = _label("SSID: --   IP: --", 12)
        btn_wifi_scan  = QPushButton("Scan Networks")
        btn_wifi_scan.clicked.connect(self._scan_wifi)
        self._wifi_list = QListWidget()
        self._wifi_list.setMaximumHeight(120)

        # Bluetooth section
        bt_title = _label("Bluetooth", 14, True, COLOUR_ACCENT)
        self._lbl_bt_status = _label("Not connected", 12, colour=COLOUR_TEXT_DIM)
        btn_bt_scan   = QPushButton("Scan Devices")
        btn_bt_scan.clicked.connect(self._scan_bt)
        btn_bt_pair   = QPushButton("Pair Selected")
        btn_bt_pair.clicked.connect(self._pair_bt)
        self._bt_list = QListWidget()
        self._bt_list.setMaximumHeight(120)

        # System section
        sys_title = _label("System", 14, True, COLOUR_ACCENT)
        btn_reboot = QPushButton("Reboot")
        btn_reboot.clicked.connect(self._reboot)
        btn_shutdown = QPushButton("Shutdown")
        btn_shutdown.clicked.connect(self._shutdown)
        sys_row = QHBoxLayout()
        sys_row.addWidget(btn_reboot)
        sys_row.addWidget(btn_shutdown)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(10)
        layout.addWidget(wifi_title)
        layout.addWidget(self._lbl_wifi)
        layout.addWidget(btn_wifi_scan)
        layout.addWidget(self._wifi_list)
        layout.addWidget(_separator())
        layout.addWidget(bt_title)
        layout.addWidget(self._lbl_bt_status)
        layout.addWidget(btn_bt_scan)
        layout.addWidget(btn_bt_pair)
        layout.addWidget(self._bt_list)
        layout.addWidget(_separator())
        layout.addWidget(sys_title)
        layout.addLayout(sys_row)
        layout.addStretch()

    # ------------------------------------------------------------------

    def refresh_connectivity(self) -> None:
        ssid = self._conn.wifi_ssid
        ip   = self._conn.ip_address
        self._lbl_wifi.setText(
            f"SSID: {ssid or '--'}   IP: {ip or '--'}"
        )
        dev = self._conn.bt_connected_device
        if dev:
            self._lbl_bt_status.setText(f"Connected: {dev.name}")
            self._lbl_bt_status.setStyleSheet(f"color: {COLOUR_ACCENT};")
        else:
            self._lbl_bt_status.setText("Not connected")
            self._lbl_bt_status.setStyleSheet(f"color: {COLOUR_TEXT_DIM};")

    def _scan_wifi(self) -> None:
        self._wifi_list.clear()
        networks = self._conn.wifi.scan()
        for net in networks:
            icon = "🔒 " if net.secured else "   "
            item = QListWidgetItem(f"{icon}{net.ssid}  ({net.signal}%)")
            if net.connected:
                item.setForeground(QColor(COLOUR_ACCENT))
            self._wifi_list.addItem(item)

    def _scan_bt(self) -> None:
        self._bt_list.clear()
        self._bt_list.addItem(QListWidgetItem("Scanning…"))
        devices = self._conn.bluetooth.scan()
        self._bt_list.clear()
        for dev in devices:
            self._bt_list.addItem(QListWidgetItem(str(dev)))

    def _pair_bt(self) -> None:
        item = self._bt_list.currentItem()
        if item is None:
            return
        # Address is in brackets in the item text: "Name [AA:BB:CC:DD:EE:FF]"
        text = item.text()
        start = text.find("[")
        end   = text.find("]")
        if start >= 0 and end > start:
            address = text[start + 1:end]
            if self._conn.bluetooth.pair(address):
                self._conn.bluetooth.trust(address)
                self._conn.bluetooth.connect(address)

    def _reboot(self) -> None:
        import subprocess
        subprocess.run(["sudo", "reboot"])

    def _shutdown(self) -> None:
        import subprocess
        subprocess.run(["sudo", "shutdown", "-h", "now"])


# ---------------------------------------------------------------------------
# Status bar (always visible at the bottom)
# ---------------------------------------------------------------------------

class StatusBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(32)
        self.setStyleSheet(f"background-color: {COLOUR_PANEL};")

        self._lbl_speed  = _label("0 km/h", 10)
        self._lbl_gps    = _label("GPS: --",  10, colour=COLOUR_TEXT_DIM)
        self._lbl_bt     = _label("BT: --",   10, colour=COLOUR_TEXT_DIM)
        self._lbl_wifi   = _label("WiFi: --", 10, colour=COLOUR_TEXT_DIM)
        self._lbl_time   = _label("00:00",    10)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 0, 10, 0)
        row.setSpacing(20)
        row.addWidget(self._lbl_speed)
        row.addWidget(self._lbl_gps)
        row.addWidget(self._lbl_bt)
        row.addStretch()
        row.addWidget(self._lbl_wifi)
        row.addWidget(self._lbl_time)

    def update_status(
        self,
        speed: float,
        gps: GPSData,
        bt_name: Optional[str],
        wifi_ssid: Optional[str],
    ) -> None:
        from datetime import datetime
        self._lbl_speed.setText(f"{int(speed)} km/h")
        self._lbl_gps.setText(f"GPS: {'Fix' if gps.valid else 'No fix'}")
        self._lbl_bt.setText(f"BT: {bt_name or '--'}")
        self._lbl_wifi.setText(f"WiFi: {wifi_ssid or '--'}")
        self._lbl_time.setText(datetime.now().strftime("%H:%M"))


# ---------------------------------------------------------------------------
# Main Window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(
        self,
        audio:        AudioManager,
        connectivity: ConnectivityManager,
    ):
        super().__init__()
        self._audio        = audio
        self._connectivity = connectivity

        # Latest data snapshots (updated by the update() timer)
        self._vehicle_data = VehicleData()
        self._gps_data     = GPSData()

        self._build_ui()
        self._start_refresh_timer()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.setWindowTitle("Infotainment")
        self.setStyleSheet(STYLESHEET)
        self.showFullScreen()

        # Tabs
        self._tab_drive = DriveTab()
        self._tab_media = MediaTab(self._audio)
        self._tab_nav   = NavigationTab()
        self._tab_set   = SettingsTab(self._connectivity)

        tabs = QTabWidget()
        tabs.addTab(self._tab_drive, "Drive")
        tabs.addTab(self._tab_media, "Media")
        tabs.addTab(self._tab_nav,   "Navigation")
        tabs.addTab(self._tab_set,   "Settings")

        # Status bar
        self._status_bar = StatusBar()

        # Root layout
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(tabs)
        root_layout.addWidget(self._status_bar)

        self.setCentralWidget(root)

    # ------------------------------------------------------------------
    # Data injection (called by main.py)
    # ------------------------------------------------------------------

    def set_vehicle_data(self, data: VehicleData) -> None:
        self._vehicle_data = data

    def set_gps_data(self, data: GPSData) -> None:
        self._gps_data = data

    # ------------------------------------------------------------------
    # Periodic UI refresh
    # ------------------------------------------------------------------

    def _start_refresh_timer(self) -> None:
        self._timer = QTimer(self)
        self._timer.setInterval(1000 // DISPLAY_FPS)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

    def _refresh(self) -> None:
        vd = self._vehicle_data
        gd = self._gps_data

        self._tab_drive.update_vehicle(vd)
        self._tab_media.update_audio()
        self._tab_nav.update_gps(gd)
        self._tab_set.refresh_connectivity()

        bt_dev = self._connectivity.bt_connected_device
        self._status_bar.update_status(
            speed    = vd.speed_kmh,
            gps      = gd,
            bt_name  = bt_dev.name if bt_dev else None,
            wifi_ssid= self._connectivity.wifi_ssid,
        )

        # Sync BT audio state
        if bt_dev and not self._audio.bt_connected:
            self._audio.set_bt_connected(bt_dev.name)
        elif not bt_dev and self._audio.bt_connected:
            self._audio.set_bt_connected(None)

    def keyPressEvent(self, event) -> None:
        """Allow Escape to exit fullscreen during development."""
        if event.key() == Qt.Key_Escape:
            self.close()
