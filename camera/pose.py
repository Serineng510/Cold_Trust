"""Person detection for the cargo-hold camera: MediaPipe pose + hand landmarks, drawn as a skeleton overlay.

Reports how many people are visible and simple pose cues ("hands raised", "reaching", "crouching",
"hand in view"). It never identifies anyone: the face points are pose landmarks (eyes, nose, mouth corners),
not face recognition. Falls back to OpenCV's HOG person detector if MediaPipe or its models are unavailable.
"""
from __future__ import annotations

import os
import time

os.environ.setdefault("GLOG_minloglevel", "2")       # keep MediaPipe's start-up chatter out of the console
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

MODELS = {  # official MediaPipe model files (downloaded once into camera/models/)
    "pose": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    "hand": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
}
MODEL_DIR = Path(__file__).with_name("models")

GREEN = (110, 214, 92)      # BGR, like the reference overlay
POINT = (60, 235, 120)
RED = (60, 60, 230)
WHITE = (245, 245, 245)
PANEL = (32, 32, 32)
VIS = 0.5                   # landmark visibility needed to draw / reason about it


@dataclass
class Detection:
    persons: int = 0
    hands: int = 0
    behaviours: list[str] = field(default_factory=list)
    poses: list = field(default_factory=list)        # MediaPipe pose landmarks per person
    hand_marks: list = field(default_factory=list)   # MediaPipe hand landmarks per hand
    boxes: list = field(default_factory=list)        # HOG fallback rectangles

    @property
    def present(self) -> bool:
        return self.persons > 0 or self.hands > 0


def ensure_models(verbose: bool = True) -> dict[str, Path]:
    MODEL_DIR.mkdir(exist_ok=True)
    paths = {}
    for name, url in MODELS.items():
        path = MODEL_DIR / url.rsplit("/", 1)[-1]
        if not path.exists():
            if verbose:
                print(f"Downloading the MediaPipe {name} model (one time) from storage.googleapis.com ...")
            tmp = path.with_suffix(".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(path)
            if verbose:
                print(f"  saved {path.name} ({path.stat().st_size / 1e6:.1f} MB)")
        paths[name] = path
    return paths


def _behaviours(lm) -> list[str]:
    """Pose cues from one person's 33 normalised landmarks (y grows downwards)."""
    def ok(*idx):
        return all((lm[i].visibility or 0) >= VIS for i in idx)
    out = []
    if ok(11, 12):
        shoulder_y = min(lm[11].y, lm[12].y)
        mid_x = (lm[11].x + lm[12].x) / 2
        width = abs(lm[11].x - lm[12].x) or 0.01
        wrists = [w for w in (15, 16) if ok(w)]
        if any(lm[w].y < shoulder_y - 0.02 for w in wrists):
            out.append("hands raised")
        if any(abs(lm[w].x - mid_x) > 1.6 * width for w in wrists):
            out.append("reaching")
        if ok(23, 24, 25, 26):
            hip_y = (lm[23].y + lm[24].y) / 2
            knee_y = (lm[25].y + lm[26].y) / 2
            torso = max(hip_y - (lm[11].y + lm[12].y) / 2, 0.01)
            if knee_y - hip_y < 0.35 * torso:
                out.append("crouching")
    return out


class PoseDetector:
    """MediaPipe Tasks: PoseLandmarker (up to 4 people) + HandLandmarker (up to 4 hands), video mode."""

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision
        paths = ensure_models()
        self.mp, self.vision = mp, vision
        mode = vision.RunningMode.VIDEO
        self.pose = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(paths["pose"])), running_mode=mode, num_poses=4))
        self.hand = vision.HandLandmarker.create_from_options(vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(paths["hand"])), running_mode=mode, num_hands=4))
        self.pose_lines = [(c.start, c.end) for c in vision.PoseLandmarksConnections.POSE_LANDMARKS
                           if c.start > 10 and c.end > 10]           # face drawn as points only
        self.hand_lines = [(c.start, c.end) for c in vision.HandLandmarksConnections.HAND_CONNECTIONS]
        self._last_ms = 0
        self.name = "MediaPipe pose + hands"

    def detect(self, frame: np.ndarray) -> Detection:
        ms = max(int(time.monotonic() * 1000), self._last_ms + 1)   # timestamps must strictly increase
        self._last_ms = ms
        img = self.mp.Image(image_format=self.mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        poses = self.pose.detect_for_video(img, ms).pose_landmarks
        hands = self.hand.detect_for_video(img, ms).hand_landmarks
        cues: list[str] = []
        for lm in poses:
            cues += [c for c in _behaviours(lm) if c not in cues]
        if hands and not poses:
            cues.append("hand in view")
        return Detection(persons=len(poses), hands=len(hands), behaviours=cues, poses=poses, hand_marks=hands)

    def draw(self, frame: np.ndarray, det: Detection) -> None:
        h, w = frame.shape[:2]
        px = lambda p: (int(p.x * w), int(p.y * h))
        for lm in det.poses:
            for a, b in self.pose_lines:
                if (lm[a].visibility or 0) >= VIS and (lm[b].visibility or 0) >= VIS:
                    cv2.line(frame, px(lm[a]), px(lm[b]), GREEN, 2, cv2.LINE_AA)
            for i in range(0, 11):                                   # eyes, nose, mouth corners
                if (lm[i].visibility or 0) >= VIS:
                    cv2.circle(frame, px(lm[i]), 3, POINT, -1, cv2.LINE_AA)
            for i in range(11, 33):
                if (lm[i].visibility or 0) >= VIS:
                    cv2.circle(frame, px(lm[i]), 3, GREEN, -1, cv2.LINE_AA)
        for lm in det.hand_marks:
            for a, b in self.hand_lines:
                cv2.line(frame, px(lm[a]), px(lm[b]), GREEN, 2, cv2.LINE_AA)
            for p in lm:
                cv2.circle(frame, px(p), 3, POINT, -1, cv2.LINE_AA)


class HogDetector:
    """Fallback: OpenCV's built-in HOG pedestrian detector (boxes only, full bodies only)."""

    def __init__(self):
        self.hog = cv2.HOGDescriptor()
        self.hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self.name = "OpenCV HOG (fallback)"

    def detect(self, frame: np.ndarray) -> Detection:
        scale = 640 / frame.shape[1]
        small = cv2.resize(frame, (640, int(frame.shape[0] * scale)))
        rects, weights = self.hog.detectMultiScale(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), winStride=(8, 8))
        boxes = [tuple(int(v / scale) for v in r) for r, wgt in zip(rects, weights) if float(wgt) > 0.5]
        return Detection(persons=len(boxes), boxes=boxes)

    def draw(self, frame: np.ndarray, det: Detection) -> None:
        for (x, y, bw, bh) in det.boxes:
            cv2.rectangle(frame, (x, y), (x + bw, y + bh), GREEN, 2)


