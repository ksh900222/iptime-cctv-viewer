#!/usr/bin/env python3
"""iptime C500 one-window viewer + PTZ (Mac)."""

from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TypeVar

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Homebrew ffmpeg on Apple Silicon
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject, QSize, QEvent, QPointF
from PyQt6.QtGui import (
    QImage,
    QKeyEvent,
    QPixmap,
    QFont,
    QCloseEvent,
    QAction,
    QTransform,
    QWheelEvent,
    QMouseEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from config import (
    CAMERAS,
    MAX_HOLD_SECONDS,
    NUDGE_SECONDS,
    PTZ_KEEPALIVE_SECONDS,
    SNAPSHOT_DIR,
    Camera,
    default_camera,
)
from ptz import PtzClient, PtzError, remap_ptz_dir
from video import VideoWorker, find_ffmpeg
from audio import AudioWorker
from zoom import (
    ZOOM_STEP,
    clamp_pan,
    clamp_zoom,
    label_delta_to_source,
    label_to_oriented,
    pan_by,
    remap_pan_for_rotation,
    reset_view,
    view_rect,
    view_rect_int,
    zoom_at,
)

log = logging.getLogger("cctv.app")

QSS = """
QMainWindow, QWidget#root { background: #101218; color: #e8eaed; }
QLabel { color: #e8eaed; }
QLabel#video {
    background: #000;
    color: #666;
    qproperty-alignment: AlignCenter;
    font-size: 16px;
}
QLabel#status {
    background: #0a0c10;
    color: #9aa0a6;
    padding: 7px 12px;
    font-family: Menlo, monospace;
    font-size: 12px;
}
QLabel#sideTitle {
    color: #9aa0a6;
    font-size: 11px;
    letter-spacing: 1px;
    padding-top: 8px;
}
QPushButton {
    background: #2a303a;
    color: #e8eaed;
    border: 1px solid #3d4450;
    border-radius: 8px;
    padding: 8px 10px;
    font-size: 14px;
    min-height: 32px;
}
QPushButton:hover { background: #3a4250; }
QPushButton:pressed { background: #1b6b45; }
QPushButton:disabled { color: #666; background: #1a1d22; border-color: #2a2e36; }
QPushButton#camOn {
    background: #1e5a3a;
    border-color: #3cb371;
    font-weight: 600;
}
QPushButton#ptz, QPushButton#stopBtn {
    font-size: 16px;
    font-weight: 700;
    padding: 0;
    margin: 0;
    /* content-box: 1px border on each side → 58+2 = 60 widget */
    min-width: 58px;
    max-width: 58px;
    min-height: 58px;
    max-height: 58px;
}
QPushButton#stopBtn {
    background: #5a2222;
    border-color: #a33;
}
QPushButton#stopBtn:hover { background: #7a2a2a; }
QPushButton#stopBtn:disabled {
    color: #666;
    background: #1a1d22;
    border-color: #2a2e36;
}
"""

PTZ_BTN = 60
PTZ_GAP = 6
# Grace period before we re-open RTSP on a camera we just disconnected from.
# Measured on both cams: after a clean process-group SIGTERM the camera accepts
# a new session again within ~120ms, and time-to-first-frame is anchored to the
# camera's own keyframe cadence (~2.2s from teardown) rather than to this value.
# 0.4s keeps a safety margin without being on the critical path.
RTSP_SETTLE_SECONDS = 0.4
MIN_START_DELAY_MS = 120
_W = TypeVar("_W", bound=QWidget)

def _square(w: _W) -> _W:
    w.setFixedSize(PTZ_BTN, PTZ_BTN)
    w.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return w

class PtzWorker(QObject):
    """Serial PTZ on a background thread. PTZ for both cams; lock only via Camera.ptz_locked_by_default."""

    result = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._q: queue.Queue = queue.Queue()
        self._alive = True
        self._client: PtzClient | None = None
        self._cam: Camera | None = None
        self._thread = threading.Thread(target=self._loop, name="ptz", daemon=True)
        self._thread.start()

    def switch(self, cam: Camera) -> None:
        self._q.put(("switch", cam))

    def move(self, direction: str) -> None:
        self._q.put(("cmd", direction))

    def stop(self) -> None:
        self._q.put(("cmd", "STOP"))

    def shutdown(self) -> None:
        self._alive = False
        self._q.put(("quit", None))
        self._thread.join(timeout=2.5)

    def _allowed(self, cam: Camera | None) -> bool:
        if cam is None:
            return False
        if cam.ptz_locked_by_default:
            return False
        return True

    def _loop(self) -> None:
        last_ka = time.monotonic()
        while self._alive:
            try:
                kind, payload = self._q.get(timeout=0.4)
            except queue.Empty:
                if self._client and self._allowed(self._cam):
                    if time.monotonic() - last_ka >= PTZ_KEEPALIVE_SECONDS:
                        try:
                            self._client.keepalive()
                            last_ka = time.monotonic()
                        except Exception as e:
                            self.result.emit(f"PTZ keepalive: {e}")
                continue

            try:
                if kind == "quit":
                    self._teardown()
                    return
                if kind == "switch":
                    cam: Camera = payload
                    self._teardown()
                    self._cam = cam
                    if not self._allowed(cam):
                        self.result.emit(f"{cam.name} PTZ 잠금 (세션 없음)")
                        continue
                    self._client = PtzClient(cam.ip)
                    msg = self._client.connect()
                    last_ka = time.monotonic()
                    self.result.emit(msg)
                elif kind == "cmd":
                    direction = payload
                    cam = self._cam
                    if not self._allowed(cam):
                        if direction != "STOP":
                            self.result.emit("차단: PTZ 잠금")
                        continue
                    if self._client is None and cam is not None:
                        self._client = PtzClient(cam.ip)
                        self.result.emit(self._client.connect())
                    if self._client is None:
                        continue
                    self.result.emit(self._client.command(direction))
                    last_ka = time.monotonic()
            except PtzError as e:
                self.result.emit(f"PTZ 오류: {e}")
            except Exception as e:
                self.result.emit(f"PTZ 예외: {e}")

    def _teardown(self) -> None:
        c, self._client = self._client, None
        if c is None:
            return
        try:
            c.stop()
        except Exception:
            pass
        try:
            c.close()
        except Exception:
            pass

class HoldButton(QPushButton):
    held = pyqtSignal(str)
    let_go = pyqtSignal(str)

    def __init__(self, direction: str, label: str, object_name: str = "ptz") -> None:
        super().__init__(label)
        self.direction = direction
        self.setObjectName(object_name)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pressed.connect(lambda: self.held.emit(self.direction))
        self.released.connect(lambda: self.let_go.emit(self.direction))

class Viewer(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("iptime C500 뷰어")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.resize(1280, 820)
        self.cam = default_camera()
        self._last_image: QImage | None = None
        # Display-only orientation. Always one of {0, 90, 180, 270}; never a click counter.
        self._rotation_deg = 0
        # Display-only digital zoom/pan. Session-only; never written to disk.
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._pan_drag: QPointF | None = None
        self._ptz_press_t: float | None = None
        self._ptz_held: str | None = None
        self._nudge_timer = QTimer(self)
        self._ptz_repeat = QTimer(self)
        self._ptz_repeat.setInterval(280)
        self._ptz_repeat.timeout.connect(self._ptz_repeat_tick)
        self._ptz_cam_dir: str | None = None
        self._nudge_timer.setSingleShot(True)
        self._nudge_timer.timeout.connect(self._nudge_stop)
        self._hold_cap = QTimer(self)
        self._hold_cap.setSingleShot(True)
        self._hold_cap.timeout.connect(self._safety_stop)
        # One cancellable start timer: rapid camera switching must not queue
        # several _start_current() calls (that spawned competing ffmpegs).
        self._start_timer = QTimer(self)
        self._start_timer.setSingleShot(True)
        self._start_timer.timeout.connect(self._start_current)
        self._last_teardown: dict[str, float] = {}
        self._switch_t0: float | None = None
        self._closing = False
        self._retired: list[VideoWorker] = []

        self._cleanup_orphan_ffmpeg()
        self.video = VideoWorker()
        self.audio = AudioWorker()
        self.audio.status.connect(self._on_audio_status)
        self.video.frame.connect(self._on_frame)
        self.video.status.connect(self._on_video_status)
        self.ptz = PtzWorker()
        self.ptz.result.connect(self._on_ptz_status)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        layout.addLayout(body, 1)

        self.video_label = QLabel("연결 중…")
        self.video_label.setObjectName("video")
        self.video_label.setMinimumSize(QSize(640, 360))
        self.video_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.video_label.setScaledContents(False)
        self.video_label.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.video_label.installEventFilter(self)
        body.addWidget(self.video_label, 1)
        body.addWidget(self._build_side())

        self.status = QLabel("시작")
        self.status.setObjectName("status")
        layout.addWidget(self.status)

        self._rebuild_cam_buttons()
        self._apply_lock_ui()
        self._switch_t0 = time.monotonic()
        self._start_timer.start(100)

        snap = QAction("스냅샷", self)
        snap.setShortcut("S")
        snap.triggered.connect(self._snapshot)
        self.addAction(snap)

    def _build_side(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(220)
        v = QVBoxLayout(side)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(8)

        t = QLabel("카메라")
        t.setObjectName("sideTitle")
        v.addWidget(t)
        self.cam_btns: dict[str, QPushButton] = {}
        for cam in CAMERAS:
            b = QPushButton(f"{cam.name}\n{cam.ip}")
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _=False, c=cam: self._select_cam(c))
            self.cam_btns[cam.key] = b
            v.addWidget(b)

        t = QLabel("PTZ")
        t.setObjectName("sideTitle")
        v.addWidget(t)

        pad = QWidget()
        pad_side = PTZ_BTN * 3 + PTZ_GAP * 2
        pad.setFixedSize(pad_side, pad_side)
        pad.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        grid = QGridLayout(pad)
        grid.setSpacing(PTZ_GAP)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for i in range(3):
            grid.setColumnStretch(i, 1)
            grid.setRowStretch(i, 1)
            grid.setColumnMinimumWidth(i, PTZ_BTN)
            grid.setRowMinimumHeight(i, PTZ_BTN)

        self.btn_up = _square(HoldButton("UP", "↑"))
        self.btn_left = _square(HoldButton("LEFT", "←"))
        self.btn_stop = _square(HoldButton("STOP", "STOP", "stopBtn"))
        self.btn_right = _square(HoldButton("RIGHT", "→"))
        self.btn_down = _square(HoldButton("DOWN", "↓"))
        for b in (self.btn_up, self.btn_left, self.btn_right, self.btn_down):
            b.held.connect(self._ptz_press)
            b.let_go.connect(self._ptz_release)
        self.btn_stop.clicked.connect(self._ptz_force_stop)

        cells: dict[tuple[int, int], QWidget] = {
            (0, 1): self.btn_up,
            (1, 0): self.btn_left,
            (1, 1): self.btn_stop,
            (1, 2): self.btn_right,
            (2, 1): self.btn_down,
        }
        for r in range(3):
            for c in range(3):
                w = cells.get((r, c))
                if w is None:
                    w = _square(QWidget())
                grid.addWidget(w, r, c, Qt.AlignmentFlag.AlignCenter)
        v.addWidget(pad, 0, Qt.AlignmentFlag.AlignHCenter)

        vol_row = QHBoxLayout()
        vol_l = QLabel("볼륨")
        vol_l.setStyleSheet("color:#9aa3ad; font-size:12px;")
        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(100)
        self.vol_slider.setFixedHeight(22)
        self.vol_slider.setToolTip("카메라 오디오 볼륨")
        self.vol_label = QLabel(f"{self.vol_slider.value()}%")
        self.vol_label.setFixedWidth(40)
        self.vol_label.setStyleSheet("color:#c8d0d8; font-size:12px;")
        self.vol_slider.valueChanged.connect(self._on_volume)
        vol_row.addWidget(vol_l)
        vol_row.addWidget(self.vol_slider, 1)
        vol_row.addWidget(self.vol_label)
        v.addLayout(vol_row)

        self.audio_label = QLabel("오디오 준비 중…")
        self.audio_label.setStyleSheet("color:#7f8894; font-size:11px;")
        self.audio_label.setWordWrap(True)
        v.addWidget(self.audio_label)

        snap = QPushButton("스냅샷")
        snap.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        snap.clicked.connect(self._snapshot)
        v.addWidget(snap)

        recon = QPushButton("영상 재연결")
        recon.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        recon.clicked.connect(self._reconnect_video)
        v.addWidget(recon)

        rot_row = QHBoxLayout()
        self.btn_rot_ccw = QPushButton("↺ 90°")
        self.btn_rot_cw = QPushButton("↻ 90°")
        for b in (self.btn_rot_ccw, self.btn_rot_cw):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_rot_ccw.setToolTip("화면을 반시계 방향으로 90° 회전 (스트림은 유지)")
        self.btn_rot_cw.setToolTip("화면을 시계 방향으로 90° 회전 (스트림은 유지)")
        self.btn_rot_ccw.clicked.connect(lambda: self._rotate_view(-90))
        self.btn_rot_cw.clicked.connect(lambda: self._rotate_view(90))
        self.rot_label = QLabel("0°")
        self.rot_label.setFixedWidth(40)
        self.rot_label.setStyleSheet("color:#c8d0d8; font-size:12px;")
        self.rot_label.setToolTip("현재 화면 회전 각도")
        rot_row.addWidget(self.btn_rot_ccw, 1)
        rot_row.addWidget(self.btn_rot_cw, 1)
        rot_row.addWidget(self.rot_label)
        v.addLayout(rot_row)

        zoom_row = QHBoxLayout()
        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_reset = QPushButton("원본(1x)")
        for b in (self.btn_zoom_out, self.btn_zoom_in, self.btn_zoom_reset):
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_zoom_out.setToolTip("화면 축소 (스트림은 유지)")
        self.btn_zoom_in.setToolTip("화면 확대 (스트림은 유지)")
        self.btn_zoom_reset.setToolTip("줌/팬을 원본 배율로 되돌림")
        self.btn_zoom_out.clicked.connect(lambda: self._bump_zoom(1.0 / ZOOM_STEP))
        self.btn_zoom_in.clicked.connect(lambda: self._bump_zoom(ZOOM_STEP))
        self.btn_zoom_reset.clicked.connect(self._reset_zoom)
        self.zoom_label = QLabel("1.0x")
        self.zoom_label.setFixedWidth(40)
        self.zoom_label.setStyleSheet("color:#c8d0d8; font-size:12px;")
        self.zoom_label.setToolTip("현재 화면 확대 배율")
        zoom_row.addWidget(self.btn_zoom_out)
        zoom_row.addWidget(self.btn_zoom_in)
        zoom_row.addWidget(self.btn_zoom_reset, 1)
        zoom_row.addWidget(self.zoom_label)
        v.addLayout(zoom_row)

        v.addStretch(1)
        hint = QLabel(
            "←↑↓→ 이동  ·  space 정지\n"
            "1 안방  ·  2 거실  ·  S 스냅샷\n"
            "휠 줌 · 드래그 이동 · 더블클릭 원본"
        )
        hint.setStyleSheet("color:#6a7380; font-size:11px;")
        hint.setWordWrap(True)
        v.addWidget(hint)
        return side

    def _cleanup_orphan_ffmpeg(self) -> None:
        """Kill leftover cam ffmpeg so a fresh viewer can take RTSP slots."""
        import os
        import signal
        import subprocess
        try:
            out = subprocess.check_output(["pgrep", "-fl", "ffmpeg"], text=True, stderr=subprocess.DEVNULL)
        except Exception:
            return
        for line in out.splitlines():
            if "192.168.0." not in line:
                continue
            if "ffmpeg" not in line:
                continue
            try:
                pid = int(line.split(None, 1)[0])
            except ValueError:
                continue
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def _reap_retired(self) -> None:
        """Release parked workers once their thread has actually finished."""
        still: list[VideoWorker] = []
        for w in self._retired:
            if w.isRunning():
                still.append(w)
            else:
                w.deleteLater()
        self._retired = still

    def _start_current(self) -> None:
        if self._closing:
            return
        self._set_status(f"{self.cam.name} {self.cam.ip} 연결")
        self.audio.stop_pcm()
        fifo = self.video.prepare_audio_fifo()
        self.audio.start_pcm(fifo)
        linear = self.vol_slider.value() / 100.0
        self.audio.set_enabled(linear > 0)
        self.audio.set_volume(linear)
        self.video.play(self.cam.play_url)
        self.ptz.switch(self.cam)

    def _settle_delay_ms(self, ip: str) -> int:
        """How long the target camera still needs before it accepts us back."""
        last = self._last_teardown.get(ip)
        if last is None:
            return MIN_START_DELAY_MS
        remain = RTSP_SETTLE_SECONDS - (time.monotonic() - last)
        return max(MIN_START_DELAY_MS, int(remain * 1000))

    def _teardown_stream(self, ip: str) -> None:
        """Stop audio + video for the camera at `ip` and arm a fresh worker."""
        self._start_timer.stop()
        old = self.video
        try:
            old.frame.disconnect(self._on_frame)
            old.status.disconnect(self._on_video_status)
        except (TypeError, RuntimeError):
            pass
        # Video first, audio second: the PCM reader has to keep draining the
        # FIFO or ffmpeg blocks on its audio write, ignores SIGTERM, and gets
        # SIGKILLed — which skips RTSP TEARDOWN and makes the camera answer
        # 400 Bad Request for many seconds afterwards.
        old.stop_stream()
        self.audio.stop_pcm()
        self._last_teardown[ip] = time.monotonic()
        if old.isRunning():
            # Destroying a running QThread aborts the process; park it instead.
            log.warning("video worker still running after stop; retired")
            self._retired.append(old)
        else:
            old.deleteLater()
        self._reap_retired()
        self._cleanup_orphan_ffmpeg()
        self.video = VideoWorker()
        self.video.frame.connect(self._on_frame)
        self.video.status.connect(self._on_video_status)

    def _select_cam(self, cam: Camera) -> None:
        if cam.key == self.cam.key:
            return
        prev_ip = self.cam.ip
        self._ptz_force_stop()
        self.cam = cam
        self._rebuild_cam_buttons()
        self._apply_lock_ui()
        self.video_label.setText("전환 중…")
        self.video_label.setPixmap(QPixmap())
        self._last_image = None
        self._teardown_stream(prev_ip)
        self._switch_t0 = time.monotonic()
        self._set_status(f"{cam.name} 재연결 대기…")
        self._start_timer.start(self._settle_delay_ms(cam.ip))

    def _reconnect_video(self) -> None:
        ip = self.cam.ip
        self._teardown_stream(ip)
        self._switch_t0 = time.monotonic()
        self._set_status("영상 재연결 대기…")
        self._start_timer.start(self._settle_delay_ms(ip))

    def _rebuild_cam_buttons(self) -> None:
        for cam in CAMERAS:
            b = self.cam_btns[cam.key]
            b.setObjectName("camOn" if cam.key == self.cam.key else "")
            b.setStyleSheet("")  # refresh QSS objectName
            b.style().unpolish(b)
            b.style().polish(b)

    def _apply_lock_ui(self) -> None:
        enabled = not (
            self.cam.ptz_locked_by_default
        )
        for b in (self.btn_up, self.btn_down, self.btn_left, self.btn_right, self.btn_stop):
            b.setEnabled(enabled)

    def _ptz_ok(self) -> bool:
        if self.cam.ptz_locked_by_default:
            self._set_status("차단: PTZ 잠금")
            return False
        return True

    def _ptz_press(self, direction: str) -> None:
        if not self._ptz_ok():
            return
        self._nudge_timer.stop()
        self._ptz_repeat.stop()
        self._ptz_press_t = time.monotonic()
        # Track the visual dir so press/release still match after remapping.
        self._ptz_held = direction
        cam_dir = remap_ptz_dir(direction, self._rotation_deg)
        self._ptz_cam_dir = cam_dir
        self._set_status(f"PTZ {direction}" + (f" → {cam_dir}" if cam_dir != direction else ""))
        self.ptz.move(cam_dir)
        # Re-send while held — some iptime firmwares ignore a single DOWN.
        self._ptz_repeat.start()
        self._hold_cap.start(int(MAX_HOLD_SECONDS * 1000))

    def _ptz_repeat_tick(self) -> None:
        if self._ptz_held and self._ptz_cam_dir:
            self.ptz.move(self._ptz_cam_dir)


    def _ptz_release(self, direction: str) -> None:
        if self._ptz_held != direction:
            return
        self._hold_cap.stop()
        self._ptz_repeat.stop()
        self._ptz_cam_dir = None
        t0 = self._ptz_press_t or time.monotonic()
        held = time.monotonic() - t0
        remain = NUDGE_SECONDS - held
        self._ptz_held = None
        if remain > 0.05:
            self._nudge_timer.start(int(remain * 1000))
        else:
            self.ptz.stop()

    def _nudge_stop(self) -> None:
        if self._ptz_held is None:
            self.ptz.stop()

    def _safety_stop(self) -> None:
        self._ptz_held = None
        self._ptz_cam_dir = None
        self._nudge_timer.stop()
        self._ptz_repeat.stop()
        self.ptz.stop()
        self._set_status("PTZ 안전 정지 (최대 유지 시간)")

    def _ptz_force_stop(self) -> None:
        self._ptz_held = None
        self._ptz_cam_dir = None
        self._nudge_timer.stop()
        self._ptz_repeat.stop()
        self._hold_cap.stop()
        self.ptz.stop()

    def _on_frame(self, img: QImage) -> None:
        if self._switch_t0 is not None:
            log.info("first frame %.0f ms after switch", (time.monotonic() - self._switch_t0) * 1000)
            self._switch_t0 = None
        self._last_image = img
        self._paint_frame()

    def _rotate_view(self, delta: int) -> None:
        """Rotate the live view by ±90°. State stays in {0, 90, 180, 270} via modulo 360."""
        old = self._rotation_deg
        new = (old + delta) % 360
        img = self._last_image
        if img is not None and not img.isNull() and self._zoom > 1.0:
            self._pan_x, self._pan_y = remap_pan_for_rotation(
                self._pan_x,
                self._pan_y,
                img.width(),
                img.height(),
                old,
                new,
                self._zoom,
            )
        self._rotation_deg = new
        self.rot_label.setText(f"{self._rotation_deg}°")
        self._paint_frame()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def _oriented_image(self, img: QImage) -> QImage:
        if self._rotation_deg:
            return img.transformed(QTransform().rotate(self._rotation_deg))
        return img

    def _oriented_size(self) -> tuple[int, int] | None:
        img = self._last_image
        if img is None or img.isNull():
            return None
        w, h = img.width(), img.height()
        if self._rotation_deg in (90, 270):
            return h, w
        return w, h

    def _paint_frame(self) -> None:
        img = self._last_image
        if img is None or img.isNull():
            return
        oriented = self._oriented_image(img)
        src_w, src_h = oriented.width(), oriented.height()
        self._pan_x, self._pan_y = clamp_pan(
            self._pan_x, self._pan_y, src_w, src_h, self._zoom
        )
        if self._zoom > 1.0:
            x, y, w, h = view_rect_int(
                src_w, src_h, self._zoom, self._pan_x, self._pan_y
            )
            oriented = oriented.copy(x, y, w, h)
            if oriented.isNull():
                return
        pix = QPixmap.fromImage(oriented)
        target = self.video_label.size()
        if target.width() < 2 or target.height() < 2:
            return
        self.video_label.setPixmap(
            pix.scaled(
                target,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
        )

    def _sync_zoom_ui(self) -> None:
        self.zoom_label.setText(f"{self._zoom:.1f}x")
        if self._zoom > 1.0:
            cursor = (
                Qt.CursorShape.ClosedHandCursor
                if self._pan_drag is not None
                else Qt.CursorShape.OpenHandCursor
            )
        else:
            cursor = Qt.CursorShape.ArrowCursor
        self.video_label.setCursor(cursor)

    def _bump_zoom(self, factor: float, anchor_label: QPointF | None = None) -> None:
        new_z = clamp_zoom(self._zoom * factor)
        size = self._oriented_size()
        if size is None:
            self._zoom = new_z
            if new_z <= 1.0:
                self._pan_x = self._pan_y = 0.0
            self._sync_zoom_ui()
            return
        src_w, src_h = size
        if anchor_label is not None:
            ax, ay = label_to_oriented(
                anchor_label.x(),
                anchor_label.y(),
                src_w,
                src_h,
                self.video_label.width(),
                self.video_label.height(),
                self._zoom,
                self._pan_x,
                self._pan_y,
            )
        else:
            vx, vy, vw, vh = view_rect(
                src_w, src_h, self._zoom, self._pan_x, self._pan_y
            )
            ax, ay = vx + vw / 2.0, vy + vh / 2.0
        self._zoom, self._pan_x, self._pan_y = zoom_at(
            self._zoom, self._pan_x, self._pan_y, src_w, src_h, new_z, ax, ay
        )
        self._sync_zoom_ui()
        self._paint_frame()

    def _reset_zoom(self) -> None:
        self._zoom, self._pan_x, self._pan_y = reset_view()
        self._pan_drag = None
        self._sync_zoom_ui()
        self._paint_frame()

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is not self.video_label:
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.Type.Wheel:
            self._on_video_wheel(event)
            return True
        if et == QEvent.Type.MouseButtonDblClick:
            if event.button() == Qt.MouseButton.LeftButton:
                self._reset_zoom()
                return True
        elif et == QEvent.Type.MouseButtonPress:
            if event.button() == Qt.MouseButton.LeftButton:
                self._pan_drag = event.position()
                self._sync_zoom_ui()
                return True
        elif et == QEvent.Type.MouseMove:
            if self._pan_drag is not None and self._zoom > 1.0:
                self._on_video_pan(event)
                return True
        elif et == QEvent.Type.MouseButtonRelease:
            if event.button() == Qt.MouseButton.LeftButton:
                self._pan_drag = None
                self._sync_zoom_ui()
                return True
        return super().eventFilter(obj, event)

    def _on_video_wheel(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            delta = event.pixelDelta().y()
        if delta == 0:
            return
        factor = ZOOM_STEP if delta > 0 else 1.0 / ZOOM_STEP
        self._bump_zoom(factor, event.position())

    def _on_video_pan(self, event: QMouseEvent) -> None:
        size = self._oriented_size()
        if size is None or self._pan_drag is None:
            return
        pos = event.position()
        dx = pos.x() - self._pan_drag.x()
        dy = pos.y() - self._pan_drag.y()
        self._pan_drag = QPointF(pos)
        src_w, src_h = size
        dsx, dsy = label_delta_to_source(
            dx,
            dy,
            src_w,
            src_h,
            self.video_label.width(),
            self.video_label.height(),
            self._zoom,
        )
        self._pan_x, self._pan_y = pan_by(
            self._pan_x, self._pan_y, src_w, src_h, self._zoom, dsx, dsy
        )
        self._paint_frame()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._paint_frame()

    def _on_video_status(self, msg: str) -> None:
        self._set_status(f"{self.cam.name} · {msg}")

    def _on_ptz_status(self, msg: str) -> None:
        self._set_status(f"{self.cam.name} · {msg}")

    def _set_status(self, msg: str) -> None:
        self.status.setText(msg)

    def _snapshot(self) -> None:
        # Full uncropped frame (current rotation). Digital zoom/pan is display-only.
        img = self._last_image
        if img is None or img.isNull():
            self._set_status("스냅샷: 아직 프레임 없음")
            return
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = SNAPSHOT_DIR / f"{self.cam.key}_{ts}.jpg"
        img = self._oriented_image(img)
        if img.save(str(path), "JPG", 90):
            self._set_status(f"저장 {path}")
        else:
            self._set_status("스냅샷 저장 실패")

    def event(self, event: QEvent) -> bool:  # noqa: N802
        # Arrow keys must always drive PTZ, even if a slider had focus.
        if event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and not event.isAutoRepeat():
                mapping = {
                    Qt.Key.Key_Left: "LEFT",
                    Qt.Key.Key_Right: "RIGHT",
                    Qt.Key.Key_Up: "UP",
                    Qt.Key.Key_Down: "DOWN",
                }
                if event.key() in mapping:
                    self._ptz_press(mapping[event.key()])
                    return True
                if event.key() == Qt.Key.Key_Space:
                    self._ptz_force_stop()
                    return True
        if event.type() == QEvent.Type.KeyRelease:
            if isinstance(event, QKeyEvent) and not event.isAutoRepeat():
                mapping = {
                    Qt.Key.Key_Left: "LEFT",
                    Qt.Key.Key_Right: "RIGHT",
                    Qt.Key.Key_Up: "UP",
                    Qt.Key.Key_Down: "DOWN",
                }
                if event.key() in mapping:
                    self._ptz_release(mapping[event.key()])
                    return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.isAutoRepeat():
            return
        key = event.key()
        mapping = {
            Qt.Key.Key_Left: "LEFT",
            Qt.Key.Key_Right: "RIGHT",
            Qt.Key.Key_Up: "UP",
            Qt.Key.Key_Down: "DOWN",
        }
        if key in mapping:
            self._ptz_press(mapping[key])
            return
        if key == Qt.Key.Key_Space:
            self._ptz_force_stop()
            return
        if key == Qt.Key.Key_1:
            self._select_cam(CAMERAS[0])
            return
        if key == Qt.Key.Key_2:
            self._select_cam(CAMERAS[1])
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.isAutoRepeat():
            return
        mapping = {
            Qt.Key.Key_Left: "LEFT",
            Qt.Key.Key_Right: "RIGHT",
            Qt.Key.Key_Up: "UP",
            Qt.Key.Key_Down: "DOWN",
        }
        if event.key() in mapping:
            self._ptz_release(mapping[event.key()])
            return
        super().keyReleaseEvent(event)

    def _on_volume(self, value: int) -> None:
        self.vol_label.setText(f"{value}%")
        self.audio.set_enabled(value > 0)
        self.audio.set_volume(value / 100.0)


    def _on_audio_status(self, msg: str) -> None:
        self.audio_label.setText(msg)
        if "실패" in msg or "오류" in msg or "없음" in msg:
            self._set_status(msg)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._closing = True
        self._start_timer.stop()
        self._ptz_force_stop()
        self.video.stop_stream()
        for w in self._retired:
            try:
                w.stop_stream()
            except Exception:
                pass
        self.audio.shutdown()
        self.ptz.shutdown()
        # A surviving ffmpeg keeps the RTSP slot and makes the next launch slow.
        self._cleanup_orphan_ffmpeg()
        event.accept()

def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        find_ffmpeg()
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setApplicationName("iptime C500 뷰어")
    app.setStyleSheet(QSS)
    font = QFont()
    font.setPointSize(13)
    app.setFont(font)
    win = Viewer()
    win.show()
    return app.exec()

if __name__ == "__main__":
    sys.exit(main())
