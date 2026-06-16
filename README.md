# Vehicle-Infotainment-System

---

## What Was Built

### Source Files (7 Python modules)

| File | Purpose |
|---|---|
| `src/config.py` | All configuration — CAN PIDs, GPS port, audio settings, display colours, logging |
| `src/can_reader.py` | MCP2515 SPI driver — polls 9 OBD-II PIDs (speed, RPM, temps, fuel, load, battery, DTC) at 10 Hz in a background thread |
| `src/gps_reader.py` | NEO-6M UART driver — parses GGA and RMC NMEA sentences, provides lat/lon/speed/heading in a background thread |
| `src/audio_manager.py` | ALSA volume control + pygame media player + Bluetooth A2DP audio source switching |
| `src/connectivity_manager.py` | BlueZ Bluetooth (scan/pair/trust/connect via bluetoothctl) + NetworkManager WiFi (nmcli) |
| `src/dashboard.py` | PyQt5 fullscreen UI — 4 tabs: Drive (gauges), Media, Navigation, Settings |
| `src/main.py` | Entry point — wires all subsystems together, demo mode with simulated data, SIGINT handling |

### Dashboard Tabs
- **Drive** — Circular speedometer and RPM gauges, coolant/fuel/load/battery readouts, MIL/DTC warning
- **Media** — Source selector (USB/BT/AUX), track playback controls, ALSA volume slider, mute
- **Navigation** — GPS fix status, coordinates, altitude, speed, heading, 8-point compass
- **Settings** — WiFi network scanner, Bluetooth device scanner/pairing, reboot/shutdown

### Documentation
- `specs/bom.json` — 10-item bill of materials with specs and purpose for every component
- `docs/steps.json` — 12-step assembly guide covering OS setup through Bluetooth pairing
- `README.md` — Full project overview, wiring tables, setup commands, CLI flags

### System Files
- `infotainment.service` — systemd unit for autostart on boot
- `src/requirements.txt` — All Python dependencies

### Key Capabilities
- Works on **any OBD-II vehicle** (all cars since 1996 in the US, 2001 in Europe)
- **Demo mode** (`--demo` flag) simulates all sensor data — no hardware needed for development
- Individual subsystems can be disabled (`--no-can`, `--no-gps`)
- Boots automatically and recovers from crashes via systemd `Restart=on-failure`
