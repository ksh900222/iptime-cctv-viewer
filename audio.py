"""Live PCM from VideoWorker's FIFO → QAudioSink. Slider = sink volume only."""

from __future__ import annotations

import array
import logging
import os
import select
import threading
from typing import Optional

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtMultimedia import QAudio, QAudioFormat, QAudioSink, QMediaDevices

log = logging.getLogger("cctv.audio")
SAMPLE_RATE = 48000


class AudioWorker(QObject):
    status = pyqtSignal(str)

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._volume = 1.0
        self._enabled = True
        self._fifo = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sink: Optional[QAudioSink] = None
        self._io = None
        self._channels = 1
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._pump = QTimer(self)
        self._pump.setInterval(10)
        self._pump.timeout.connect(self._push)

    def start(self) -> None:
        return

    def play(self, url: str) -> None:
        # URL ignored; PCM comes from video ffmpeg FIFO.
        return

    def start_pcm(self, fifo_path: str) -> None:
        """Begin reading s16le mono @48k from fifo written by VideoWorker."""
        self.stop_pcm()
        self._fifo = fifo_path
        sink_ok = self._open_sink()
        if sink_ok:
            self._apply_sink_volume()
        # Fresh event per session: a reader that outlived stop_pcm() keeps its
        # own (already set) event and can never write into the new buffer.
        stop = threading.Event()
        self._stop = stop
        # Start the reader even with no sink: it is the FIFO's only consumer,
        # and a full FIFO blocks ffmpeg's muxer, which freezes video as well.
        self._thread = threading.Thread(
            target=self._read_loop, args=(fifo_path, stop), name="cctv-pcm", daemon=True
        )
        self._thread.start()
        self.status.emit("오디오 재생 중" if sink_ok else "오디오 장치 열기 실패 (영상만 재생)")

    def stop_pcm(self) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
            if thread.is_alive():
                log.warning("pcm reader did not stop in time; abandoned")
        with self._lock:
            self._buf.clear()

    def stop_stream(self) -> None:
        self.stop_pcm()

    def shutdown(self) -> None:
        self.stop_pcm()
        self._close_sink()

    def set_volume(self, linear: float) -> None:
        """Smooth volume: only touches QAudioSink, never restarts ffmpeg."""
        self._volume = max(0.0, min(1.0, float(linear)))
        self._apply_sink_volume()

    def set_enabled(self, on: bool) -> None:
        self._enabled = bool(on)
        self._apply_sink_volume()

    def beep(self, *args, **kwargs) -> None:
        return

    def _apply_sink_volume(self) -> None:
        if self._sink is None:
            return
        if not self._enabled or self._volume <= 0.001:
            self._sink.setVolume(0.0)
            return
        # Perceptual curve for the slider.
        vol = QAudio.convertVolume(
            self._volume,
            QAudio.VolumeScale.LogarithmicVolumeScale,
            QAudio.VolumeScale.LinearVolumeScale,
        )
        self._sink.setVolume(max(0.0, min(1.0, vol)))

    def _open_sink(self) -> bool:
        if self._sink is not None:
            # A sink only reaches StoppedState on a fatal device error (e.g. the
            # output was unplugged). Rebuild instead of feeding a dead sink.
            if self._sink.state() != QAudio.State.StoppedState:
                return True
            log.warning("audio sink stopped (%s); reopening", self._sink.error())
            self._close_sink()
        dev = QMediaDevices.defaultAudioOutput()
        if dev is None or dev.isNull():
            return False
        fmt = QAudioFormat()
        fmt.setSampleRate(SAMPLE_RATE)
        fmt.setChannelCount(1)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
        if not dev.isFormatSupported(fmt):
            fmt.setChannelCount(2)
            if not dev.isFormatSupported(fmt):
                return False
        self._channels = fmt.channelCount()
        sink = QAudioSink(dev, fmt, self)
        sink.setBufferSize(SAMPLE_RATE * 2 * self._channels // 2)  # ~500ms
        self._io = sink.start()
        if self._io is None:
            sink.deleteLater()
            return False
        self._sink = sink
        self._pump.start()
        log.info("QAudioSink %s %dch", dev.description(), self._channels)
        return True

    def _close_sink(self) -> None:
        self._pump.stop()
        sink, self._sink = self._sink, None
        self._io = None
        if sink is not None:
            try:
                sink.stop()
            except Exception:
                pass
            sink.deleteLater()

    def _read_loop(self, path: str, stop: threading.Event) -> None:
        try:
            # O_NONBLOCK + select so stop_pcm() never has to wait for ffmpeg
            # to produce the next audio packet.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as e:
            log.warning("fifo open: %s", e)
            self.status.emit(f"오디오 FIFO 실패: {e}")
            return
        channels = self._channels
        cap = SAMPLE_RATE * 2 * channels  # ~1s of latency
        odd = b""
        try:
            while not stop.is_set():
                try:
                    ready, _, _ = select.select([fd], [], [], 0.1)
                except (OSError, ValueError):
                    break
                if not ready:
                    continue
                try:
                    data = os.read(fd, 8192)
                except BlockingIOError:
                    continue
                except OSError as e:
                    log.warning("fifo read: %s", e)
                    break
                if not data:
                    if stop.wait(0.05):
                        break
                    continue
                # Upmix mono→stereo if needed (carry a stray odd byte so the
                # s16le stream never loses frame alignment).
                if channels > 1:
                    data = odd + data
                    usable = len(data) - len(data) % 2
                    odd = data[usable:]
                    mono = array.array("h")
                    mono.frombytes(data[:usable])
                    out = array.array("h", bytes(len(mono) * 4))
                    out[0::2] = mono
                    out[1::2] = mono
                    data = out.tobytes()
                with self._lock:
                    # stop_pcm() may have abandoned this reader after its join
                    # timeout; never let stale audio bleed into the new session.
                    if stop.is_set() or self._stop is not stop:
                        break
                    self._buf += data
                    if len(self._buf) > cap:
                        del self._buf[: len(self._buf) - cap]
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def _push(self) -> None:
        sink, io = self._sink, self._io
        if sink is None or io is None:
            return
        free = sink.bytesFree()
        frame = 2 * self._channels
        if free < frame:
            return
        with self._lock:
            n = min(free, len(self._buf))
            n -= n % frame
            if n <= 0:
                return
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
        io.write(chunk)
