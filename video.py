"""Live HEVC view via ffmpeg subprocess (RTSP/TCP + VideoToolbox)."""

from __future__ import annotations

import logging
import os
import select
import shutil
import signal
import subprocess
import threading
import time
import tempfile
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from config import VIEW_HEIGHT, VIEW_WIDTH

log = logging.getLogger("cctv.video")


def find_ffmpeg() -> str:
    env = os.environ.get("FFMPEG")
    if env and os.path.isfile(env):
        return env
    for p in ("/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if os.path.isfile(p):
            return p
    found = shutil.which("ffmpeg")
    if not found:
        raise FileNotFoundError("ffmpeg not found (brew install ffmpeg)")
    return found


def _readexactly(fp, n: int) -> Optional[bytes]:
    buf = bytearray()
    while len(buf) < n:
        chunk = fp.read(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class VideoWorker(QThread):
    frame = pyqtSignal(QImage)
    status = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._url = ""
        self._stop = threading.Event()
        self._ffmpeg = find_ffmpeg()
        self._proc: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()
        self._hard_kill = False
        self.use_videotoolbox = True
        self.width = VIEW_WIDTH
        self.height = VIEW_HEIGHT
        self._audio_fifo = ""
        self._fifo_keep_fd = -1
        self._fifo_dir = ""
        self._audio_gain = float(os.environ.get("CCTV_AUDIO_GAIN", "40"))
        self._audio_muted = False

    def play(self, url: str) -> None:
        self._url = url
        self.use_videotoolbox = True
        if not self.isRunning():
            self._stop.clear()
            self.start()
        else:
            # Running thread will notice URL change and reconnect.
            self._kill_proc()

    def set_audio_gain(self, gain: float, muted: bool = False) -> None:
        """Deprecated: slider volume is QAudioSink-side. Kept for API compat."""
        self._audio_gain = float(gain)
        self._audio_muted = bool(muted)

    def stop_stream(self) -> None:
        self._stop.set()
        self._kill_proc()
        # FIFO cleanup after ffmpeg dies (writer closed)
        stopped = self.wait(3000)
        if not stopped:
            log.warning("video thread did not stop in time")
        # Blunt fallback only when our own process-group kill was not clean.
        # SIGKILL skips RTSP TEARDOWN, so never spend it on the happy path.
        if not stopped or self._hard_kill:
            log.warning("falling back to pkill -9 for leftover cam ffmpeg")
            try:
                subprocess.run(
                    ["pkill", "-9", "-f", "ffmpeg.*192.168.0"],
                    capture_output=True,
                    timeout=2,
                )
            except Exception:
                pass
        self.cleanup_audio_fifo()

    def run(self) -> None:
        frame_size = self.width * self.height * 3
        backoff = 1.0
        while not self._stop.is_set():
            url = self._url
            if not url:
                time.sleep(0.1)
                continue
            try:
                cmd = self._cmd(url)
            except RuntimeError as e:
                # FIFO was torn down under us; never let this kill the thread.
                self.status.emit(f"오디오 FIFO 없음: {e}")
                break
            self.status.emit("영상 연결 중 (RTSP/TCP)…")
            log.info("ffmpeg %s", " ".join(cmd[:8]) + " …")
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL,
                    bufsize=frame_size,
                    start_new_session=True,
                )
            except OSError as e:
                self.status.emit(f"ffmpeg 실행 실패: {e}")
                time.sleep(2)
                continue

            # Publish under the lock so a stop_stream() racing with the spawn
            # cannot leave an orphan ffmpeg holding the RTSP session.
            with self._proc_lock:
                self._proc = proc
            if self._stop.is_set():
                self._kill_proc()
                break

            assert proc.stderr is not None
            threading.Thread(
                target=self._drain_stderr,
                args=(proc.stderr,),
                daemon=True,
            ).start()

            got = 0
            t0 = time.monotonic()
            fps_n = 0
            fps_t = t0
            stdout = proc.stdout
            assert stdout is not None
            try:
                while not self._stop.is_set() and self._url == url:
                    blob = _readexactly(stdout, frame_size)
                    if blob is None:
                        break
                    img = QImage(
                        blob,
                        self.width,
                        self.height,
                        self.width * 3,
                        QImage.Format.Format_RGB888,
                    )
                    if img.isNull():
                        continue
                    if self._stop.is_set() or self._url != url:
                        break
                    self.frame.emit(img.copy())
                    got += 1
                    if got == 1:
                        log.info("first frame ok %sx%s", self.width, self.height)
                    fps_n += 1
                    now = time.monotonic()
                    if now - fps_t >= 1.0:
                        self.status.emit(f"live {fps_n} fps · {self.width}x{self.height} · TCP")
                        fps_n = 0
                        fps_t = now
                    backoff = 1.0
            except Exception as e:
                log.warning("frame loop: %s", e)
            finally:
                self._kill_proc()

            if self._stop.is_set():
                break
            # Give the camera time to free the RTSP session before retry.
            self._stop.wait(0.7)
            if self._stop.is_set():
                break
            if got == 0 and self.use_videotoolbox:
                self.use_videotoolbox = False
                self.status.emit("VideoToolbox 실패 → 소프트웨어 디코더로 재시도")
                continue
            if got == 0:
                self.status.emit(f"연결 실패, {backoff:.0f}s 후 재시도")
            else:
                self.status.emit(f"영상 끊김, {backoff:.0f}s 후 재연결")
            self._stop.wait(backoff)
            backoff = min(backoff * 1.5, 6.0)


    def prepare_audio_fifo(self) -> str:
        """Create a FIFO for PCM; keep RDWR open so readers/writers do not block forever."""
        self.cleanup_audio_fifo()
        self._fifo_dir = tempfile.mkdtemp(prefix="cctv_av_")
        self._audio_fifo = os.path.join(self._fifo_dir, "audio.pcm")
        os.mkfifo(self._audio_fifo)
        # Keep the FIFO "open" so open(O_RDONLY) in the audio thread does not hang
        # before ffmpeg starts, and ffmpeg open(O_WRONLY) succeeds immediately.
        self._fifo_keep_fd = os.open(self._audio_fifo, os.O_RDWR | os.O_NONBLOCK)
        return self._audio_fifo

    def cleanup_audio_fifo(self) -> None:
        if self._fifo_keep_fd >= 0:
            try:
                os.close(self._fifo_keep_fd)
            except OSError:
                pass
            self._fifo_keep_fd = -1
        if self._audio_fifo and os.path.exists(self._audio_fifo):
            try:
                os.unlink(self._audio_fifo)
            except OSError:
                pass
        self._audio_fifo = ""
        if self._fifo_dir and os.path.isdir(self._fifo_dir):
            try:
                shutil.rmtree(self._fifo_dir, ignore_errors=True)
            except Exception:
                pass
            self._fifo_dir = ""

    def _cmd(self, url: str) -> list[str]:
        # ONE RTSP session: video → stdout, audio → FIFO (QAudioSink owns volume).
        if not self._audio_fifo:
            raise RuntimeError("prepare_audio_fifo() before play")
        cmd = [
            self._ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-y",
            "-loglevel",
            "warning",
            "-rtsp_transport",
            "tcp",
            "-timeout",
            "8000000",
            "-probesize",
            "2M",
            "-analyzeduration",
            "1000000",
        ]
        if self.use_videotoolbox:
            cmd += ["-hwaccel", "videotoolbox"]
        cmd += [
            "-i",
            url,
            # video out
            "-map",
            "0:v:0",
            "-vf",
            f"scale={self.width}:{self.height}",
            "-pix_fmt",
            "rgb24",
            "-f",
            "rawvideo",
            "pipe:1",
            # audio out (same RTSP) → FIFO for QAudioSink (smooth volume)
            "-map",
            "0:a:0",
            "-vn",
            "-af",
            "aresample=48000,volume=40",
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            self._audio_fifo,
        ]
        return cmd

    def _drain_stderr(self, pipe) -> None:
        try:
            for raw in iter(pipe.readline, b""):
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    log.info("ffmpeg: %s", line)
        except Exception:
            pass

    def _drain_stdout(self, proc: subprocess.Popen, deadline: float) -> None:
        """Keep the raw-video pipe empty while ffmpeg shuts down.

        The frame loop stops reading as soon as _stop is set, and an ffmpeg
        blocked in write() never reaches its signal handler — so it survives
        SIGTERM and only dies to SIGKILL, which skips RTSP TEARDOWN and makes
        the camera reject the next session. Measured: draining lets it exit in
        ~90ms, not draining leaves it alive past 6s.
        """
        pipe = proc.stdout
        if pipe is None:
            return
        try:
            fd = pipe.fileno()
        except (ValueError, OSError):
            return
        end = time.monotonic() + deadline
        while proc.poll() is None and time.monotonic() < end:
            try:
                ready, _, _ = select.select([fd], [], [], 0.05)
            except (OSError, ValueError):
                return
            if not ready:
                continue
            try:
                if not os.read(fd, 1 << 16):
                    return
            except (BlockingIOError, OSError):
                return

    def _kill_proc(self) -> None:
        with self._proc_lock:
            proc, self._proc = self._proc, None
        if proc is None:
            return
        # Drain for the whole shutdown, from SIGTERM to reap.
        threading.Thread(
            target=self._drain_stdout, args=(proc, 3.0), daemon=True
        ).start()
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            try:
                proc.terminate()
            except OSError:
                pass
        try:
            proc.wait(timeout=1.2)
        except Exception:
            # SIGTERM was ignored (usually ffmpeg stuck on the audio write):
            # remember it so stop_stream() runs the wider cleanup.
            self._hard_kill = True
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=1.0)
            except Exception:
                pass
