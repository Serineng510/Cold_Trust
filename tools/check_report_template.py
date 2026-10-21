"""Check an incident report template before it is used (run it after every edit in Word).

    .venv\\Scripts\\python -m tools.check_report_template [path\\to\\template.docx]

It lists any {tag} the report cannot fill, any field the report expects that the template no longer has, sections that are not
closed, and then fills the template with sample data to prove it still produces a well-formed document.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

from backend import docfill
from backend.report import PROJECT_ROOT

DEFAULT = PROJECT_ROOT / "templates" / "logistics-incident-report.docx"


def _png() -> bytes:
    """A tiny valid PNG, so the picture slot can be tested without any image library."""
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))
    rows = b"".join(b"\x00" + b"\x30\x60\xa0" * 8 for _ in range(6))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 6, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


SAMPLE = {
    "reportNumber": "INC-20260101-1", "incidentDate": "1 January 2026", "incidentTime": "12:00:00 (UTC+08:00)",
    "incidentLocation": "Sample location", "personnel": [{"name": "A. Driver", "role": "Driver", "contactInfo": "n/a"}],
    "incidentDescription": "Sample description.", "incidentType": "Sample type", "cargoDetails": "Sample cargo",
    "cargoDamageStatus": "Not determined", "hasCargoDamage": True, "cargoDamageDetails": "Sample exposure",
    "vehicleDamageStatus": "Not assessed", "hasVehicleDamage": True, "vehicleDamageDetails": "Sample",
    "injuriesStatus": "Not assessed", "hasInjuries": True, "injured": [{"name": "A. Person", "injuryDescription": "Sample"}],
    "incidentCause": "Sample cause", "immediateActions": "12:00  Sample action", "reporterName": "A. Admin",
    "reporterPosition": "Administrator", "reporterContact": "n/a", "additionalNotes": "Sample notes",
    "hasPhotoEvidence": True, "photos": [{"image": docfill.Image(_png(), "sample.png", "sample"), "photoDescription": "Sample"}],
    "signatures": [{"name": "A. Admin", "role": "Administrator", "signature": "", "date": ""}],
}
# Names used inside the lists above, and the picture tag
INSIDE = {"name", "role", "contactInfo", "injuryDescription", "signature", "date", "photoDescription", "image"}
REQUIRED = {"reportNumber", "incidentDate", "incidentTime", "incidentLocation", "personnel", "incidentDescription", "incidentType",
            "cargoDetails", "incidentCause", "immediateActions", "reporterName", "additionalNotes", "signatures"}


def problems(template: bytes) -> list[str]:
    out = []
    try:
        used = docfill.tags_in(template)
    except Exception as exc:
        return [f"The file cannot be read as a Word document: {exc}"]
    out += [f"{{{t}}} is not a field the report provides (typo?)" for t in sorted(used - set(SAMPLE) - INSIDE)]
    out += [f"{{{t}}} is not in the template, so that information will not appear in the report" for t in sorted(REQUIRED - used)]
    try:
        res = docfill.fill(template, SAMPLE)
        z = __import__("zipfile").ZipFile(__import__("io").BytesIO(res.docx))
        ET.fromstring(z.read("word/document.xml"))
        if "{%image}" in docfill._text(z.read("word/document.xml").decode()) or res.images != 1:
            out.append("The picture slot {%image} did not produce a picture: it must be on a paragraph of its own, inside "
                       "{#photos} ... {/photos}.")
        out += [f"The sample data had nothing for {{{t}}}" for t in sorted(res.missing)]
    except Exception as exc:
        out.append(f"Filling the template with sample data failed: {exc}")
    return out


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT
    found = problems(path.read_bytes())
    print(f"{path.name}: " + ("OK, every tag is known and the sample report builds." if not found else f"{len(found)} problem(s)"))
    for line in found:
        print("  -", line)
    sys.exit(1 if found else 0)
