# =============================================================================
# connectivity_manager.py — Bluetooth & WiFi Manager
# Interfaces: BlueZ (bluetoothctl via pydbus), NetworkManager / wpa_supplicant
#
# System dependencies:
#   sudo apt-get install bluez bluez-tools python3-dbus network-manager
#   pip install pydbus
#
# Enable Bluetooth at boot: sudo systemctl enable bluetooth
# =============================================================================

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from config import BT_SCAN_DURATION, BT_A2DP_UUID

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BTDevice:
    address:   str
    name:      str
    paired:    bool  = False
    connected: bool  = False
    trusted:   bool  = False
    rssi:      int   = 0         # dBm (not always available from BlueZ)

    def __str__(self) -> str:
        state = []
        if self.paired:    state.append("paired")
        if self.connected: state.append("connected")
        if self.trusted:   state.append("trusted")
        return f"{self.name} [{self.address}] {'/'.join(state) or 'found'}"


@dataclass
class WiFiNetwork:
    ssid:     str
    signal:   int  = 0    # 0–100
    secured:  bool = True
    connected: bool = False


# ---------------------------------------------------------------------------
# Bluetooth Manager
# ---------------------------------------------------------------------------

class BluetoothManager:
    """
    Manages Bluetooth discovery, pairing, and connection via BlueZ D-Bus API.
    Falls back to bluetoothctl subprocess if pydbus is unavailable.

    Events (set callbacks before calling start()):
        on_device_found(BTDevice)
        on_connected(BTDevice)
        on_disconnected(address: str)
    """

    def __init__(self):
        self._devices:   Dict[str, BTDevice] = {}
        self._lock       = threading.Lock()
        self._running    = False
        self._thread:    Optional[threading.Thread] = None

        # Callbacks
        self.on_device_found:  Optional[Callable[[BTDevice], None]] = None
        self.on_connected:     Optional[Callable[[BTDevice], None]] = None
        self.on_disconnected:  Optional[Callable[[str], None]]      = None

        # Try pydbus for richer D-Bus access
        self._dbus_available = self._check_dbus()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> bool:
        """Power on the Bluetooth adapter and start the monitor thread."""
        if not self._bt_power_on():
            return False
        self._running = True
        self._thread  = threading.Thread(
            target=self._monitor_loop, name="BTManager", daemon=True
        )
        self._thread.start()
        log.info("Bluetooth manager started")
        return True

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=3.0)
        log.info("Bluetooth manager stopped")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def scan(self) -> List[BTDevice]:
        """Perform a BT scan and return discovered devices."""
        log.info("Scanning for Bluetooth devices (%ds)…", BT_SCAN_DURATION)
        result = self._run_btctl(f"scan on", wait=BT_SCAN_DURATION)
        self._run_btctl("scan off", wait=1)
        devices = self._parse_device_list()
        log.info("Found %d device(s)", len(devices))
        return devices

    def pair(self, address: str) -> bool:
        """Pair with a device by MAC address."""
        log.info("Pairing with %s", address)
        out = self._run_btctl(f"pair {address}", wait=15, expect="Pairing successful")
        success = "successful" in (out or "").lower()
        if success:
            with self._lock:
                if address in self._devices:
                    self._devices[address].paired = True
        log.info("Pair %s: %s", address, "OK" if success else "FAILED")
        return success

    def connect(self, address: str) -> bool:
        """Connect to a paired device."""
        log.info("Connecting to %s", address)
        out = self._run_btctl(f"connect {address}", wait=10, expect="Connection successful")
        success = "successful" in (out or "").lower()
        if success:
            with self._lock:
                if address in self._devices:
                    dev = self._devices[address]
                    dev.connected = True
                    if self.on_connected:
                        threading.Thread(
                            target=self.on_connected, args=(dev,), daemon=True
                        ).start()
        log.info("Connect %s: %s", address, "OK" if success else "FAILED")
        return success

    def disconnect(self, address: str) -> bool:
        """Disconnect a connected device."""
        log.info("Disconnecting %s", address)
        self._run_btctl(f"disconnect {address}", wait=5)
        with self._lock:
            if address in self._devices:
                self._devices[address].connected = False
        if self.on_disconnected:
            self.on_disconnected(address)
        return True

    def trust(self, address: str) -> None:
        """Mark a device as trusted (auto-connect on future visits)."""
        self._run_btctl(f"trust {address}", wait=3)
        with self._lock:
            if address in self._devices:
                self._devices[address].trusted = True

    def get_devices(self) -> List[BTDevice]:
        with self._lock:
            return list(self._devices.values())

    def get_connected_device(self) -> Optional[BTDevice]:
        with self._lock:
            for dev in self._devices.values():
                if dev.connected:
                    return dev
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        """Periodically check for newly connected/disconnected devices."""
        while self._running:
            devices = self._parse_device_list()
            with self._lock:
                for dev in devices:
                    prev = self._devices.get(dev.address)
                    self._devices[dev.address] = dev
                    # Fire on_connected when a new connection is detected
                    if dev.connected and (prev is None or not prev.connected):
                        log.info("BT device connected: %s", dev)
                        if self.on_connected:
                            self.on_connected(dev)
                    # Fire on_disconnected when a known device drops
                    if prev and prev.connected and not dev.connected:
                        log.info("BT device disconnected: %s", dev.address)
                        if self.on_disconnected:
                            self.on_disconnected(dev.address)
                    # Fire on_device_found for new entries
                    if prev is None and self.on_device_found:
                        self.on_device_found(dev)
            time.sleep(5.0)

    def _bt_power_on(self) -> bool:
        try:
            out = subprocess.run(
                ["bluetoothctl", "power", "on"],
                capture_output=True, text=True, timeout=5
            )
            return "succeeded" in out.stdout.lower() or "yes" in out.stdout.lower()
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.error("bluetoothctl not available: %s", exc)
            return False

    def _run_btctl(
        self, cmd: str, wait: int = 3, expect: Optional[str] = None
    ) -> Optional[str]:
        """Run a bluetoothctl command and return stdout."""
        full_cmd = f'echo -e "{cmd}\\nquit" | bluetoothctl'
        try:
            result = subprocess.run(
                full_cmd, shell=True, capture_output=True,
                text=True, timeout=wait + 5
            )
            return result.stdout
        except subprocess.TimeoutExpired:
            return None
        except Exception as exc:
            log.debug("bluetoothctl error: %s", exc)
            return None

    def _parse_device_list(self) -> List[BTDevice]:
        """Parse 'devices' and 'info' output from bluetoothctl."""
        devices: List[BTDevice] = []
        try:
            out = subprocess.run(
                ["bluetoothctl", "devices"],
                capture_output=True, text=True, timeout=5
            ).stdout
        except Exception:
            return devices

        for line in out.splitlines():
            parts = line.strip().split(" ", 2)
            if len(parts) >= 3 and parts[0] == "Device":
                addr = parts[1]
                name = parts[2]
                # Check connection status with 'info'
                info = self._get_device_info(addr)
                devices.append(BTDevice(
                    address=addr,
                    name=name,
                    paired=info.get("paired", False),
                    connected=info.get("connected", False),
                    trusted=info.get("trusted", False),
                ))
        return devices

    def _get_device_info(self, address: str) -> dict:
        info: dict = {}
        try:
            out = subprocess.run(
                ["bluetoothctl", "info", address],
                capture_output=True, text=True, timeout=5
            ).stdout
            for line in out.splitlines():
                line = line.strip().lower()
                if "paired:" in line:  info["paired"]    = "yes" in line
                if "connected:" in line: info["connected"] = "yes" in line
                if "trusted:" in line: info["trusted"]   = "yes" in line
        except Exception:
            pass
        return info

    def _check_dbus(self) -> bool:
        try:
            import pydbus  # noqa: F401
            return True
        except ImportError:
            return False


