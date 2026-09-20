# iptime C500 CCTV Viewer (macOS)

One-window PyQt6 viewer for **iptime C500** (and similar ONVIF/RTSP) cameras on a Mac:

- Live **HEVC** video over RTSP/TCP (`ffmpeg` + VideoToolbox)
- Live **audio** from the **same** ffmpeg session (FIFO → Qt `QAudioSink`) — dual RTSP clients starve audio on these cams
- **PTZ** via a separate RTSP control socket (`SETUP track1` + `ptzCmd`)
- Smooth volume slider (does not restart the stream)
- Live-view rotate buttons (`↺ 90°` / `↻ 90°`) — display-only, does not restart ffmpeg
- Digital **zoom / pan** on the live view (mouse wheel, drag, `+` / `−` / `원본(1x)`) — display-only, does not restart ffmpeg
- Fast camera switching (clean ffmpeg teardown / RTSP TEARDOWN)

Built and tested on Apple Silicon Mac mini with Homebrew ffmpeg.


## Launchers

- `CCTV뷰어.command` — macOS double-click shell script (opens Terminal, runs the viewer, **closes that Terminal window when the viewer exits**).
- Prefer `CCTV뷰어.app` when present (custom icon, no Terminal window).

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
| `↺ 90°` / `↻ 90°` | Rotate the live view −90° / +90°. One integer state in `{0, 90, 180, 270}` (modulo 360); four clicks in one direction return to the original orientation. Does not restart ffmpeg/RTSP. Session-only (not written to disk). Snapshots use the same orientation. |
| Mouse wheel over video | Zoom in/out around the cursor (1.0× … 8.0×). Display-path only; ffmpeg/RTSP stays up. |
| Click-drag on video | Pan the zoomed view. Offsets clamp so the frame never scrolls into empty space. At 1.0×, pan is a no-op. |
| `+` / `−` | Zoom in/out around the view center. Same bounds as the wheel. |
| `원본(1x)` / double-click video | Reset zoom and pan to 1.0×, centered. |
| `S` / Snapshot | Save JPG under `SNAPSHOT_DIR`. Full uncropped frame (current rotation); **not** the zoomed crop. |

## Architecture notes

- **One RTSP AV client** per camera for media. Opening a second ffmpeg/VLC on the same cam often yields silent audio.
- Audio makeup gain is fixed in ffmpeg (`volume=40`); the UI volume only changes `QAudioSink.setVolume`.
- See `REVIEW_STABILITY.md` for measured switch latency and teardown fixes.

## License

MIT — use and share freely. Cameras and accounts are yours; keep credentials out of git.
