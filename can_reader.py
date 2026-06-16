# =============================================================================
# can_reader.py — CAN Bus Driver & OBD-II PID Decoder
# Hardware: MCP2515 CAN controller module (SPI) + TJA1050 transceiver
# Protocol: ISO 15765-4 (CAN 11-bit, 500 kbps) — universal OBD-II HS-CAN
#
# Wiring (MCP2515 breakout → Raspberry Pi header):
#   VCC  → Pin 2  (5V)
#   GND  → Pin 6  (GND)
#   SCK  → Pin 23 (GPIO11 / SPI CLK)
#   SI   → Pin 19 (GPIO10 / SPI MOSI)
#   SO   → Pin 21 (GPIO9  / SPI MISO)
#   CS   → Pin 24 (GPIO8  / SPI CE0)
#   INT  → Pin 22 (GPIO25 / input interrupt)
#
# /boot/config.txt:
#   dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
#   dtoverlay=spi-bcm2835
#
# Bring up interface (once per boot, or add to /etc/network/interfaces):
#   sudo ip link set can0 up type can bitrate 500000
# =============================================================================

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional

import can

from config import (
    CAN_CHANNEL, CAN_BITRATE, CAN_INTERFACE, CAN_TIMEOUT, CAN_POLL_INTERVAL,
    OBD_REQUEST_ID, OBD_REPLY_BASE,
    PID_ENGINE_RPM, PID_VEHICLE_SPEED, PID_COOLANT_TEMP, PID_INTAKE_TEMP,
    PID_THROTTLE, PID_FUEL_LEVEL, PID_ENGINE_LOAD, PID_BATTERY_VOLT, PID_DTC_COUNT,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Vehicle data snapshot
# ---------------------------------------------------------------------------

@dataclass
class VehicleData:
    speed_kmh:       float = 0.0      # km/h
    rpm:             float = 0.0      # rev/min
    coolant_temp_c:  float = 0.0      # °C
    intake_temp_c:   float = 0.0      # °C
    throttle_pct:    float = 0.0      # 0–100 %
    fuel_level_pct:  float = 0.0      # 0–100 %
    engine_load_pct: float = 0.0      # 0–100 %
    battery_volt:    float = 0.0      # V (from OBD-II APCM)
    dtc_count:       int   = 0        # active fault codes
    mil_on:          bool  = False    # Check-Engine lamp
    raw_frames:      Dict  = field(default_factory=dict)  # {arb_id: bytes}

    def to_dict(self) -> dict:
        return {
            "speed_kmh":       self.speed_kmh,
            "rpm":             self.rpm,
            "coolant_temp_c":  self.coolant_temp_c,
            "intake_temp_c":   self.intake_temp_c,
            "throttle_pct":    self.throttle_pct,
            "fuel_level_pct":  self.fuel_level_pct,
            "engine_load_pct": self.engine_load_pct,
            "battery_volt":    self.battery_volt,
            "dtc_count":       self.dtc_count,
            "mil_on":          self.mil_on,
        }


# ---------------------------------------------------------------------------
# OBD-II decoder helpers
# ---------------------------------------------------------------------------

def _decode_pid(pid: int, data: bytes) -> Optional[float]:
    """
    Decode Mode 01 PID response bytes A/B/C/D (data[3:]).
    Returns the physical value or None on parse error.
    References: SAE J1979 Table A-3.
    """
    try:
        A = data[3]
        B = data[4] if len(data) > 4 else 0
        if pid == PID_ENGINE_RPM:       return ((A * 256) + B) / 4.0      # rpm
        if pid == PID_VEHICLE_SPEED:    return float(A)                    # km/h
        if pid == PID_COOLANT_TEMP:     return float(A) - 40.0            # °C
        if pid == PID_INTAKE_TEMP:      return float(A) - 40.0            # °C
        if pid == PID_THROTTLE:         return (A / 255.0) * 100.0        # %
        if pid == PID_FUEL_LEVEL:       return (A / 255.0) * 100.0        # %
        if pid == PID_ENGINE_LOAD:      return (A / 255.0) * 100.0        # %
        if pid == PID_BATTERY_VOLT:     return ((A * 256) + B) / 1000.0   # V
        if pid == PID_DTC_COUNT:        return float(A & 0x7F)            # count
    except (IndexError, TypeError) as exc:
        log.debug("PID 0x%02X decode error: %s", pid, exc)
    return None


# ---------------------------------------------------------------------------
# CAN bus reader (background thread)
# ---------------------------------------------------------------------------

class CANReader:
    """
    Polls OBD-II Mode 01 PIDs over the MCP2515 CAN interface.
    Runs an ISO-TP / single-frame (SF) exchange for each PID at ~10 Hz.
    Also sniffs all CAN frames for custom proprietary data.

    Usage:
        reader = CANReader()
        reader.start()
        data = reader.get_data()
        reader.stop()
    """

    POLL_PIDS = [
        PID_ENGINE_RPM,
        PID_VEHICLE_SPEED,
        PID_COOLANT_TEMP,
        PID_INTAKE_TEMP,
        PID_THROTTLE,
        PID_FUEL_LEVEL,
        PID_ENGINE_LOAD,
        PID_BATTERY_VOLT,
        PID_DTC_COUNT,
    ]

    def __init__(self, on_update: Optional[Callable[[VehicleData], None]] = None):
        self._data    = VehicleData()
        self._lock    = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._bus:    Optional[can.Bus] = None
        self._on_update = on_update      # optional callback on every refresh

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Open the SocketCAN bus and start the polling thread."""
        try:
            self._bus = can.Bus(
                interface=CAN_INTERFACE,
                channel=CAN_CHANNEL,
                bitrate=CAN_BITRATE,
            )
            log.info("CAN bus opened: %s @ %d bps", CAN_CHANNEL, CAN_BITRATE)
        except can.CanError as exc:
            log.error("Failed to open CAN bus: %s", exc)
            return False

        self._running = True
        self._thread  = threading.Thread(target=self._poll_loop, name="CANReader", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the polling thread and close the bus."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        if self._bus:
            self._bus.shutdown()
            self._bus = None
        log.info("CAN bus closed")

    def get_data(self) -> VehicleData:
        """Return a thread-safe snapshot of the latest vehicle data."""
        with self._lock:
            # Return a shallow copy to avoid race conditions in the UI
            import copy
            return copy.copy(self._data)

    # ------------------------------------------------------------------
    # Internal polling loop (runs in background thread)
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        while self._running:
            for pid in self.POLL_PIDS:
                if not self._running:
                    break
                value = self._request_pid(pid)
                if value is not None:
                    self._apply_pid(pid, value)
            # Fire the optional UI callback
            if self._on_update:
                try:
                    self._on_update(self.get_data())
                except Exception as exc:         # pragma: no cover
                    log.warning("on_update callback error: %s", exc)
            time.sleep(CAN_POLL_INTERVAL)

    def _request_pid(self, pid: int) -> Optional[float]:
        """Send a Mode 01 PID request and decode the response."""
        if self._bus is None:
            return None

        # Build ISO-TP single-frame (SF) request: [0x02, 0x01, PID, 0x55…]
        request = can.Message(
            arbitration_id=OBD_REQUEST_ID,
            data=[0x02, 0x01, pid, 0x55, 0x55, 0x55, 0x55, 0x55],
            is_extended_id=False,
        )
        try:
            self._bus.send(request)
        except can.CanError as exc:
            log.debug("CAN send error (PID 0x%02X): %s", pid, exc)
            return None

        # Wait for the matching ECU response
        deadline = time.monotonic() + CAN_TIMEOUT
        while time.monotonic() < deadline:
            msg = self._bus.recv(timeout=CAN_TIMEOUT)
            if msg is None:
                break
            # Store every frame for raw sniffer access
            with self._lock:
                self._data.raw_frames[msg.arbitration_id] = bytes(msg.data)
            # Match Mode 01 positive response (0x41 = 0x40 | 0x01)
            if (OBD_REPLY_BASE <= msg.arbitration_id <= OBD_REPLY_BASE + 7
                    and len(msg.data) >= 4
                    and msg.data[1] == 0x41
                    and msg.data[2] == pid):
                return _decode_pid(pid, bytes(msg.data))

        log.debug("No response for PID 0x%02X (timeout)", pid)
        return None

    def _apply_pid(self, pid: int, value: float) -> None:
        with self._lock:
            if pid == PID_ENGINE_RPM:       self._data.rpm             = value
            elif pid == PID_VEHICLE_SPEED:  self._data.speed_kmh       = value
            elif pid == PID_COOLANT_TEMP:   self._data.coolant_temp_c  = value
            elif pid == PID_INTAKE_TEMP:    self._data.intake_temp_c   = value
            elif pid == PID_THROTTLE:       self._data.throttle_pct    = value
            elif pid == PID_FUEL_LEVEL:     self._data.fuel_level_pct  = value
            elif pid == PID_ENGINE_LOAD:    self._data.engine_load_pct = value
            elif pid == PID_BATTERY_VOLT:   self._data.battery_volt    = value
            elif pid == PID_DTC_COUNT:
                self._data.dtc_count = int(value)
                # MIL bit is bit 7 of the first status byte (from PID 0x01 raw frame)
                raw = self._data.raw_frames.get(OBD_REPLY_BASE, b"\x00" * 6)
                self._data.mil_on = bool(raw[3] & 0x80) if len(raw) > 3 else False
