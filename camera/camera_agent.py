"""ColdTrust camera agent for the Logitech C270 (runs on the laptop the camera is plugged into).

What it does, continuously:
- Detects people with MediaPipe pose + hand landmarks and draws the skeleton overlay.
- Streams the annotated video to the dashboard (POST /api/camera/frame, about 5 frames per second).
- Tells the backend when a person appears, leaves, or does something new (POST /api/camera/observation).
  The backend decides what it means: handling at an authorised stop, a SUSPICIOUS_PERSON alert elsewhere,
  or theft if the hold is lit at the same time.
- When the door opens (light event) or a person alert fires, it saves one picture of what it detected
  (with the overlay) to /api/evidence; the backend stores it with the time and location and SHA-256 hashes it.

It reports "a person" and pose cues only; it never identifies anyone. Tell drivers and handlers the hold is recorded.

    .venv\\Scripts\\python -m camera.camera_agent --url http://127.0.0.1:8000 --show
    .venv\\Scripts\\python -m camera.camera_agent --source photo.jpg      # test without a camera (image or video file)
"""
from __future__ import annotations

import argparse
import base64
import collections
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import cv2

from camera.pose import Detection, label_box, make_detector

TRIGGER_CODES = {"LIGHT_BRIGHT"}
TRIGGER_SCENARIOS = {"DOOR_AJAR", "THEFT_SUSPECTED", "AUTHORISED_HANDLING", "SPOOF_SUSPECTED", "SUSPICIOUS_PERSON"}
ALERT_KINDS = {"SUSPICIOUS_PERSON", "THEFT_SUSPECTED"}
COOLDOWN_S = 20           # one picture per incident, not one per ping
POLL_S = 1.0
SEEN_FRAMES, OF_LAST = 3, 5   # "present" when seen in 3 of the last 5 analysed frames
GONE_S = 3.0              # "left" after this long without a detection
HEARTBEAT_S = 15          # re-send the observation while someone stays in view
LINGER_S = 20


def is_trigger(entry: dict) -> bool:
    """A backend audit entry that should produce camera evidence (door open or a person alert)."""
    codes = {f.get("code") for f in entry.get("findings") or []}
    return bool(codes & TRIGGER_CODES) or entry.get("scenario") in TRIGGER_SCENARIOS


class Backend:
    def __init__(self, base: str, key: str):
        self.base, self.key = base.rstrip("/"), key

    def call(self, method: str, path: str, body: dict | bytes | None = None, ctype: str = "application/json",
             timeout: float = 10):
        data = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Content-Type": ctype, "X-ColdTrust-Key": self.key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                print("  backend refused the camera key: COLDTRUST_SECRET in cold-chain/.env must match --key")
            return None
        except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError):
            return None


class Source:
    """Webcam index, or a video/image file for testing without the camera."""

    def __init__(self, source: str):
        self.still = None
        if source.isdigit():
            idx = int(source)
            self.cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(idx)
            self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
            self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        elif Path(source).suffix.lower() in (".jpg", ".jpeg", ".png", ".bmp"):
            self.still, self.cap = cv2.imread(source), None
        else:
            self.cap = cv2.VideoCapture(source)
        self.source = source

    def read(self):
        if self.still is not None:
            return self.still.copy()
        ok, frame = self.cap.read()
        if not ok and not self.source.isdigit():          # loop a test video
            self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self.cap.read()
        return frame if ok else None

    def release(self):
        if self.cap is not None:
            self.cap.release()


