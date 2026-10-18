"""Camera test: is the Logitech C270 seen, at what resolution and frame rate, and does a snapshot save?

    .venv\\Scripts\\python -m camera.cam_test            # camera 0, saves camera/cam_test.jpg
    .venv\\Scripts\\python -m camera.cam_test --cam 1    # try the next camera if 0 is the laptop's own webcam
    .venv\\Scripts\\python -m camera.cam_test --show     # live preview window; press q to close

Pass: prints a resolution (the C270 gives 640x480 or 1280x720), about 15-30 fps, and saves a sharp image.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2


def open_camera(index: int) -> cv2.VideoCapture:
    # DirectShow opens USB webcams quickly and reliably on Windows
    cap = cv2.VideoCapture(index, cv2.CAP_DSHOW) if sys.platform == "win32" else cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    return cap


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cam", type=int, default=0, help="camera index (0, 1, 2...)")
    ap.add_argument("--show", action="store_true", help="open a live preview window")
    a = ap.parse_args(argv)

    cap = open_camera(a.cam)
    if not cap.isOpened():
        print(f"Camera {a.cam} not found. Is the C270 plugged in? Try --cam 1.")
        return 1
    ok, frame = cap.read()
    if not ok:
        print("Camera opened but returned no image. Close other apps using it (Zoom, Teams, browser) and retry.")
        return 1
    h, w = frame.shape[:2]
    print(f"Camera {a.cam}")
    print(f"Resolution: {w}x{h}")

    start, frames = time.time(), 0
    while frames < 30:
        ok, frame = cap.read()
        frames += ok
    print(f"Frame rate: {frames / (time.time() - start):.1f} fps")

    out = Path(__file__).with_name("cam_test.jpg")
    cv2.imwrite(str(out), frame)
    print(f"Snapshot saved: {out}")

    if a.show:
        print("Preview open; press q in the window to close.")
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            cv2.imshow("ColdTrust camera test (q to quit)", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        cv2.destroyAllWindows()
    cap.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
