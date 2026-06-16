#!/usr/bin/env python3
# =============================================================================
# main.py — Vehicle Infotainment System — Application Entry Point
# Raspberry Pi 3/4/5  |  Python 3.9+
#
# Usage:
#   sudo python3 src/main.py                    # normal run (needs CAN + GPS hw)
#   sudo python3 src/main.py --demo             # demo mode (simulated sensor data)
#   sudo python3 src/main.py --no-can           # skip CAN bus (GPS + UI only)
#   sudo python3 src/main.py --no-gps           # skip GPS (CAN + UI only)
#
# Boot autostart (systemd):
#   sudo cp infotainment.service /etc/systemd/system/
#   sudo systemctl enable infotainment
#   sudo systemctl start  infotainment
# =============================================================================

import argparse
import logging
import logging.handlers
import math
import os
import signal
import sys
import threading
import time
from typing import Optional

# Ensure src/ is on the Python path when run from project root
sys.path.insert(0, os.path.dirname(__file__))

from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore    import QTimer

from config import LOG_LEVEL, LOG_FILE, LOG_MAX_BYTES, LOG_BACKUP_COUNT
from can_reader           import CANReader, VehicleData
from gps_reader           import GPSReader, GPSData
from audio_manager        import AudioManager
from connectivity_manager import ConnectivityManager
from dashboard            import MainWindow


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else getattr(logging, LOG_LEVEL, logging.INFO)
    fmt   = "%(asctime)s  %(levelname)-8s  %(name)-24s  %(message)s"

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Console handler
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter(fmt))
    root_logger.addHandler(ch)

    # Rotating file handler (skip on permission error)
    try:
        fh = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT
        )
        fh.setLevel(level)
        fh.setFormatter(logging.Formatter(fmt))
        root_logger.addHandler(fh)
    except (PermissionError, OSError) as exc:
        logging.warning("Cannot write to log file %s: %s", LOG_FILE, exc)


log = logging.getLogger("main")


# ---------------------------------------------------------------------------
# Demo-mode sensor simulator
# ---------------------------------------------------------------------------

class DemoSensor:
    """
    Generates synthetic CAN + GPS data so the dashboard can be tested
    without physical hardware.  Values follow a gentle sine-wave pattern
    to simulate a driving scenario.
    """

    def __init__(self):
        self._t = 0.0

    def tick(self, dt: float = 0.1) -> None:
        self._t += dt

    def vehicle_data(self) -> VehicleData:
        t = self._t
        vd = VehicleData()
        vd.speed_kmh       = 80 + 40 * math.sin(t / 10.0)
        vd.rpm             = 2500 + 1000 * math.sin(t / 7.0)
        vd.coolant_temp_c  = 88 + 5 * math.sin(t / 20.0)
        vd.intake_temp_c   = 30 + 3 * math.sin(t / 15.0)
        vd.throttle_pct    = 40 + 20 * math.sin(t / 5.0)
        vd.fuel_level_pct  = max(10.0, 75 - (t / 600.0) * 100)
        vd.engine_load_pct = 50 + 20 * math.sin(t / 8.0)
        vd.battery_volt    = 13.8 + 0.3 * math.sin(t / 3.0)
        vd.dtc_count       = 0
        vd.mil_on          = False
        return vd

    def gps_data(self) -> GPSData:
        t = self._t
        gd = GPSData()
        gd.valid       = True
        gd.fix_quality = 1
        gd.num_sats    = 9
        gd.latitude    = 51.5074 + 0.001 * math.sin(t / 30.0)
        gd.longitude   = -0.1278 + 0.001 * math.cos(t / 30.0)
        gd.altitude_m  = 35.0
        gd.speed_kmh   = 80 + 40 * math.sin(t / 10.0)
        gd.heading     = (t * 3.0) % 360
        gd.timestamp   = time.strftime("%H:%M:%S")
        return gd


# ---------------------------------------------------------------------------
# Application class
# ---------------------------------------------------------------------------