def jpeg(frame, width: int | None = None, quality: int = 80) -> bytes:
    if width and frame.shape[1] > width:
        frame = cv2.resize(frame, (width, int(frame.shape[0] * width / frame.shape[1])))
    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="http://127.0.0.1:8000", help="ColdTrust backend")
    ap.add_argument("--source", default="0", help="camera index (0, 1...) or an image/video file for testing")
    ap.add_argument("--cam", help="same as --source with a camera index (kept for older commands)")
    ap.add_argument("--fps", type=int, default=10, help="frames analysed per second")
    ap.add_argument("--stream-fps", type=int, default=5, help="annotated frames sent to the dashboard per second")
    ap.add_argument("--key", help="device key (default: COLDTRUST_SECRET from cold-chain/.env)")
    ap.add_argument("--show", action="store_true", help="also show a local preview window (q to quit)")
    ap.add_argument("--once", action="store_true", help="analyse a single frame, save camera/annotated.jpg, exit")
    a = ap.parse_args(argv)
    if a.cam is not None:
        a.source = a.cam
    if a.key is None:
        from backend.config import load
        a.key = load().device_secret

    src = Source(a.source)
    frame = src.read()
    if frame is None:
        print(f"No image from {a.source}. For a camera, run `python -m camera.cam_test` first.")
        return 1
    detector = make_detector()
    print(f"Detector: {detector.name}")

    if a.once:
        det = detector.detect(frame)
        detector.draw(frame, det)
        label_box(frame, det, None)
        out = Path(__file__).with_name("annotated.jpg")
        cv2.imwrite(str(out), frame)
        print(f"persons={det.persons} hands={det.hands} cues={det.behaviours} -> {out}")
        return 0

    api = Backend(a.url, a.key)
    audit = api.call("GET", "/api/audit?limit=50")
    if audit is None:
        print(f"Cannot reach the backend at {a.url}.")
        return 1
    last_id = max((e["id"] for e in audit), default=0)
    print(f"Watching {a.source}; streaming to {a.url}/api/camera/stream. Ctrl+C to stop.")

    seen = collections.deque(maxlen=OF_LAST)
    present, present_since, last_seen = False, 0.0, 0.0
    last_obs_cues: list[str] = []
    last_obs = last_poll = last_push = last_burst = 0.0
    snapshot_for: str | None = None           # alert kind waiting for its evidence picture
    alert_label, alert_until = None, 0.0     # red label: at least 30 s, and for as long as that person stays

    try:
        while True:
            t0 = time.time()
            frame = src.read()
            if frame is None:
                print("Camera stopped delivering frames.")
                return 1
            now = time.time()

            det: Detection = detector.detect(frame)
            seen.append(det.present)
            if det.present:
                last_seen = now
            cues = list(det.behaviours)
            if present and now - present_since >= LINGER_S:
                cues.append("lingering")

            # presence changes, new cues, heartbeat -> backend observation
            became = not present and sum(seen) >= SEEN_FRAMES
            left = present and now - last_seen > GONE_S
            if became:
                present, present_since = True, now
            if left:
                present = False
                alert_label = None if now >= alert_until else alert_label
            if became or left or (present and (set(cues) - set(last_obs_cues) or now - last_obs >= HEARTBEAT_S)):
                last_obs, last_obs_cues = now, cues
                res = api.call("POST", "/api/camera/observation", {
                    "person_present": present, "persons": max(det.persons, 1 if det.hands else 0) if present else 0,
                    "behaviours": cues if present else [], "source": "pose",
                    "description": f"{det.persons} person(s) visible" if present else "No person visible"})
                entry = (res or {}).get("entry")
                if entry:
                    print(f"{time.strftime('%H:%M:%S')} {entry['kind']}: {entry['reasoning']}")

            # backend alerts: red label + evidence picture
            if now - last_poll >= POLL_S:
                last_poll = now
                for e in [e for e in (api.call("GET", "/api/audit?limit=20") or []) if e["id"] > last_id]:
                    last_id = max(last_id, e["id"])
                    if e["kind"] in ALERT_KINDS:          # red label for the alert, whether or not it sent a message
                        alert_label, alert_until = e["kind"].replace("_", " "), now + 30
                    if is_trigger(e) and now - last_burst >= COOLDOWN_S:
                        last_burst = now
                        snapshot_for = e["kind"]

            # annotated live frame -> dashboard
            view = frame.copy()
            detector.draw(view, det)
            label_box(view, det, alert_label if (now < alert_until or present) else None, cues if present else [])
            if snapshot_for:                       # the picture shows exactly what was detected
                res = api.call("POST", "/api/evidence", {"device_id": "CAM-01", "trigger": snapshot_for,
                                                         "jpeg_base64": base64.b64encode(jpeg(view, 1280, 85)).decode()})
                if res:
                    print(f"{time.strftime('%H:%M:%S')} {snapshot_for}: picture saved ({res['stored']})")
                snapshot_for = None
            if now - last_push >= 1 / max(a.stream_fps, 1):
                last_push = now
                q = urllib.parse.urlencode({"persons": max(det.persons, 1 if det.hands else 0),
                                            "labels": ",".join(cues)})
                api.call("POST", f"/api/camera/frame?{q}", jpeg(view, 960, 72), "image/jpeg", timeout=5)
            if a.show:
                cv2.imshow("ColdTrust camera (q to quit)", view)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            time.sleep(max(0.0, 1 / a.fps - (time.time() - t0)))
    except KeyboardInterrupt:
        pass
    finally:
        src.release()
        cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
