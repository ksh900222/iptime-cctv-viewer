"""iptime C500GS / Yoosee HIipCamera PTZ over a dedicated RTSP socket.

Verified on **iptime C500GS** (거실 192.168.0.28, 안방 192.168.0.27):
  Digest DESCRIBE 200 → SETUP track1 (RTP/AVP/TCP interleaved) → Session
  → SET_PARAMETER  Content-type: ptzCmd: DIR
  (tilt down must be DWON — C500GS firmware typo; DOWN is ignored)
                   Content-Length: len(that Content-type value)

Bare SET_PARAMETER without SETUP often returns 200 but does not move motors.
Video PLAY stays on a separate ffmpeg/VLC connection.
"""

from __future__ import annotations

import hashlib
import logging
import re
import socket
import threading
import time
from typing import Optional

from config import PASSWORD, USERNAME

log = logging.getLogger("cctv.ptz")

USER_AGENT = "C500Viewer/1.0"
CONNECT_TIMEOUT = 5.0
RECV_TIMEOUT = 4.0

DIRS = ("LEFT", "RIGHT", "UP", "DOWN", "STOP")

# C500GS (Yoosee / HIipCamera) accepts tilt-down as the misspelling "DWON",
# not "DOWN" — verified on C500GS. UI/remap keep logical DOWN; wire token differs.
_WIRE_DIR = {"DOWN": "DWON"}

# Clockwise compass. Visual dir → camera dir is (idx - n) % 4
# where n = (rotation_deg // 90) % 4 (Qt +rotate = CW on the displayed image).
#
# visual | n=0 | n=1 (+90 CW) | n=2 (+180) | n=3 (+270 CW)
# UP     | UP  | LEFT         | DOWN       | RIGHT
# RIGHT  | RIGHT| UP          | LEFT       | DOWN
# DOWN   | DOWN | RIGHT       | UP         | LEFT
# LEFT   | LEFT | DOWN        | RIGHT      | UP
_PTZ_COMPASS = ("UP", "RIGHT", "DOWN", "LEFT")


