"""Example camera settings. Copy to config.py and fill in your values.

  cp config.example.py config.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

USERNAME = "admin"
PASSWORD = "CHANGE_ME"  # If it contains '%', Digest HA1 uses it literally
RTSP_PATH = "/onvif1"
RTSP_PORT = 554
DIGEST_REALM = "HIipCamera"

SNAPSHOT_DIR = Path.home() / "Desktop" / "CCTV" / "snapshots"

VIEW_WIDTH = 1280
VIEW_HEIGHT = 720

NUDGE_SECONDS = 0.95
MAX_HOLD_SECONDS = 8.0
PTZ_KEEPALIVE_SECONDS = 20.0


@dataclass(frozen=True)
class Camera:
    key: str
    name: str
    ip: str
    ptz_locked_by_default: bool
    is_default: bool = False

    @property
    def base_uri(self) -> str:
        return f"rtsp://{self.ip}:{RTSP_PORT}{RTSP_PATH}"

    @property
    def track_uri(self) -> str:
        return f"{self.base_uri}/track1"

    @property
    def play_url(self) -> str:
        user = quote(USERNAME, safe="")
        pw = quote(PASSWORD, safe="")
        return f"rtsp://{user}:{pw}@{self.ip}:{RTSP_PORT}{RTSP_PATH}"


CAMERAS: tuple[Camera, ...] = (
    Camera("cam1", "Camera 1", "192.168.0.10", ptz_locked_by_default=False),
    Camera("cam2", "Camera 2", "192.168.0.11", ptz_locked_by_default=False, is_default=True),
)

CAM_BY_KEY = {c.key: c for c in CAMERAS}


def default_camera() -> Camera:
    for cam in CAMERAS:
        if cam.is_default:
            return cam
    return CAMERAS[0]