# ---------------------------------------------------------------------------
# WiFi Manager
# ---------------------------------------------------------------------------

class WiFiManager:
    """
    Manages WiFi connections via nmcli (NetworkManager CLI).
    Requires: sudo apt-get install network-manager
              sudo systemctl enable NetworkManager
    """

    def scan(self) -> List[WiFiNetwork]:
        """Return list of nearby WiFi networks."""
        networks: List[WiFiNetwork] = []
        try:
            out = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY,ACTIVE",
                 "device", "wifi", "list"],
                capture_output=True, text=True, timeout=15
            ).stdout
            for line in out.splitlines():
                parts = line.split(":")
                if len(parts) >= 4:
                    ssid     = parts[0].strip()
                    signal   = int(parts[1]) if parts[1].isdigit() else 0
                    secured  = parts[2].strip() != "--"
                    active   = parts[3].strip().lower() == "yes"
                    if ssid:
                        networks.append(WiFiNetwork(
                            ssid=ssid, signal=signal,
                            secured=secured, connected=active
                        ))
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            log.warning("nmcli not available: %s", exc)
        return networks

    def connect(self, ssid: str, password: Optional[str] = None) -> bool:
        """Connect to a WiFi network."""
        cmd = ["nmcli", "device", "wifi", "connect", ssid]
        if password:
            cmd += ["password", password]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            success = result.returncode == 0
            log.info("WiFi connect %s: %s", ssid, "OK" if success else result.stderr.strip())
            return success
        except subprocess.TimeoutExpired:
            log.error("WiFi connect timeout for %s", ssid)
            return False

    def disconnect(self) -> bool:
        try:
            result = subprocess.run(
                ["nmcli", "device", "disconnect", "wlan0"],
                capture_output=True, text=True, timeout=10
            )
            return result.returncode == 0
        except Exception:
            return False

    def get_current_ssid(self) -> Optional[str]:
        try:
            out = subprocess.run(
                ["nmcli", "-t", "-f", "ACTIVE,SSID", "device", "wifi"],
                capture_output=True, text=True, timeout=5
            ).stdout
            for line in out.splitlines():
                if line.startswith("yes:"):
                    return line.split(":", 1)[1].strip()
        except Exception:
            pass
        return None

    def get_ip_address(self) -> Optional[str]:
        try:
            out = subprocess.run(
                ["hostname", "-I"],
                capture_output=True, text=True, timeout=5
            ).stdout.strip()
            return out.split()[0] if out else None
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Connectivity Manager (facade)
# ---------------------------------------------------------------------------

class ConnectivityManager:
    """
    Top-level facade combining Bluetooth and WiFi managers.
    Used by the main application and dashboard UI.
    """

    def __init__(self):
        self.bluetooth = BluetoothManager()
        self.wifi      = WiFiManager()

    def start(self) -> None:
        self.bluetooth.start()
        log.info("ConnectivityManager ready")

    def stop(self) -> None:
        self.bluetooth.stop()
        log.info("ConnectivityManager stopped")

    @property
    def bt_connected_device(self) -> Optional[BTDevice]:
        return self.bluetooth.get_connected_device()

    @property
    def wifi_ssid(self) -> Optional[str]:
        return self.wifi.get_current_ssid()

    @property
    def ip_address(self) -> Optional[str]:
        return self.wifi.get_ip_address()