def remap_ptz_dir(visual_dir: str, rotation_deg: int) -> str:
    """Map on-screen PTZ direction to the camera-native command.

    After a digital clockwise rotation of n*90°, a visual arrow must send the
    camera command that moves the *picture* in that visual direction.
    Always uses ``// 90 % 4`` — never a growing click counter.
    STOP and unknown tokens pass through unchanged.
    """
    d = visual_dir.upper().strip()
    if d == "STOP":
        return "STOP"
    try:
        idx = _PTZ_COMPASS.index(d)
    except ValueError:
        return visual_dir
    n = (int(rotation_deg) // 90) % 4
    return _PTZ_COMPASS[(idx - n) % 4]


def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _digest(method: str, uri: str, realm: str, nonce: str) -> str:
    ha1 = _md5(f"{USERNAME}:{realm}:{PASSWORD}")
    ha2 = _md5(f"{method}:{uri}")
    resp = _md5(f"{ha1}:{nonce}:{ha2}")
    return (
        f'Digest username="{USERNAME}", realm="{realm}", '
        f'nonce="{nonce}", uri="{uri}", response="{resp}"'
    )


class PtzError(Exception):
    pass


class PtzClient:
    """One RTSP TCP connection per camera. Not safe to share across threads
    unless the caller holds `lock` (the worker thread owns that)."""

    def __init__(self, ip: str) -> None:
        self.ip = ip
        self.base_uri = f"rtsp://{ip}:554/onvif1"
        self.track_uri = f"{self.base_uri}/track1"
        self.sock: Optional[socket.socket] = None
        self.cseq = 0
        self.realm: Optional[str] = None
        self.nonce: Optional[str] = None
        self.session: Optional[str] = None
        self.lock = threading.Lock()
        self.last_status = ""

    def connected(self) -> bool:
        return self.sock is not None and bool(self.session)

    def connect(self) -> str:
        self.close()
        self.sock = socket.create_connection((self.ip, 554), timeout=CONNECT_TIMEOUT)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.cseq = 0
        self.realm = self.nonce = self.session = None

        r = self._request("DESCRIBE", self.base_uri, extra=["Accept: application/sdp"], auth=False)
        if r.status == 401:
            r = self._request("DESCRIBE", self.base_uri, extra=["Accept: application/sdp"], auth=True)
        if r.status != 200:
            raise PtzError(f"DESCRIBE {r.status} {r.reason}".strip())

        r = self._request(
            "SETUP",
            self.track_uri,
            extra=["Transport: RTP/AVP/TCP;unicast;interleaved=0-1"],
            auth=True,
            digest_uri=self.track_uri,
        )
        if r.status == 401:
            r = self._request(
                "SETUP",
                self.track_uri,
                extra=["Transport: RTP/AVP/TCP;unicast;interleaved=0-1"],
                auth=True,
                digest_uri=self.base_uri,
            )
        if r.status != 200 or not self.session:
            raise PtzError(f"SETUP {r.status} {r.reason} session={self.session!r}".strip())

        msg = f"PTZ 세션 {self.session} @ {self.ip}"
        self.last_status = msg
        return msg

    def command(self, direction: str) -> str:
        direction = direction.upper().strip()
        if direction not in DIRS:
            raise PtzError(f"unknown dir {direction}")
        if self.sock is None:
            self.connect()
        wire = _WIRE_DIR.get(direction, direction)
        ct = f"ptzCmd: {wire}"
        r = self._set_parameter(ct)
        if r.status in (0, 401) or r.status >= 400:
            # nonce/session lost — full SETUP again, then retry once
            self.connect()
            r = self._set_parameter(ct)
        if r.status != 200:
            r = self._user_cmd_set(direction)
        if r.status != 200:
            raise PtzError(f"{direction} → {r.status} {r.reason}".strip())
        shown = direction if wire == direction else f"{direction}({wire})"
        msg = f"{shown} → {r.status} {r.reason} sess={self.session}"
        self.last_status = msg
        return msg

    def stop(self) -> str:
        try:
            return self.command("STOP")
        except Exception as e:
            self.last_status = f"STOP 실패: {e}"
            return self.last_status

    def keepalive(self) -> None:
        if self.sock is None or not self.session:
            return
        r = self._request("GET_PARAMETER", self.base_uri, auth=True)
        if r.status != 200:
            self.connect()

    def close(self) -> None:
        sock = self.sock
        if sock is None:
            self.session = None
            return
        try:
            if self.session:
                sock.settimeout(0.6)
                self._request("TEARDOWN", self.base_uri, auth=True)
        except Exception:
            pass
        try:
            sock.close()
        except OSError:
            pass
        self.sock = None
        self.session = None

    # ----- wire ----------------------------------------------------------

    class Resp:
        def __init__(self, raw: str) -> None:
            self.raw = raw
            self.status = 0
            self.reason = ""
            first = raw.split("\r\n", 1)[0] if raw else ""
            m = re.match(r"RTSP/\d\.\d\s+(\d{3})\s*(.*)", first)
            if m:
                self.status = int(m.group(1))
                self.reason = m.group(2).strip()
            self.headers: dict[str, str] = {}
            head = raw.split("\r\n\r\n", 1)[0]
            for line in head.split("\r\n")[1:]:
                if ":" in line:
                    k, v = line.split(":", 1)
                    self.headers[k.strip().lower()] = v.strip()

    def _set_parameter(self, content_type: str) -> "PtzClient.Resp":
        # Yoosee: command lives in Content-type; Content-Length is strlen of that value.
        return self._request(
            "SET_PARAMETER",
            self.base_uri,
            extra=[
                f"Content-type: {content_type}",
                f"Content-Length: {len(content_type)}",
            ],
            auth=True,
        )

    def _user_cmd_set(self, direction: str) -> "PtzClient.Resp":
        if direction == "STOP":
            cmd = "Stop"
        else:
            cmd = _WIRE_DIR.get(direction, direction)
        ct = f"ptzCmd: {cmd}"
        return self._request(
            "USER_CMD_SET",
            self.base_uri,
            extra=[
                "Content-length: strlen(Content-type)",
                f"Content-type: {ct}",
            ],
            auth=True,
        )

    def _request(
        self,
        method: str,
        uri: str,
        extra: Optional[list[str]] = None,
        auth: bool = True,
        digest_uri: Optional[str] = None,
    ) -> "PtzClient.Resp":
        if self.sock is None:
            return PtzClient.Resp("")
        self.cseq += 1
        lines = [
            f"{method} {uri} RTSP/1.0",
            f"CSeq: {self.cseq}",
            f"User-Agent: {USER_AGENT}",
        ]
        d_uri = digest_uri or uri
        if auth and self.realm and self.nonce:
            lines.append("Authorization: " + _digest(method, d_uri, self.realm, self.nonce))
        if self.session:
            lines.append(f"Session: {self.session}")
        if extra:
            lines.extend(extra)
        msg = "\r\n".join(lines) + "\r\n\r\n"
        log.debug(">>> %s", msg.replace("\r\n", " | "))
        try:
            self.sock.sendall(msg.encode("ascii"))
            raw = self._recv()
        except OSError as e:
            log.warning("rtsp io: %s", e)
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None
            return PtzClient.Resp("")
        resp = PtzClient.Resp(raw)
        log.debug("<<< %s %s", resp.status, resp.reason)
        www = resp.headers.get("www-authenticate", "")
        m = re.search(r'realm="([^"]+)".*nonce="([^"]+)"', www, re.S)
        if not m:
            m = re.search(r'realm="([^"]+)".*nonce="([^"]+)"', raw, re.S)
        if m:
            self.realm, self.nonce = m.group(1), m.group(2)
        sess = resp.headers.get("session", "")
        if sess:
            sid = sess.split(";", 1)[0].strip()
            if sid:
                self.session = sid
        return resp

    def _recv(self) -> str:
        assert self.sock is not None
        self.sock.settimeout(RECV_TIMEOUT)
        data = b""
        try:
            while True:
                chunk = self.sock.recv(16384)
                if not chunk:
                    break
                data += chunk
                if b"\r\n\r\n" in data:
                    hm = re.search(br"Content-Length:\s*(\d+)", data, re.I)
                    if not hm:
                        break
                    header, body = data.split(b"\r\n\r\n", 1)
                    need = int(hm.group(1))
                    while len(body) < need:
                        more = self.sock.recv(16384)
                        if not more:
                            break
                        body += more
                    data = header + b"\r\n\r\n" + body
                    break
        except socket.timeout:
            pass
        return data.decode("utf-8", "replace")


def refuse_anbang(ip: str) -> None:
    if ip.endswith(".27") or ip == "192.168.0.27":
        raise SystemExit("refuse: will not send PTZ to 안방 (.27) from CLI")


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.28"
    refuse_anbang(ip)
    hold = float(sys.argv[2]) if len(sys.argv) > 2 else 0.6
    c = PtzClient(ip)
    print(c.connect())
    try:
        print(c.command("LEFT"))
        time.sleep(hold)
        print(c.stop())
        time.sleep(0.3)
        print(c.command("RIGHT"))
        time.sleep(hold)
        print(c.stop())
    finally:
        c.close()
    print("done")
