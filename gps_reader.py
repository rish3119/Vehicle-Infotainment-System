# =============================================================================
# gps_reader.py — GPS NMEA Driver (NEO-6M / NEO-8M / PA1616D)
# Interface: UART (GPIO14 TX → GPS RX, GPIO15 RX → GPS TX)
#
# Wiring (u-blox NEO-6M breakout → Raspberry Pi header):
#   VCC  → Pin 1  (3.3V)     ← some modules also accept 5V on Pin 2
#   GND  → Pin 9  (GND)
#   TX   → Pin 10 (GPIO15 / RXD)
#   RX   → Pin 8  (GPIO14 / TXD)
#
# Before use:
#   sudo raspi-config → Interface Options → Serial Port
#     "Login shell over serial?" → No
#     "Serial port hardware enabled?" → Yes
#   sudo reboot
# =============================================================================

from __future__ import annotations

import io
import logging
import math
import threading
import time
from dataclasses import dataclass
from typing import Optional

import pynmea2
import serial

from config import GPS_PORT, GPS_BAUD, GPS_TIMEOUT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# GPS snapshot
# ---------------------------------------------------------------------------

@dataclass
class GPSData:
    latitude:   float = 0.0      # decimal degrees (+ = N, – = S)
    longitude:  float = 0.0      # decimal degrees (+ = E, – = W)
    altitude_m: float = 0.0      # metres above MSL
    speed_knots:float = 0.0      # knots (from RMC sentence)
    speed_kmh:  float = 0.0      # converted for display
    heading:    float = 0.0      # degrees true (from RMC)
    num_sats:   int   = 0        # satellites in use
    fix_quality:int   = 0        # 0=no fix, 1=GPS, 2=DGPS
    timestamp:  str   = ""       # UTC HH:MM:SS from NMEA
    valid:      bool  = False    # True when fix is active

    def to_dict(self) -> dict:
        return {
            "latitude":    self.latitude,
            "longitude":   self.longitude,
            "altitude_m":  self.altitude_m,
            "speed_kmh":   self.speed_kmh,
            "heading":     self.heading,
            "num_sats":    self.num_sats,
            "fix_quality": self.fix_quality,
            "timestamp":   self.timestamp,
            "valid":       self.valid,
        }


# ---------------------------------------------------------------------------
# GPS reader (background thread)
# ---------------------------------------------------------------------------

class GPSReader:
    """
    Reads NMEA-0183 sentences from a serial GPS module.
    Parses GGA (position/quality) and RMC (speed/heading) sentences.
    Runs in a daemon thread; call get_data() for the latest snapshot.

    Usage:
        gps = GPSReader()
        gps.start()
        data = gps.get_data()
        gps.stop()
    """

    FALLBACK_PORTS = ["/dev/ttyUSB0", "/dev/ttyACM0", "/dev/serial0"]

    def __init__(self):
        self._data    = GPSData()
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._ser:    Optional[serial.Serial]    = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Open the serial port and start the reader thread."""
        port = self._open_port()
        if port is None:
            log.error("Cannot open any GPS serial port")
            return False
        self._ser     = port
        self._running = True
        self._thread  = threading.Thread(target=self._read_loop, name="GPSReader", daemon=True)
        self._thread.start()
        log.info("GPS reader started on %s @ %d", self._ser.port, GPS_BAUD)
        return True

    def stop(self) -> None:
        """Stop the reader thread and close the serial port."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._ser and self._ser.is_open:
            self._ser.close()
        log.info("GPS reader stopped")

    def get_data(self) -> GPSData:
        """Return a thread-safe copy of the latest GPS snapshot."""
        with self._lock:
            import copy
            return copy.copy(self._data)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _open_port(self) -> Optional[serial.Serial]:
        candidates = [GPS_PORT] + [p for p in self.FALLBACK_PORTS if p != GPS_PORT]
        for port in candidates:
            try:
                ser = serial.Serial(port, GPS_BAUD, timeout=GPS_TIMEOUT)
                log.info("Opened GPS port: %s", port)
                return ser
            except serial.SerialException as exc:
                log.debug("Cannot open %s: %s", port, exc)
        return None

    def _read_loop(self) -> None:
        sio = io.TextIOWrapper(
            io.BufferedRWPair(self._ser, self._ser),
            encoding="ascii",
            errors="replace",
        )
        while self._running:
            try:
                line = sio.readline().strip()
                if not line.startswith("$"):
                    continue
                self._parse_sentence(line)
            except serial.SerialException as exc:
                log.error("GPS serial error: %s", exc)
                time.sleep(1.0)
            except UnicodeDecodeError:
                pass  # skip garbled line

    def _parse_sentence(self, line: str) -> None:
        try:
            msg = pynmea2.parse(line)
        except pynmea2.ParseError:
            return

        with self._lock:
            # GGA — fix quality, position, altitude, satellite count
            if isinstance(msg, pynmea2.GGA):
                self._data.fix_quality = int(msg.gps_qual) if msg.gps_qual else 0
                self._data.num_sats    = int(msg.num_sats)  if msg.num_sats  else 0
                self._data.valid       = self._data.fix_quality > 0
                if self._data.valid:
                    self._data.latitude   = msg.latitude
                    self._data.longitude  = msg.longitude
                    self._data.altitude_m = float(msg.altitude) if msg.altitude else 0.0
                if msg.timestamp:
                    self._data.timestamp = str(msg.timestamp)

            # RMC — speed over ground, true heading, date/time
            elif isinstance(msg, pynmea2.RMC):
                if msg.status == "A":           # A = valid, V = void
                    self._data.latitude    = msg.latitude
                    self._data.longitude   = msg.longitude
                    self._data.speed_knots = float(msg.spd_over_grnd) if msg.spd_over_grnd else 0.0
                    self._data.speed_kmh   = self._data.speed_knots * 1.852
                    self._data.heading     = float(msg.true_course)   if msg.true_course   else 0.0
                    self._data.valid       = True
                else:
                    self._data.valid = False


# ---------------------------------------------------------------------------
# Standalone test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    gps = GPSReader()
    if gps.start():
        try:
            while True:
                d = gps.get_data()
                print(f"Fix:{d.fix_quality} Sats:{d.num_sats} "
                      f"Lat:{d.latitude:.6f} Lon:{d.longitude:.6f} "
                      f"Speed:{d.speed_kmh:.1f} km/h Hdg:{d.heading:.1f}°")
                time.sleep(1.0)
        except KeyboardInterrupt:
            gps.stop()
