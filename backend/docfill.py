"""Fills a Word (.docx) template that uses docxtemplater-style tags, with no third-party packages.

    {name}                       replaced by the value (a "\\n" becomes a line break; the text is XML-escaped)
    {#name} ... {/name}          a section: repeated for each item of a list, kept once when the value is true, dropped when false
    {^name} ... {/name}          an inverted section: kept only when the value is false or an empty list
    {%name}                      a paragraph on its own: replaced by the image in data[name] (an `Image`)

Sections can be whole paragraphs (the tag paragraphs are removed), several paragraphs and tables in between, one table row (the
row is repeated), or inline inside a paragraph. Tags that Word has split across several runs are put back together. Anything the
template asks for that the data does not have is replaced by nothing and reported in `Result.missing`.
"""
from __future__ import annotations

import io
import re
import struct
import zipfile
from dataclasses import dataclass, field
from typing import Any
from xml.sax.saxutils import escape, unescape

EMU_PER_INCH = 914400
MAX_IMAGE_W_IN, MAX_IMAGE_H_IN = 4.6, 3.6          # the page text area is 6 inches wide

TAG = re.compile(r"\{([#^/%]?)(\w+)\}")
MARKER = re.compile(r"^\{([#^/])(\w+)\}$")
IMAGE_MARKER = re.compile(r"^\{%(\w+)\}$")
INLINE_SECTION = re.compile(r"\{([#^])(\w+)\}(.*?)\{/\2\}", re.S)
T_ELEMENT = re.compile(r"<w:t(?:\s[^>]*)?(?<!/)>(.*?)</w:t>", re.S)       # not a self-closing <w:t/>


@dataclass
class Image:
    data: bytes
    name: str = "image.jpeg"
    descr: str = ""

    @property
    def ext(self) -> str:
        return "png" if self.data[:8] == b"\x89PNG\r\n\x1a\n" else "jpeg"


@dataclass
class Result:
    docx: bytes
    missing: set[str] = field(default_factory=set)       # tags the template used that the data did not have
    images: int = 0


def image_size(data: bytes) -> tuple[int, int]:
    """(width, height) in pixels of a PNG or JPEG, read from the header."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker == 0xFF:
                i += 1
            elif marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
            elif 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            else:
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    raise ValueError("not a PNG or JPEG image")


_XML_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]")      # not allowed in XML 1.0, even escaped


# ------------------------------------------------------------------ reading the XML as blocks

def _end_of_element(xml: str, start: int) -> int:
    """Index just after the element that starts at `start` (counts nested elements of the same name)."""
    name = re.match(r"<([\w:]+)", xml[start:]).group(1)
    gt = xml.index(">", start)
    if xml[gt - 1] == "/":
        return gt + 1
    depth, pos = 1, gt + 1
    pat = re.compile(rf"<{name}(?=[\s>/])|</{name}>")
    while depth:
        m = pat.search(xml, pos)
        if m is None:
            raise ValueError(f"unclosed <{name}>")
        if m.group().startswith("</"):
            depth -= 1
        elif xml[xml.index(">", m.start()) - 1] != "/":
            depth += 1
        pos = m.end()
    return pos


def _blocks(xml: str) -> list[str]:
    """The direct children of a container (paragraphs, tables, ...), each as XML text."""
    out, i = [], 0
    while True:
        m = re.compile(r"<[\w:]+(?=[\s>/])").search(xml, i)
        if not m:
            return out
        end = _end_of_element(xml, m.start())
        out.append(xml[m.start():end])
        i = end


def _text(xml: str) -> str:
    return "".join(unescape(t) for t in T_ELEMENT.findall(xml))


# ------------------------------------------------------------------ tags split across runs

def _join_split_tags(p: str) -> str:
    """Word often splits "{name}" over several runs. If any tag is not whole inside one <w:t>, merge the paragraph's runs."""
    joined = _text(p)
    if "{" not in joined:
        return p
    whole = {m.group() for t in T_ELEMENT.findall(p) for m in TAG.finditer(unescape(t))}
    if all(m.group() in whole for m in TAG.finditer(joined)):
        return p
    ppr = re.search(r"<w:pPr>.*?</w:pPr>|<w:pPr/>", p, re.S)
    rpr = re.search(r"<w:r>\s*(<w:rPr>.*?</w:rPr>|<w:rPr/>)", p, re.S)
    head = re.match(r"<w:p(?:\s[^>]*)?>", p).group()
    return (f"{head}{ppr.group() if ppr else ''}<w:r>{rpr.group(1) if rpr else ''}"
            f'<w:t xml:space="preserve">{escape(joined)}</w:t></w:r></w:p>')


