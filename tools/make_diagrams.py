"""Draw the two pitch diagrams (architecture and AI agent workflow) as SVG, then render slide-ready PNGs with Edge.

    .venv\\Scripts\\python -m tools.make_diagrams

Writes docs/diagrams/architecture.svg|png and docs/diagrams/agent_workflow.svg|png. The SVG files open in PowerPoint
(Insert > Pictures; right-click > Convert to Shape makes every box editable). Edit the content in this file and run it again.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "docs" / "diagrams"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
FONT = "'Segoe UI', Arial, Helvetica, sans-serif"

INK, MUTED = "#1f2933", "#52606d"
BLUE, BLUE_BG = "#2563eb", "#eaf2ff"          # hardware / inputs and AI agents
GRAY, GRAY_BG = "#4b5563", "#f3f4f6"          # rules (deterministic code)
GREEN, GREEN_BG = "#15803d", "#ecfdf3"        # people and outputs
AMBER, AMBER_BG = "#b45309", "#fff4d6"        # safeguards and gates
RED, RED_BG = "#b91c1c", "#fdecec"            # rejected / blocked


class Svg:
    def __init__(self, w: int, h: int):
        self.w, self.h, self.parts = w, h, []

    def add(self, s: str) -> None:
        self.parts.append(s)

    def text(self, x, y, s, size=17, weight="400", fill=INK, anchor="start", halo=False):
        halo_attr = ' stroke="#ffffff" stroke-width="7" paint-order="stroke" stroke-linejoin="round"' if halo else ""
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{halo_attr}>{escape(s)}</text>')

    def lines(self, x, y, items, size=16, fill=MUTED, gap=23, anchor="start", weight="400"):
        for i, s in enumerate(items):
            self.text(x, y + i * gap, s, size=size, fill=fill, anchor=anchor, weight=weight)

    def box(self, x, y, w, h, title, body=(), fill="#ffffff", stroke=INK, dashed=False, tsize=19, bsize=16,
            rx=14, num=None, center=False):
        dash = ' stroke-dasharray="9 7"' if dashed else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="2.2"{dash}/>')
        cx = x + w / 2 if center else x + 18
        anchor = "middle" if center else "start"
        ty = y + 31
        if num is not None:
            self.add(f'<circle cx="{x + 22}" cy="{y + 25}" r="13" fill="{stroke}"/>')
            self.text(x + 22, y + 31, str(num), size=15, weight="700", fill="#ffffff", anchor="middle")
            cx, anchor = x + 44, "start"
        self.text(cx, ty, title, size=tsize, weight="700", anchor=anchor)
        chars = max(10, int((w - 36) / (bsize * 0.52)))
        wrapped = [ln for s in body for ln in (textwrap.wrap(s, chars) or [""])]
        self.lines(x + w / 2 if center else x + 18, ty + 27, wrapped, size=bsize, anchor=anchor if not num else "start")

    def container(self, x, y, w, h, label, stroke=MUTED, fill="none", lsize=17):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="20" fill="{fill}" stroke="{stroke}" stroke-width="2" stroke-dasharray="3 7"/>')
        self.text(x + 20, y + 28, label, size=lsize, weight="700", fill=stroke)

    def arrow(self, pts, label=None, lx=None, ly=None, dashed=False, color=INK, both=False, anchor="middle", lsize=15):
        d = "M " + " L ".join(f"{px} {py}" for px, py in pts)
        dash = ' stroke-dasharray="8 6"' if dashed else ""
        start = ' marker-start="url(#head-r)"' if both else ""
        mid = f"head-{color[1:]}"
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.4"{dash} marker-end="url(#{mid})"{start.replace("head-r", mid)}/>')
        if label:
            mx = lx if lx is not None else (pts[0][0] + pts[-1][0]) / 2
            my = ly if ly is not None else (pts[0][1] + pts[-1][1]) / 2 - 8
            for i, ln in enumerate(label.split("\n")):
                self.text(mx, my + i * 19, ln, size=lsize, fill=color if color != INK else MUTED, anchor=anchor, halo=True)

    def person(self, cx, cy, color, label, sub=(), size=1.0, lsize=17):
        s = size
        self.add(f'<circle cx="{cx}" cy="{cy - 22 * s}" r="{15 * s}" fill="{color}"/>')
        self.add(f'<path d="M {cx - 30 * s} {cy + 22 * s} Q {cx - 30 * s} {cy - 2 * s} {cx} {cy - 2 * s} Q {cx + 30 * s} {cy - 2 * s} {cx + 30 * s} {cy + 22 * s} Z" fill="{color}"/>')
        self.text(cx, cy + 46 * s, label, size=lsize, weight="700", anchor="middle")
        self.lines(cx, cy + 46 * s + 23, sub, size=15, anchor="middle")

    def chip(self, x, y, s, color=AMBER, bg=AMBER_BG, size=14):
        w = len(s) * size * 0.56 + 20
        self.add(f'<rect x="{x}" y="{y - 17}" width="{w}" height="26" rx="13" fill="{bg}" stroke="{color}" stroke-width="1.5"/>')
        self.text(x + 10, y + 1, s, size=size, weight="600", fill=color)

    def render(self) -> str:
        colors = {INK: INK, BLUE: BLUE, GREEN: GREEN, AMBER: AMBER, RED: RED, GRAY: GRAY}
        markers = "".join(
            f'<marker id="head-{c[1:]}" viewBox="0 0 12 12" refX="10" refY="6" markerWidth="11" markerHeight="11" orient="auto-start-reverse">'
            f'<path d="M 1 1 L 11 6 L 1 11 z" fill="{c}"/></marker>' for c in colors)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w}" height="{self.h}" viewBox="0 0 {self.w} {self.h}" '
                f'font-family="{FONT}"><defs>{markers}</defs><rect width="100%" height="100%" fill="#ffffff"/>' + "".join(self.parts) + "</svg>")


# ====================================================================== architecture
def architecture() -> Svg:
    g = Svg(1720, 960)
    g.text(40, 52, "Architecture", size=32, weight="700")
    g.text(40, 82, "Frozen Warden: a cold-chain guardian that checks its own data before it acts", size=18, fill=MUTED)

    # ---- at a glance (left)
    g.text(40, 135, "At a glance", size=18, weight="700", fill=MUTED)
    g.box(40, 155, 230, 120, "Truck", ["sensors + camera", "in the cargo hold"], fill=BLUE_BG, stroke=BLUE, center=True)
    g.box(40, 395, 230, 140, "Frozen Warden", ["checks the data,", "decides status,", "AI explains + proposes"], fill=GRAY_BG, stroke=GRAY, center=True)
    g.box(40, 655, 230, 120, "People", ["admin approves,", "driver is alerted"], fill=GREEN_BG, stroke=GREEN, center=True)
    g.arrow([(155, 275), (155, 395)], "signed data", 170, 340, anchor="start")
    g.arrow([(155, 535), (155, 655)], "alerts + proposals", 170, 600, anchor="start")

    # ---- detail panel
    g.container(310, 100, 1370, 830, "", stroke=MUTED)
    g.text(334, 912, "Frozen Warden system in detail", size=17, weight="700", fill=MUTED)

    # inputs
    g.text(340, 148, "Inputs (the truck)", size=17, weight="700", fill=BLUE)
    g.box(340, 165, 300, 190, "Cargo-hold sensor node", ["NodeMCU ESP8266", "DHT11: temperature + humidity", "Light module: door open", "LEDs: local status",
                                                          "sends signed JSON over Wi-Fi"], fill=BLUE_BG, stroke=BLUE)
    g.box(340, 410, 300, 160, "Cargo camera", ["Logitech C270 (USB)", "MediaPipe: person + pose cues", "never identifies anyone",
                                               "sends sightings + frames"], fill=BLUE_BG, stroke=BLUE)
    g.box(340, 605, 300, 200, "Route and zone context", ["OSRM road route (OpenStreetMap)", "geofences: docks, risk stretch, backup cold store",
                                                          "position + speed (simulated in the prototype)"], fill=BLUE_BG, stroke=BLUE)

    # backend
    g.container(690, 120, 500, 790, "FastAPI backend + SQLite (runs locally)", stroke=GRAY)
    bx, bw = 715, 450
    g.box(bx, 160, bw, 92, "Integrity gate", ["signature · sequence · time window · replay"], fill=GRAY_BG, stroke=GRAY, num=1)
    g.box(bx, 282, bw, 92, "Seal trust report", ["8 checks: passed / suspicious / failed / not run"], fill=GRAY_BG, stroke=GRAY, num=2)
    g.box(bx, 404, bw, 100, "Rules engine", ["status · risk score · quarantine · custody tokens", "rules decide; the AI never changes a status"],
          fill=GRAY_BG, stroke=GRAY, num=3)
    g.box(bx, 534, bw, 112, "AI agents (Gemini API, free tier)", ["Thermal · Seal · Incident", "one call per alert; every number is checked", "against the data"],
          fill=BLUE_BG, stroke=BLUE, num=4)
    g.box(bx, 676, bw, 92, "Approvals queue", ["admin approves or rejects; driver is read-only"], fill=AMBER_BG, stroke=AMBER, num=5)
    g.box(bx, 798, bw, 92, "Actions + records", ["approved action runs; everything is logged"], fill=GRAY_BG, stroke=GRAY, num=6)
    for y0, y1 in ((252, 282), (374, 404), (504, 534), (646, 676), (768, 798)):
        g.arrow([(bx + bw / 2, y0), (bx + bw / 2, y1)])

    # inputs -> backend
    g.arrow([(640, 270), (715, 206)])
    g.arrow([(640, 490), (715, 462)])
    g.arrow([(640, 705), (715, 490)])

    # outputs
    g.text(1220, 148, "Outside the backend", size=17, weight="700", fill=GREEN)
    g.box(1220, 165, 430, 262, "Safeguards built in", ["Data trust: HMAC-signed readings, replay and spoof checks, evidence hashes",
                                                        "AI safety: every number checked, fixed action menu, rule text if the AI is off",
                                                        "Human oversight: admin-only approvals, driver is read-only",
                                                        "Accountability: login + lockout, audit log of every decision"],
          fill=AMBER_BG, stroke=AMBER)
    g.box(1220, 520, 430, 130, "Dashboard + Telegram", ["admin: approve, override, report", "driver: alerts, read-only",
                                                        "Telegram: one message per alert"], fill=GREEN_BG, stroke=GREEN)
    g.arrow([(1165, 590), (1220, 590)])
    g.person(1330, 718, GREEN, "Admin", ["human approval"], size=0.8)
    g.arrow([(1165, 722), (1285, 722)], None, both=True, color=GREEN)
    g.box(1220, 790, 430, 120, "Records", ["incident report (.docx) + evidence photos", "GS1 EPCIS 2.0 hash-ID events", "audit log of every decision"],
          fill=GREEN_BG, stroke=GREEN)
    g.arrow([(1165, 850), (1220, 850)])
    return g


# ====================================================================== agent workflow
def workflow() -> Svg:
    g = Svg(1720, 1000)
    g.text(40, 52, "AI agent workflow", size=32, weight="700")
    g.text(40, 82, "Rules decide the status. Agents explain and propose. A human approves anything disruptive.", size=18, fill=MUTED)

    # ---- top row: data path
    y, h, w = 115, 130, 330
    xs = [40, 430, 820, 1210]
    g.box(xs[0], y, w, h, "Events", ["signed sensor batches", "camera sightings", "zone + speed"], fill=BLUE_BG, stroke=BLUE, num=1)
    g.box(xs[1], y, w, h, "Integrity gate", ["signature · sequence ·", "time window · replay"], fill=GRAY_BG, stroke=GRAY, num=2)
    g.box(xs[2], y, w, h, "Seal trust report", ["8 integrity checks, each", "passed / suspicious / failed", "/ not run"], fill=GRAY_BG, stroke=GRAY, num=3)
    g.box(xs[3], y, w, h, "Rules engine", ["status · risk score", "auto-hold cargo on theft"], fill=GRAY_BG, stroke=GRAY, num=4)
    for i in range(3):
        g.arrow([(xs[i] + w, y + h / 2), (xs[i + 1], y + h / 2)])
    g.box(xs[1], 290, w, 70, "Rejected + logged", ["never reaches the agents"], fill=RED_BG, stroke=RED, tsize=17, bsize=15)
    g.arrow([(xs[1] + w / 2, y + h), (xs[1] + w / 2, 290)], "bad data", xs[1] + w / 2 + 14, 272, color=RED, anchor="start")
    g.text(xs[3] + w + 10, y + 70, "", size=1)

    # ---- agents
    g.container(380, 400, 1300, 250, "AI agents (Gemini)  ·  one call per alert  ·  every number checked against the data", stroke=BLUE)
    g.person(560, 505, BLUE, "Thermal agent", ["temperature, freeze risk,", "maintenance review"])
    g.person(1030, 505, BLUE, "Seal agent", ["door events and", "data trust"])
    g.person(1500, 505, BLUE, "Incident agent", ["person, theft; proposes", "actions from a fixed menu"])
    g.arrow([(xs[3] + w / 2, y + h), (xs[3] + w / 2, 400)], "findings + status\nset by rules", xs[3] + w / 2 + 14, 305, anchor="start")
    g.arrow([(xs[2] + w / 2, y + h), (xs[2] + w / 2, 400)], "data check handed\nto every agent", xs[2] + w / 2 + 14, 330, anchor="start")
    g.chip(640, 626, "numbers checked · menu limited by the rules · rule-based text if the AI is off")

    # ---- bottom row
    yb, hb = 740, 170
    g.box(40, yb, 300, hb, "Maintenance review", ["Thermal agent reads the sensor history: is the cooling unit weakening?", "(temperature events only)"], fill=BLUE_BG, stroke=BLUE, num=5)
    g.box(420, yb, 310, hb, "Report gate", ["a temperature event becomes an incident report only if the review finds the unit may be failing"],
          fill=AMBER_BG, stroke=AMBER, num=6)
    g.box(820, yb, 330, hb, "Proposals", ["quarantine · divert to backup cold store · take vehicle offline · prepare report", "the rules limit the menu"],
          fill=AMBER_BG, stroke=AMBER, num=7)
    g.person(1290, yb + 55, GREEN, "Admin", ["approves or rejects"], size=1.0)
    g.person(1290, yb + 150, GREEN, "Driver", ["alerts, read-only"], size=0.55, lsize=15)
    g.box(1430, yb, 250, hb, "Action + records", ["Telegram alert", "Word report + evidence photos", "GS1 EPCIS events", "audit log of the decision"],
          fill=GREEN_BG, stroke=GREEN, num=8)

    g.arrow([(560, 650), (200, yb)], "temperature\nevents", 250, 690, anchor="middle")
    g.arrow([(340, yb + hb / 2), (420, yb + hb / 2)])
    g.arrow([(730, yb + hb / 2), (820, yb + hb / 2)], "if allowed", 775, yb + hb / 2 - 12)
    g.arrow([(985, 650), (985, yb)], "AI proposals, or the rules'\ndefault if the AI is off", 1000, 700, anchor="start")
    g.arrow([(1150, yb + 80), (1240, yb + 80)], None)
    g.arrow([(1345, yb + 80), (1430, yb + 80)], "approved", 1388, yb + 60)
    g.arrow([(1540, 650), (1540, yb)], "person or theft: draft\nreport made automatically", 1526, 700, dashed=True, color=GREEN, anchor="end")

    # ---- legend
    ly = 960
    g.person(60, ly - 1, BLUE, "", [], size=0.42)
    g.text(88, ly + 6, "AI agent", size=15)
    g.person(200, ly - 1, GREEN, "", [], size=0.42)
    g.text(228, ly + 6, "person", size=15)
    g.add(f'<rect x="360" y="{ly - 14}" width="30" height="22" rx="5" fill="{GRAY_BG}" stroke="{GRAY}" stroke-width="2"/>')
    g.text(400, ly + 6, "rules (ordinary code)", size=15)
    g.add(f'<rect x="590" y="{ly - 14}" width="30" height="22" rx="5" fill="{AMBER_BG}" stroke="{AMBER}" stroke-width="2"/>')
    g.text(630, ly + 6, "safeguard / human approval point", size=15)
    return g


def render_png(svg: Path, png: Path, w: int, h: int) -> bool:
    cmd = [EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
           f"--window-size={w},{h}", "--default-background-color=ffffffff", f"--screenshot={png}", svg.resolve().as_uri()]
    subprocess.run(cmd, capture_output=True, timeout=90)
    return png.exists() and png.stat().st_size > 5000


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, build in (("architecture", architecture), ("agent_workflow", workflow)):
        g = build()
        svg, png = OUT / f"{name}.svg", OUT / f"{name}.png"
        svg.write_text(g.render(), encoding="utf-8")
        ok = render_png(svg, png, g.w, g.h)
        print(f"{name}: svg written; png {'ok' if ok else 'FAILED (is Edge installed?)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
