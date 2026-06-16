# =============================================================================
# audio_manager.py — Audio Control Driver
# Handles: ALSA volume/source, pygame media playback, Bluetooth A2DP sink
#
# System dependencies:
#   sudo apt-get install python3-dev libasound2-dev pulseaudio bluez
#   pip install pyalsaaudio pygame
# =============================================================================

from __future__ import annotations

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import List, Optional

import alsaaudio
import pygame

from config import (
    AUDIO_MIXER_CARD, AUDIO_MIXER_CTL, AUDIO_DEFAULT_VOL, AUDIO_STEP,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Audio sources
# ---------------------------------------------------------------------------

class AudioSource:
    RADIO    = "radio"
    MEDIA    = "media"       # local files (USB / SD)
    BLUETOOTH = "bluetooth"  # A2DP sink
    AUX      = "aux"         # line-in


# ---------------------------------------------------------------------------
# ALSA Volume Controller
# ---------------------------------------------------------------------------

class VolumeController:
    """
    Controls system volume through ALSA mixer.
    Gracefully falls back to a software-only mode if the mixer
    control is not found (e.g. HDMI audio, USB DAC).
    """

    def __init__(self):
        self._mixer: Optional[alsaaudio.Mixer] = None
        self._volume: int = AUDIO_DEFAULT_VOL
        self._muted:  bool = False
        self._init_mixer()

    def _init_mixer(self) -> None:
        controls_to_try = [AUDIO_MIXER_CTL, "Master", "PCM", "Speaker", "Headphone"]
        for ctl in controls_to_try:
            try:
                self._mixer = alsaaudio.Mixer(ctl, cardindex=0)
                log.info("ALSA mixer opened: %s", ctl)
                # Sync volume from current system state
                vols = self._mixer.getvolume()
                self._volume = int(sum(vols) / len(vols)) if vols else AUDIO_DEFAULT_VOL
                return
            except alsaaudio.ALSAAudioError:
                continue
        log.warning("No ALSA mixer found — volume control is software-only")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def volume(self) -> int:
        return self._volume

    @property
    def muted(self) -> bool:
        return self._muted

    def set_volume(self, level: int) -> int:
        """Set volume to level (0–100). Returns actual level applied."""
        self._volume = max(0, min(100, level))
        self._apply()
        return self._volume

    def volume_up(self) -> int:
        return self.set_volume(self._volume + AUDIO_STEP)

    def volume_down(self) -> int:
        return self.set_volume(self._volume - AUDIO_STEP)

    def toggle_mute(self) -> bool:
        self._muted = not self._muted
        self._apply()
        log.info("Mute: %s", self._muted)
        return self._muted

    def _apply(self) -> None:
        if self._mixer is None:
            return
        try:
            effective = 0 if self._muted else self._volume
            self._mixer.setvolume(effective)
        except alsaaudio.ALSAAudioError as exc:
            log.warning("Failed to set ALSA volume: %s", exc)


# ---------------------------------------------------------------------------
# Media Player (pygame-based, local files)
# ---------------------------------------------------------------------------

class MediaPlayer:
    """
    Plays audio files from a local directory (USB stick / SD card).
    Supports MP3, WAV, OGG, FLAC.
    """

    SUPPORTED_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a"}

    def __init__(self):
        pygame.mixer.pre_init(frequency=44100, size=-16, channels=2, buffer=2048)
        pygame.mixer.init()
        self._playlist: List[Path] = []
        self._index:    int        = 0
        self._playing:  bool       = False
        self._paused:   bool       = False

    # ------------------------------------------------------------------

    def load_directory(self, path: str) -> int:
        """Scan a directory for audio files. Returns track count."""
        root = Path(path)
        if not root.exists():
            log.warning("Media path does not exist: %s", path)
            return 0
        self._playlist = sorted(
            f for f in root.rglob("*") if f.suffix.lower() in self.SUPPORTED_EXTS
        )
        self._index = 0
        log.info("Loaded %d tracks from %s", len(self._playlist), path)
        return len(self._playlist)

    def play(self) -> bool:
        if not self._playlist:
            log.warning("Playlist is empty")
            return False
        track = self._playlist[self._index]
        try:
            pygame.mixer.music.load(str(track))
            pygame.mixer.music.play()
            self._playing = True
            self._paused  = False
            log.info("Playing: %s", track.name)
            return True
        except pygame.error as exc:
            log.error("Cannot play %s: %s", track, exc)
            return False

    def pause(self) -> None:
        if self._playing and not self._paused:
            pygame.mixer.music.pause()
            self._paused = True

    def resume(self) -> None:
        if self._paused:
            pygame.mixer.music.unpause()
            self._paused = False

    def stop(self) -> None:
        pygame.mixer.music.stop()
        self._playing = False
        self._paused  = False

    def next_track(self) -> bool:
        self._index = (self._index + 1) % max(1, len(self._playlist))
        if self._playing:
            return self.play()
        return True

    def prev_track(self) -> bool:
        self._index = (self._index - 1) % max(1, len(self._playlist))
        if self._playing:
            return self.play()
        return True

    @property
    def current_track_name(self) -> str:
        if self._playlist:
            return self._playlist[self._index].stem
        return "No track"

    @property
    def is_playing(self) -> bool:
        return pygame.mixer.music.get_busy()


# ---------------------------------------------------------------------------
# Audio Manager (facade: routes sources, owns volume)
# ---------------------------------------------------------------------------

class AudioManager:
    """
    Central audio facade used by the dashboard.

    Responsibilities:
    - Maintain the active audio source (media / BT / aux / radio)
    - Delegate volume to VolumeController
    - Delegate playback to MediaPlayer
    - Switch ALSA sink when source changes
    """

    def __init__(self):
        self._volume   = VolumeController()
        self._player   = MediaPlayer()
        self._source   = AudioSource.MEDIA
        self._bt_connected = False
        self._bt_device: Optional[str] = None

    # ------------------------------------------------------------------
    # Source switching
    # ------------------------------------------------------------------

    def set_source(self, source: str) -> None:
        if source == self._source:
            return
        log.info("Audio source: %s → %s", self._source, source)
        # Stop current playback before switching
        if self._source == AudioSource.MEDIA:
            self._player.stop()
        self._source = source

    @property
    def source(self) -> str:
        return self._source

    # ------------------------------------------------------------------
    # Volume delegation
    # ------------------------------------------------------------------

    @property
    def volume(self) -> int:
        return self._volume.volume

    @property
    def muted(self) -> bool:
        return self._volume.muted

    def set_volume(self, level: int) -> int:
        return self._volume.set_volume(level)

    def volume_up(self) -> int:
        return self._volume.volume_up()

    def volume_down(self) -> int:
        return self._volume.volume_down()

    def toggle_mute(self) -> bool:
        return self._volume.toggle_mute()

    # ------------------------------------------------------------------
    # Media playback delegation
    # ------------------------------------------------------------------

    def load_media(self, path: str) -> int:
        return self._player.load_directory(path)

    def play(self) -> bool:
        self.set_source(AudioSource.MEDIA)
        return self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def resume(self) -> None:
        self._player.resume()

    def stop(self) -> None:
        self._player.stop()

    def next_track(self) -> bool:
        return self._player.next_track()

    def prev_track(self) -> bool:
        return self._player.prev_track()

    @property
    def current_track(self) -> str:
        return self._player.current_track_name

    @property
    def is_playing(self) -> bool:
        return self._player.is_playing

    # ------------------------------------------------------------------
    # Bluetooth audio status
    # ------------------------------------------------------------------

    def set_bt_connected(self, device_name: Optional[str]) -> None:
        self._bt_connected = device_name is not None
        self._bt_device    = device_name
        if self._bt_connected:
            self.set_source(AudioSource.BLUETOOTH)
        log.info("BT audio: %s", device_name or "disconnected")

    @property
    def bt_device(self) -> Optional[str]:
        return self._bt_device

    @property
    def bt_connected(self) -> bool:
        return self._bt_connected