# ------------------------------------------------------------------ the renderer

class _Filler:
    def __init__(self) -> None:
        self.missing: set[str] = set()
        self.media: list[tuple[str, Image]] = []        # (relationship id, image)
        self._ids = 1000

    # ---- values
    def _value(self, name: str, ctx: dict[str, Any]) -> Any:
        if name not in ctx:
            self.missing.add(name)
        return ctx.get(name)

    @staticmethod
    def _text_of(v: Any) -> str:
        return "" if v is None else _XML_ILLEGAL.sub("", str(v))

    @staticmethod
    def _truthy(v: Any) -> bool:
        return bool(v)

    # ---- inline text
    def _inline(self, text: str, ctx: dict[str, Any]) -> str:
        def section(m: re.Match) -> str:
            sign, name, inner = m.groups()
            v = self._value(name, ctx)
            if sign == "^":
                return inner if not self._truthy(v) else ""
            if isinstance(v, list):
                return "".join(self._inline(inner, {**ctx, **it}) for it in v if isinstance(it, dict))
            return self._inline(inner, ctx) if self._truthy(v) else ""

        text = INLINE_SECTION.sub(section, text)
        return TAG.sub(lambda m: self._text_of(self._value(m.group(2), ctx)) if m.group(1) == "" else "", text)

    def _paragraph(self, p: str, ctx: dict[str, Any]) -> str:
        def one(m: re.Match) -> str:
            head = m.group()[:m.group().index(">") + 1]
            if "xml:space" not in head:
                head = head[:-1] + ' xml:space="preserve">'
            parts = self._inline(unescape(m.group(1)), ctx).split("\n")
            return "</w:t><w:br/>".join(f"{head}{escape(x)}" for x in parts) + "</w:t>" if len(parts) > 1 else \
                f"{head}{escape(parts[0])}</w:t>"

        return T_ELEMENT.sub(one, p) if "{" in p else p

    # ---- images
    def _image_paragraph(self, p: str, img: Image) -> str:
        w_px, h_px = image_size(img.data)
        scale = min(MAX_IMAGE_W_IN * 96 / w_px, MAX_IMAGE_H_IN * 96 / h_px, 1e9)
        cx, cy = int(w_px * scale / 96 * EMU_PER_INCH), int(h_px * scale / 96 * EMU_PER_INCH)
        rid = f"rIdImg{len(self.media) + 1}"
        self.media.append((rid, img))
        self._ids += 1
        n = self._ids
        descr = escape(img.descr, {'"': "&quot;"})
        return (
            '<w:p><w:pPr><w:keepNext/><w:spacing w:before="120" w:after="60"/><w:jc w:val="center"/></w:pPr><w:r><w:drawing>'
            f'<wp:inline xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" distT="0" distB="0" '
            f'distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/><wp:docPr id="{n}" name="Evidence {n - 1000}" descr="{descr}"/>'
            '<wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'noChangeAspect="1"/></wp:cNvGraphicFramePr>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{n}" name="{escape(img.name)}"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline>'
            '</w:drawing></w:r></w:p>')

    # ---- tables
    def _table(self, tbl: str, ctx: dict[str, Any]) -> str:
        pieces, last = [], 0
        for m in re.finditer(r"<w:tr(?:\s[^>]*)?>.*?</w:tr>", tbl, re.S):
            pieces.append(tbl[last:m.start()])
            last = m.end()
            row = re.sub(r"<w:p(?:\s[^>]*)?>.*?</w:p>", lambda pm: _join_split_tags(pm.group()), m.group(), flags=re.S)
            loop = next((n for s, n in TAG.findall(_text(row)) if s == "#" and f"{{/{n}}}" in _text(row)), None)
            if loop is None:
                pieces.append(re.sub(r"<w:p(?:\s[^>]*)?>.*?</w:p>", lambda pm: self._paragraph(pm.group(), ctx), row, flags=re.S))
                continue
            base = row.replace(f"{{#{loop}}}", "").replace(f"{{/{loop}}}", "")
            items = self._value(loop, ctx)
            for it in items if isinstance(items, list) else []:
                sub = {**ctx, **it} if isinstance(it, dict) else ctx
                pieces.append(re.sub(r"<w:p(?:\s[^>]*)?>.*?</w:p>", lambda pm: self._paragraph(pm.group(), sub), base, flags=re.S))
        pieces.append(tbl[last:])
        return "".join(pieces)

    # ---- the body
    def blocks(self, blocks: list[str], ctx: dict[str, Any]) -> str:
        out, i = [], 0
        while i < len(blocks):
            b = blocks[i]
            if b.startswith("<w:p ") or b.startswith("<w:p>"):
                b = _join_split_tags(b)
                text = _text(b).strip()
                marker = MARKER.match(text)
                if marker and marker.group(1) in "#^":
                    sign, name = marker.group(1), marker.group(2)
                    depth, j = 1, i + 1
                    while j < len(blocks) and depth:                      # find the matching {/name}
                        t = MARKER.match(_text(_join_split_tags(blocks[j])).strip()) if blocks[j].startswith("<w:p") else None
                        if t and t.group(2) == name:
                            depth += 1 if t.group(1) in "#^" else -1
                        j += 1
                    if depth:
                        raise ValueError(f"section {{{sign}{name}}} is never closed")
                    inner = blocks[i + 1:j - 1]
                    v = self._value(name, ctx)
                    if sign == "^":
                        out.append(self.blocks(inner, ctx) if not self._truthy(v) else "")
                    elif isinstance(v, list):
                        out.append("".join(self.blocks(inner, {**ctx, **it}) for it in v if isinstance(it, dict)))
                    else:
                        out.append(self.blocks(inner, ctx) if self._truthy(v) else "")
                    i = j
                    continue
                img = IMAGE_MARKER.match(text)
                if img:
                    v = self._value(img.group(1), ctx)
                    out.append(self._image_paragraph(b, v) if isinstance(v, Image) else "")
                else:
                    out.append(self._paragraph(b, ctx))
            elif b.startswith("<w:tbl"):
                out.append(self._table(b, ctx))
            else:
                out.append(b)
            i += 1
        return "".join(out)