def make_detector():
    try:
        return PoseDetector()
    except Exception as exc:  # no mediapipe, no network for the model, unsupported platform
        print(f"MediaPipe unavailable ({exc}); using the HOG fallback.")
        return HogDetector()


def label_box(frame: np.ndarray, det: Detection, alert: str | None, cues: list[str] | None = None) -> None:
    """Top-left status box, like the reference: dark panel, coloured text. `cues` overrides det.behaviours."""
    cues = det.behaviours if cues is None else cues
    if alert:
        title, color = alert, RED
    elif det.present:
        n = max(det.persons, 1)
        title, color = f"PERSON x{n}", GREEN
    else:
        title, color = "NO PERSON", WHITE
    lines = [title] + ([", ".join(cues)] if cues else [])
    font, y = cv2.FONT_HERSHEY_SIMPLEX, 12
    width = max(cv2.getTextSize(t, font, 0.62 if i == 0 else 0.5, 2)[0][0] for i, t in enumerate(lines)) + 24
    cv2.rectangle(frame, (10, 10), (10 + width, 18 + 26 * len(lines)), PANEL, -1)
    for i, t in enumerate(lines):
        y += 26
        cv2.putText(frame, t, (22, y), font, 0.62 if i == 0 else 0.5, color if i == 0 else WHITE, 2 if i == 0 else 1,
                    cv2.LINE_AA)
    stamp = time.strftime("REC %H:%M:%S")
    (tw, _), _ = cv2.getTextSize(stamp, font, 0.5, 1)
    cv2.putText(frame, stamp, (frame.shape[1] - tw - 14, 28), font, 0.5, RED, 1, cv2.LINE_AA)