class InfotainmentApp:
    """
    Top-level application.  Owns all subsystems and the Qt main window.
    """

    def __init__(self, args: argparse.Namespace):
        self._args     = args
        self._running  = False

        # Subsystems
        self._can:    Optional[CANReader]          = None
        self._gps:    Optional[GPSReader]          = None
        self._audio   = AudioManager()
        self._conn    = ConnectivityManager()
        self._demo:   Optional[DemoSensor]         = None

        # Qt application + main window
        self._qt_app  = QApplication(sys.argv)
        self._window: Optional[MainWindow] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> int:
        """Initialise all subsystems and enter the Qt event loop."""
        log.info("=" * 60)
        log.info("Vehicle Infotainment System — starting up")
        log.info("=" * 60)

        # Demo mode
        if self._args.demo:
            log.info("DEMO MODE — using simulated sensor data")
            self._demo = DemoSensor()

        # CAN bus
        if not self._args.no_can and not self._args.demo:
            self._can = CANReader()
            if not self._can.start():
                log.warning("CAN bus failed to start — vehicle data unavailable")
                self._can = None

        # GPS
        if not self._args.no_gps and not self._args.demo:
            self._gps = GPSReader()
            if not self._gps.start():
                log.warning("GPS failed to start — navigation unavailable")
                self._gps = None

        # Audio
        log.info("Audio manager ready (default volume %d)", self._audio.volume)

        # Connectivity
        self._conn.start()

        # Load media from /media/usb if present
        usb_path = "/media/usb"
        if os.path.isdir(usb_path):
            count = self._audio.load_media(usb_path)
            log.info("Loaded %d tracks from %s", count, usb_path)

        # Build the main window
        self._window = MainWindow(
            audio        = self._audio,
            connectivity = self._conn,
        )

        # Connect BT audio callback
        self._conn.bluetooth.on_connected    = self._on_bt_connected
        self._conn.bluetooth.on_disconnected = self._on_bt_disconnected

        # Start the sensor polling timer (feeds data into the UI)
        self._poll_timer = QTimer()
        self._poll_timer.setInterval(100)     # 10 Hz
        self._poll_timer.timeout.connect(self._poll_sensors)
        self._poll_timer.start()

        # Install SIGINT handler so Ctrl+C works cleanly
        signal.signal(signal.SIGINT, self._handle_signal)

        self._running = True
        log.info("UI ready — entering event loop")
        ret = self._qt_app.exec_()
        self.stop()
        return ret

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        log.info("Shutting down…")
        if self._can:  self._can.stop()
        if self._gps:  self._gps.stop()
        self._conn.stop()
        log.info("Shutdown complete")

    # ------------------------------------------------------------------
    # Sensor polling (called at 10 Hz by QTimer)
    # ------------------------------------------------------------------

    def _poll_sensors(self) -> None:
        if self._window is None:
            return

        if self._demo:
            self._demo.tick(0.1)
            vd = self._demo.vehicle_data()
            gd = self._demo.gps_data()
        else:
            vd = self._can.get_data() if self._can else VehicleData()
            gd = self._gps.get_data() if self._gps else GPSData()

        self._window.set_vehicle_data(vd)
        self._window.set_gps_data(gd)

        # Log a brief summary every 5 s
        if not hasattr(self, "_last_log") or time.monotonic() - self._last_log > 5.0:
            self._last_log = time.monotonic()
            log.debug(
                "Speed=%.0f km/h  RPM=%.0f  Coolant=%.0f°C  "
                "Fuel=%.0f%%  GPS=%s  Sats=%d",
                vd.speed_kmh, vd.rpm, vd.coolant_temp_c,
                vd.fuel_level_pct,
                "OK" if gd.valid else "no fix",
                gd.num_sats,
            )

    # ------------------------------------------------------------------
    # Bluetooth callbacks
    # ------------------------------------------------------------------

    def _on_bt_connected(self, device) -> None:
        log.info("BT audio connected: %s", device.name)
        self._audio.set_bt_connected(device.name)

    def _on_bt_disconnected(self, address: str) -> None:
        log.info("BT audio disconnected: %s", address)
        self._audio.set_bt_connected(None)

    # ------------------------------------------------------------------
    # Signal handler
    # ------------------------------------------------------------------

    def _handle_signal(self, signum, frame) -> None:
        log.info("Caught signal %d — quitting", signum)
        self._qt_app.quit()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Vehicle Infotainment System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--demo",    action="store_true", help="Run with simulated sensor data")
    parser.add_argument("--no-can", action="store_true", help="Disable CAN bus reader")
    parser.add_argument("--no-gps", action="store_true", help="Disable GPS reader")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _setup_logging(verbose=args.verbose)
    app  = InfotainmentApp(args)
    return app.start()


if __name__ == "__main__":
    sys.exit(main())