def fill(template: bytes, data: dict[str, Any]) -> Result:
    """Return the filled document. `template` is the .docx file's bytes."""
    zin = zipfile.ZipFile(io.BytesIO(template))
    doc = zin.read("word/document.xml").decode("utf-8")
    start = doc.index("<w:body>") + len("<w:body>")
    end = doc.rindex("</w:body>")
    body = _blocks(doc[start:end])
    sect = [b for b in body if b.startswith("<w:sectPr")]
    filler = _Filler()
    new_body = filler.blocks([b for b in body if not b.startswith("<w:sectPr")], data) + "".join(sect)
    new_doc = doc[:start] + new_body + doc[end:]

    rels = zin.read("word/_rels/document.xml.rels").decode("utf-8")
    types = zin.read("[Content_Types].xml").decode("utf-8")
    extra: dict[str, bytes] = {}
    for n, (rid, img) in enumerate(filler.media, 1):
        part = f"media/evidence{n}.{img.ext}"
        extra["word/" + part] = img.data
        rels = rels.replace("</Relationships>", f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/'
                                                f'2006/relationships/image" Target="{part}"/></Relationships>')
        if f'Extension="{img.ext}"' not in types:
            types = types.replace("<Override ", f'<Default Extension="{img.ext}" ContentType="image/{img.ext}"/><Override ', 1)

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            if item.filename == "word/document.xml":
                zout.writestr(item, new_doc.encode("utf-8"))
            elif item.filename == "word/_rels/document.xml.rels":
                zout.writestr(item, rels.encode("utf-8"))
            elif item.filename == "[Content_Types].xml":
                zout.writestr(item, types.encode("utf-8"))
            else:
                zout.writestr(item, zin.read(item.filename))
        for name, blob in extra.items():
            zout.writestr(name, blob)
    return Result(out.getvalue(), filler.missing, len(filler.media))


def tags_in(template: bytes) -> set[str]:
    """Every tag name a template uses (for the template checker)."""
    doc = zipfile.ZipFile(io.BytesIO(template)).read("word/document.xml").decode("utf-8")
    start = doc.index("<w:body>")
    names: set[str] = set()
    for p in re.findall(r"<w:p(?:\s[^>]*)?>.*?</w:p>", doc[start:], re.S):
        names.update(n for _, n in TAG.findall(_text(_join_split_tags(p))))
    return names
