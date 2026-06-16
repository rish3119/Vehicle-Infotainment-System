# Vehicle Infotainment System

A full-featured automotive infotainment system for Raspberry Pi 3/4/5 that connects to any vehicle via the OBD-II CAN bus port and presents a touchscreen dashboard with real-time engine data, GPS navigation, media playback, and Bluetooth/WiFi connectivity.

---

## Features

| Feature | Details |
|---|---|
| CAN Bus / OBD-II | Reads Speed, RPM, Coolant Temp, Intake Temp, Throttle, Fuel Level, Engine Load, Battery Voltage, DTC fault count, MIL (Check Engine) status |
| GPS Navigation | NMEA-0183 serial GPS — latitude, longitude, altitude, speed, heading, satellite count |
| Audio Playback | Local USB/SD media (MP3, FLAC, OGG, WAV) via ALSA + pygame |
| Bluetooth Audio | A2DP sink via BlueZ — scan, pair, trust, connect from the UI |
| WiFi Management | Scan and connect to networks via NetworkManager (nmcli) |
| Dashboard UI | PyQt5 fullscreen dark theme — Drive / Media / Navigation / Settings tabs |
| Demo Mode | Runs entirely without hardware (`--demo` flag) for development/testing |
| Autostart | systemd service file included for boot-time startup |

---

## Hardware Required

| Component | Purpose |
|---|---|
| Raspberry Pi 4B (2GB+) | Main processor |
| MCP2515 CAN module (TJA1050) | CAN bus interface via SPI |
| u-blox NEO-6M GPS module | NMEA UART GPS |
| 7" HDMI Touchscreen (800×480) | Display |
| USB Audio Adapter | Audio output |
| 12V→5V 5A DC-DC Buck Converter | Vehicle power |
| OBD-II to DB9 cable | Vehicle CAN bus connection |
| USB Flash Drive | Local media storage |

Full component list with specs: `specs/bom.json`

---

## Wiring Summary

### MCP2515 CAN Module → Raspberry Pi
```
MCP2515 VCC  → Pi Pin 2  (5V)
MCP2515 GND  → Pi Pin 6  (GND)
MCP2515 SCK  → Pi Pin 23 (GPIO11 SPI CLK)
MCP2515 SI   → Pi Pin 19 (GPIO10 SPI MOSI)
MCP2515 SO   → Pi Pin 21 (GPIO9  SPI MISO)
MCP2515 CS   → Pi Pin 24 (GPIO8  SPI CE0)
MCP2515 INT  → Pi Pin 22 (GPIO25)
MCP2515 CANH → OBD-II Pin 6
MCP2515 CANL → OBD-II Pin 14
```

### NEO-6M GPS → Raspberry Pi
```
GPS VCC → Pi Pin 1  (3.3V)
GPS GND → Pi Pin 9  (GND)
GPS TX  → Pi Pin 10 (GPIO15 RXD)
GPS RX  → Pi Pin 8  (GPIO14 TXD)
```

Full step-by-step assembly guide: `docs/steps.json`

---

## Software Setup

```bash
# 1. Flash Raspberry Pi OS Bookworm (64-bit) to microSD

# 2. Enable SPI and serial port via raspi-config
sudo raspi-config

# 3. Add CAN overlay to /boot/config.txt
echo "dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25" | sudo tee -a /boot/config.txt
echo "dtoverlay=spi-bcm2835" | sudo tee -a /boot/config.txt

# 4. Install dependencies
sudo apt-get install -y python3-pyqt5 python3-pyqt5.qtchart libasound2-dev \
    can-utils bluez bluetooth network-manager
pip3 install -r src/requirements.txt

# 5. Bring up CAN interface
sudo ip link set can0 up type can bitrate 500000

# 6. Run in demo mode (no hardware needed)
python3 src/main.py --demo

# 7. Run with real hardware
sudo python3 src/main.py
```

---

## Project Structure

```
src/
  main.py                — Application entry point, CLI flags, subsystem orchestration
  config.py              — All pins, thresholds, colours, timing constants
  can_reader.py          — MCP2515 SPI driver, OBD-II PID polling (background thread)
  gps_reader.py          — NMEA serial GPS driver (background thread)
  audio_manager.py       — ALSA volume control + pygame media player + BT audio
  connectivity_manager.py— BlueZ Bluetooth manager + NetworkManager WiFi manager
  dashboard.py           — PyQt5 fullscreen UI (Drive/Media/Navigation/Settings tabs)
  requirements.txt       — Python dependencies
docs/
  steps.json             — Full 12-step assembly and setup guide
specs/
  bom.json               — Bill of materials
infotainment.service     — systemd unit file for autostart on boot
```

---

## CLI Flags

```
python3 src/main.py --demo       # Simulated sensor data — no hardware required
python3 src/main.py --no-can     # Skip CAN bus (GPS + UI only)
python3 src/main.py --no-gps     # Skip GPS (CAN + UI only)
python3 src/main.py --verbose    # Enable debug logging
```

---

## Autostart on Boot

```bash
sudo cp infotainment.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable infotainment
sudo systemctl start  infotainment
sudo journalctl -u infotainment -f   # live logs
```

---

## Supported Vehicles

Any vehicle with an OBD-II port that communicates on ISO 15765-4 (CAN, 11-bit, 500 kbps):
- All petrol/diesel cars and light trucks sold in the USA after 1996
- All EU/EOBD vehicles after 2001 (petrol) and 2004 (diesel)
- Most motorcycles after 2006 with OBD-II ports

For vehicles using ISO 9141-2 (K-Line) or SAE J1850 (older GM/Ford), a different physical interface is required — the MCP2515 handles CAN only.

---

## License

MIT
