# =============================================================================
# config.py — Vehicle Infotainment System Configuration
# Target: Raspberry Pi 3/4/5
# =============================================================================

# ---------------------------------------------------------------------------
# CAN Bus (MCP2515 via SPI)
# /boot/config.txt must include:
#   dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
#   dtoverlay=spi-bcm2835
# Run: sudo ip link set can0 up type can bitrate 500000
# ---------------------------------------------------------------------------
CAN_CHANNEL        = "can0"
CAN_BITRATE        = 500000        # 500 kbps — standard OBD-II / CAN HS
CAN_INTERFACE      = "socketcan"
CAN_TIMEOUT        = 0.5           # seconds, response wait per PID request
CAN_POLL_INTERVAL  = 0.1           # seconds between PID polls

# OBD-II standard arbitration IDs
OBD_REQUEST_ID     = 0x7DF        # Broadcast functional request
OBD_REPLY_BASE     = 0x7E8        # ECU reply base (7E8 … 7EF)

# OBD-II Mode 01 PIDs
PID_ENGINE_RPM     = 0x0C
PID_VEHICLE_SPEED  = 0x0D
PID_COOLANT_TEMP   = 0x05
PID_INTAKE_TEMP    = 0x0F
PID_THROTTLE       = 0x11
PID_FUEL_LEVEL     = 0x2F
PID_ENGINE_LOAD    = 0x04
PID_FUEL_PRESSURE  = 0x0A
PID_BATTERY_VOLT   = 0x42
PID_DTC_COUNT      = 0x01         # Mode 01, PID 01 — MIL + DTC count

# ---------------------------------------------------------------------------
# GPS (NMEA serial module — NEO-6M / NEO-8M / PA1616D)
# Connect TX→GPIO15 (RXD), RX→GPIO14 (TXD)
# Disable serial console: sudo raspi-config → Interface → Serial → No console
# ---------------------------------------------------------------------------
GPS_PORT           = "/dev/ttyAMA0"   # UART on GPIO14/15; use /dev/ttyUSB0 for USB dongles
GPS_BAUD           = 9600
GPS_TIMEOUT        = 2.0              # seconds readline timeout

# ---------------------------------------------------------------------------
# Audio (ALSA)
# For USB audio dongle or HDMI audio
# ---------------------------------------------------------------------------
AUDIO_MIXER_CARD   = "default"
AUDIO_MIXER_CTL    = "PCM"           # Change to "Master" for HDMI/headphone jack
AUDIO_DEFAULT_VOL  = 70              # 0–100
AUDIO_STEP         = 5               # Volume up/down step

# ---------------------------------------------------------------------------
# Bluetooth (BlueZ via bluetoothctl)
# Built-in BT on RPi 3/4/5 — ensure bluetoothd is running
# ---------------------------------------------------------------------------
BT_SCAN_DURATION   = 8              # seconds for device discovery
BT_A2DP_UUID       = "0000110d-0000-1000-8000-00805f9b34fb"
BT_HFP_UUID        = "0000111e-0000-1000-8000-00805f9b34fb"

# ---------------------------------------------------------------------------
# Display (PyQt5 fullscreen dashboard)
# Optimised for 7" 800×480 RPi touchscreen or 1024×600 HDMI panel
# ---------------------------------------------------------------------------
DISPLAY_WIDTH      = 800
DISPLAY_HEIGHT     = 480
DISPLAY_FPS        = 30             # UI refresh rate (Hz)
DISPLAY_THEME      = "dark"         # "dark" | "light"

# Colour palette (dark automotive theme)
COLOUR_BG          = "#0d0d0d"
COLOUR_PANEL       = "#1c1c1c"
COLOUR_ACCENT      = "#00bfff"      # DeepSkyBlue
COLOUR_WARNING     = "#ff8c00"      # DarkOrange
COLOUR_DANGER      = "#ff2020"      # Red
COLOUR_TEXT        = "#e0e0e0"
COLOUR_TEXT_DIM    = "#707070"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
LOG_LEVEL          = "INFO"         # DEBUG | INFO | WARNING | ERROR
LOG_FILE           = "/var/log/infotainment.log"
LOG_MAX_BYTES      = 5 * 1024 * 1024   # 5 MB rotate
LOG_BACKUP_COUNT   = 3
