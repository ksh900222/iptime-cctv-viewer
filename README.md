# iptime C500 CCTV Viewer (macOS)

One-window PyQt6 viewer for **iptime C500** (and similar ONVIF/RTSP) cameras on a Mac:

- Live **HEVC** video over RTSP/TCP (`ffmpeg` + VideoToolbox)
- Live **audio** from the **same** ffmpeg session (FIFO → Qt `QAudioSink`) — dual RTSP clients starve audio on these cams
- **PTZ** via a separate RTSP control socket (`SETUP track1` + `ptzCmd`)
- Smooth volume slider (does not restart the stream)
- Fast camera switching (clean ffmpeg teardown / RTSP TEARDOWN)

Built and tested on Apple Silicon Mac mini with Homebrew ffmpeg.

## Requirements

- macOS (Apple Silicon recommended)
- Python 3.12+
- [Homebrew](https://brew.sh) `ffmpeg` with VideoToolbox
- PyQt6

```bash
brew install ffmpeg
python3 -m pip install --user -r requirements.txt
```

## Setup

```bash
git clone https://github.com/ksh900222/iptime-cctv-viewer.git
cd iptime-cctv-viewer
cp config.example.py config.py
# edit config.py: USERNAME, PASSWORD, camera names/IPs
python3 app.py
```

Or double-click `CCTV뷰어.command` after editing `config.py`.

`config.py` is gitignored. Never commit real passwords.

## Controls

| Input | Action |
|------|--------|
| Camera buttons / `1` `2` | Switch camera |
| Arrow keys or on-screen pad | Hold to move PTZ, release = STOP; short tap ≈ nudge |
| `Space` / STOP | Stop PTZ |
| Volume slider | Live gain (no reconnect) |
| `S` / Snapshot | Save JPG under `SNAPSHOT_DIR` |

## Architecture notes

- **One RTSP AV client** per camera for media. Opening a second ffmpeg/VLC on the same cam often yields silent audio.
- Audio makeup gain is fixed in ffmpeg (`volume=40`); the UI volume only changes `QAudioSink.setVolume`.
- See `REVIEW_STABILITY.md` for measured switch latency and teardown fixes.

## License

MIT — use and share freely. Cameras and accounts are yours; keep credentials out of git.
