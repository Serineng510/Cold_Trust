"""Add the evidence-picture slot to the PROJECT COPY of the incident report template (the original is never touched).

    .venv\\Scripts\\python -m tools.add_photo_slot

The template's "Photo Evidence" section has one text line per photo ({photoDescription}). This wraps that line in a loop
and puts an image tag above it, so each captured picture is embedded with its description underneath:

    {#hasPhotoEvidence}
    {#photos}
    {%image}                      <- the picture
    {photoDescription}            <- time, what was detected, file name, SHA-256
    {/photos}
    {/hasPhotoEvidence}

Safe to run again: it does nothing when the slot is already there. To move the picture, move the {%image} paragraph in Word.
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "logistics-incident-report.docx"
PLAIN = '<w:p><w:pPr><w:spacing w:lineRule="auto"/></w:pPr><w:r><w:rPr/><w:t xml:space="preserve">{}</w:t></w:r></w:p>'


def add_slot(docx: bytes) -> bytes | None:
    zin = zipfile.ZipFile(io.BytesIO(docx))
    doc = zin.read("word/document.xml").decode("utf-8")
    if "{%image}" in doc:
        return None
    m = None
    for p in re.finditer(r"<w:p(?:\s[^>]*)?>(?:(?!</w:p>).)*?\{photoDescription\}.*?</w:p>", doc, re.S):
        m = p
    if m is None:
        raise SystemExit("The template has no {photoDescription} paragraph, so there is nowhere to put the picture.")
    new = PLAIN.format("{#photos}") + PLAIN.format("{%image}") + m.group() + PLAIN.format("{/photos}")
    doc = doc[:m.start()] + new + doc[m.end():]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            zout.writestr(item, doc.encode("utf-8") if item.filename == "word/document.xml" else zin.read(item.filename))
    return out.getvalue()


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else TEMPLATE
    changed = add_slot(path.read_bytes())
    if changed is None:
        print(f"{path.name}: the picture slot is already there.")
    else:
        path.write_bytes(changed)
        print(f"{path.name}: picture slot added.")
