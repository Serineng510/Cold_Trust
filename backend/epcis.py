"""GS1 EPCIS 2.0 / CBV 2.0 records for the cold-chain trip (zero dependencies).

Port of Secure Handoff's epcis.js. Same identifiers and the same canonical
Event Hash ID algorithm (CBV 2.0 section 8.9, "ni:///sha-256;...?ver=CBV2.0"), checked
against the GS1 EPCIS Sandbox vectors in tests/fixtures/gs1-sandbox-hash-vectors.json.

What each ColdTrust moment becomes (vocabulary verified against ref.gs1.org and the
official EPCIS 2.0 JSON schema):

    first telemetry of the trip    departing     in_transit
    every accepted device ping     transporting  in_transit   + sensorElementList
    package goes UNDER REVIEW      holding       unknown      (segregated for further review)
    package QUARANTINED            inspecting    non_conformant, persistentDisposition set
    released by a supervisor       inspecting    conformant,     persistentDisposition unset

Theft is only ever *suspected* by the rules, so the agent records non_conformant, never
`stolen`; that disposition is a human finding.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Iterable

EPCIS_CONTEXT = "https://ref.gs1.org/standards/epcis/2.0.0/epcis-context.jsonld"
CT_NS = "https://coldtrust.example/epcis/"  # user-extension namespace, prefix "ct"
CONTEXT: list[Any] = [EPCIS_CONTEXT, {"ct": CT_NS}]

# GS1 reserves company prefixes starting 952 for examples and demos.
COMPANY_PREFIX = "9521234"

# ------------------------------------------------------------------ GS1 identifiers

def gs1_check_digit(digits: str) -> str:
    total = sum(int(d) * (3 if i % 2 == 0 else 1) for i, d in enumerate(reversed(digits)))
    return str((10 - total % 10) % 10)


def sscc(serial: int | str) -> str:
    """Logistic unit, AI (00): extension digit 0 + company prefix + serial ref (17 digits) + check digit."""
    body = "0" + COMPANY_PREFIX + str(serial).zfill(16 - len(COMPANY_PREFIX))
    return f"https://id.gs1.org/00/{body}{gs1_check_digit(body)}"


def gln(ref: int | str) -> str:
    """Physical location, AI (414)."""
    body = COMPANY_PREFIX + str(ref).zfill(12 - len(COMPANY_PREFIX))
    return f"https://id.gs1.org/414/{body}{gs1_check_digit(body)}"


def pgln(ref: int | str) -> str:
    """Party, AI (417)."""
    return gln(ref).replace("/414/", "/417/")


def giai(asset_ref: str) -> str:
    """Individual asset (sensor node, truck), AI (8004): company prefix + alphanumeric reference."""
    ref = re.sub(r"[^0-9A-Za-z]", "", asset_ref)
    return f"https://id.gs1.org/8004/{COMPANY_PREFIX}{ref}"


def geo_uri(lat: float, lon: float) -> str:
    """RFC 5870 location, allowed by CBV 2.0 for readPoint while the vehicle is moving."""
    return f"geo:{float(lat):.6f},{float(lon):.6f}"


def is_valid_digital_link(uri: str) -> bool:
    m = re.fullmatch(r"https://id\.gs1\.org/(00|414|417)/(\d+)", uri)
    if not m:
        return False
    length = {"00": 18, "414": 13, "417": 13}[m.group(1)]
    digits = m.group(2)
    return len(digits) == length and gs1_check_digit(digits[:-1]) == digits[-1]


def package_epc(pkg_id: str) -> str:
    """SHP-8841 -> SSCC with serial reference 8841."""
    return sscc(int(re.sub(r"\D", "", pkg_id) or 0))


# ------------------------------------------------------------------ canonical Event Hash ID (CBV 2.0 s8.9)

CBV = "https://ref.gs1.org/cbv/"
VOC = "https://ref.gs1.org/voc/"
ORDER = ["type", "eventTime", "eventTimeZoneOffset", "certificationInfo", "epcList", "parentID", "inputEPCList",
         "childEPCs", "quantityList", "childQuantityList", "inputQuantityList", "outputEPCList", "outputQuantityList",
         "action", "transformationID", "bizStep", "disposition", "persistentDisposition", "readPoint", "bizLocation",
         "bizTransactionList", "sourceList", "destinationList", "sensorElementList", "ilmd"]
EPC_LISTS = {"epcList": "epc", "inputEPCList": "epc", "childEPCs": "epc", "outputEPCList": "epc"}
QTY_LISTS = ["quantityList", "childQuantityList", "inputQuantityList", "outputQuantityList"]
SENSOR_META = ["time", "startTime", "endTime", "deviceID", "deviceMetadata", "rawData", "dataProcessingMethod",
               "bizRules"]
SENSOR_REPORT = ["type", "exception", "deviceID", "deviceMetadata", "rawData", "dataProcessingMethod", "time",
                 "microorganism", "chemicalSubstance", "value", "component", "stringValue", "booleanValue",
                 "hexBinaryValue", "uriValue", "minValue", "maxValue", "meanValue", "sDev", "percRank", "percValue",
                 "uom", "coordinateReferenceSystem"]
SKIP = {"@context", "eventID", "recordTime", "errorDeclaration"}
_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)


def utc_millis(t: str | float | int | datetime) -> str:
    """Always YYYY-MM-DDTHH:mm:ss.sssZ, like JavaScript's Date.prototype.toISOString()."""
    if isinstance(t, (int, float)):
        dt = datetime.fromtimestamp(int(t * 1000) / 1000, tz=timezone.utc)
    elif isinstance(t, datetime):
        dt = t.astimezone(timezone.utc)
    else:
        s = str(t).replace("Z", "+00:00")
        m = re.match(r"^(.*T\d\d:\d\d:\d\d)(\.\d+)?(.*)$", s)
        if m:  # normalise fractional seconds to 6 digits so fromisoformat accepts any precision
            frac = (m.group(2) or ".0")[1:7].ljust(6, "0")
            s = f"{m.group(1)}.{frac}{m.group(3)}"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _num(v: Any) -> str:
    """String(v) as JavaScript would print it."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    return str(v)


def _kv(k: str, v: Any) -> str:
    return f"{k}={v}"


def _sort_join(parts: Iterable[str]) -> str:
    return "".join(sorted(parts))


def _expand_vocab(prefix: str, v: str | None) -> str | None:
    return f"{CBV}{prefix}-{v}" if v and not _SCHEME.match(v) else v


def _expand_sensor_type(v: str | None) -> str | None:
    if v and v.startswith("gs1:"):
        return VOC + v[4:]
    if v and ":" not in v:
        return VOC + v
    return v


def _context_map(context: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for c in context if isinstance(context, list) else [context]:
        if isinstance(c, dict):
            out.update(c)
    return out


def _expand_key(key: str, ctx: dict[str, str]) -> str:
    i = key.find(":")
    if i > 0 and ctx.get(key[:i]):
        return ctx[key[:i]] + key[i + 1:]
    return key


def _extension_string(key: str, value: Any, ctx: dict[str, str]) -> str:
    iri = _expand_key(key, ctx)
    m = re.match(r"^(.*[#/])([^#/]*)$", iri)
    name = "{" + (m.group(1) if m else "") + "}" + (m.group(2) if m else iri)
    if isinstance(value, dict):
        return name + _sort_join(_extension_string(k, v, ctx) for k, v in value.items())
    if isinstance(value, list):
        return _sort_join(_extension_string(key, v, ctx) for v in value)
    return _kv(name, _num(value))


def pre_hash_string(event: dict[str, Any], context: Any = None) -> str:
    ctx = _context_map(CONTEXT if context is None else context)
    s = ""
    for field in ORDER:
        v = event.get(field)
        if v is None or (isinstance(v, list) and not v):
            continue
        if field == "type":
            s += _kv("eventType", v)
        elif field == "eventTime":
            s += _kv("eventTime", utc_millis(v))
        elif field in EPC_LISTS:
            s += field + _sort_join(_kv(EPC_LISTS[field], e) for e in v)
        elif field in QTY_LISTS:
            s += field + _sort_join(
                "quantityElement" + "".join(_kv(k, _num(q[k])) for k in ("epcClass", "quantity", "uom") if k in q)
                for q in v)
        elif field == "bizStep":
            s += _kv("bizStep", _expand_vocab("BizStep", v))
        elif field == "disposition":
            s += _kv("disposition", _expand_vocab("Disp", v))
        elif field == "persistentDisposition":
            s += "persistentDisposition" + "".join(
                _sort_join(_kv(k, _expand_vocab("Disp", d)) for d in v[k]) for k in ("set", "unset") if v.get(k))
        elif field in ("readPoint", "bizLocation"):
            s += field + _kv("id", v["id"])
        elif field == "bizTransactionList":
            s += field + _sort_join(
                (_kv("type", _expand_vocab("BTT", b["type"])) if b.get("type") else "")
                + _kv("bizTransaction", b["bizTransaction"]) for b in v)
        elif field in ("sourceList", "destinationList"):
            k = "source" if field == "sourceList" else "destination"
            s += field + _sort_join(_kv("type", _expand_vocab("SDT", x["type"])) + _kv(k, x[k]) for x in v)
        elif field == "sensorElementList":
            def element(el: dict[str, Any]) -> str:
                out = "sensorElement"
                meta = el.get("sensorMetadata")
                if meta:
                    out += "sensorMetadata" + "".join(
                        _kv(k, utc_millis(meta[k]) if "time" in k.lower() else meta[k])
                        for k in SENSOR_META if meta.get(k) is not None)
                return out + _sort_join(
                    "sensorReport" + "".join(
                        _kv(k, _expand_sensor_type(r[k]) if k == "type" else utc_millis(r[k]) if k == "time"
                            else _num(r[k]))
                        for k in SENSOR_REPORT if r.get(k) is not None)
                    for r in el.get("sensorReport") or [])
            s += _sort_join(element(el) for el in v)
        elif field == "ilmd":
            s += "ilmd" + _sort_join(_extension_string(k, x, ctx) for k, x in v.items())
        else:
            s += _kv(field, v)
    known = set(ORDER) | SKIP
    return s + _sort_join(_extension_string(k, v, ctx) for k, v in event.items() if k not in known)


def event_hash_id(event: dict[str, Any], context: Any = None) -> str:
    digest = hashlib.sha256(pre_hash_string(event, context).encode("utf-8")).hexdigest()
    return f"ni:///sha-256;{digest}?ver=CBV2.0"


def with_event_id(event: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in event.items() if k != "eventID"}
    return {"eventID": event_hash_id(body), **body}


def verify_events(events: list[dict[str, Any]], context: Any = None) -> list[dict[str, Any]]:
    """Recompute every event's hash: any edit after capture shows up as valid=False."""
    out = []
    for e in events:
        body = {k: v for k, v in e.items() if k != "eventID"}
        out.append({"eventID": e.get("eventID"), "valid": event_hash_id(body, context) == e.get("eventID")})
    return out


# ------------------------------------------------------------------ documents and queries

def document(events: list[dict[str, Any]], created: str) -> dict[str, Any]:
    return {"@context": CONTEXT, "type": "EPCISDocument", "schemaVersion": "2.0", "creationDate": created,
            "epcisBody": {"eventList": events}}


def query_document(events: list[dict[str, Any]], created: str, query_name: str = "SimpleEventQuery") -> dict[str, Any]:
    return {"@context": CONTEXT, "type": "EPCISQueryDocument", "schemaVersion": "2.0", "creationDate": created,
            "epcisBody": {"queryResults": {"queryName": query_name, "resultsBody": {"eventList": events}}}}


def query_events(events: list[dict[str, Any]], q: dict[str, str | None]) -> list[dict[str, Any]]:
    """EPCIS 2.0 query subset: EQ_bizStep, EQ_disposition, MATCH_epc, GE_eventTime, LT_eventTime, eventID."""
    def lst(v: str) -> list[str]:
        return [x.strip() for x in v.split(",") if x.strip()]
    ge = utc_millis(q["GE_eventTime"]) if q.get("GE_eventTime") else None
    lt = utc_millis(q["LT_eventTime"]) if q.get("LT_eventTime") else None
    return [e for e in events
            if (not q.get("EQ_bizStep") or e.get("bizStep") in lst(q["EQ_bizStep"]))
            and (not q.get("EQ_disposition") or e.get("disposition") in lst(q["EQ_disposition"]))
            and (not q.get("MATCH_epc") or any(x in e.get("epcList", []) for x in lst(q["MATCH_epc"])))
            and (not ge or e["eventTime"] >= ge)
            and (not lt or e["eventTime"] < lt)
            and (not q.get("eventID") or e.get("eventID") == q["eventID"])]


# ------------------------------------------------------------------ cold-chain event builders

def _base(epcs: list[str], when: float, tz: str, biz_step: str, disposition: str) -> dict[str, Any]:
    return {"type": "ObjectEvent", "eventTime": utc_millis(when), "eventTimeZoneOffset": tz,
            "epcList": sorted(epcs), "action": "OBSERVE", "bizStep": biz_step, "disposition": disposition}


def departing_event(epcs: list[str], when: float, tz: str, location: str, vehicle: str) -> dict[str, Any]:
    ev = _base(epcs, when, tz, "departing", "in_transit")
    ev["readPoint"] = {"id": location}
    ev["bizLocation"] = {"id": location}
    ev["ct:vehicle"] = vehicle
    return with_event_id(ev)


def transport_event(epcs: list[str], when: float, tz: str, *, read_point: str | None, vehicle: str, device: str,
                    samples: list[Any], hold_limits: tuple[float, float], light_alarm_pct: float,
                    data_suspect: bool, audit_id: int | None, simulated: bool = False) -> dict[str, Any]:
    """One sensorElement per device payload: min/max/mean instead of every 5 s sample."""
    temps = [s.temp_c for s in samples]
    hums = [s.hum for s in samples]
    lights = [s.light_pct for s in samples]
    lo, hi = hold_limits
    temp_report: dict[str, Any] = {"type": "Temperature", "minValue": min(temps), "maxValue": max(temps),
                                   "meanValue": round(sum(temps) / len(temps), 2), "uom": "CEL"}
    if data_suspect:
        temp_report["exception"] = "ERROR_CONDITION"      # the reading itself is not trustworthy
    elif min(temps) < lo or max(temps) > hi:
        temp_report["exception"] = "ALARM_CONDITION"      # outside the hold's limits
    reports = [
        temp_report,
        {"type": "RelativeHumidity", "meanValue": round(sum(hums) / len(hums), 1), "uom": "P1"},
        # The LDR is uncalibrated, so its level is a relative percentage, not lux.
        {"type": "Dimensionless", "maxValue": round(max(lights), 1), "meanValue": round(sum(lights) / len(lights), 1),
         "uom": "P1", "dataProcessingMethod": CT_NS + "method/ldr-relative-level"},
    ]
    if max(lights) >= light_alarm_pct:
        reports.append({"type": "Illuminance", "exception": "ALARM_CONDITION", "booleanValue": True})
    ev = _base(epcs, when, tz, "transporting", "in_transit")
    if read_point:
        ev["readPoint"] = {"id": read_point}
    ev["sensorElementList"] = [{
        "sensorMetadata": {"startTime": utc_millis(samples[0].t), "endTime": utc_millis(samples[-1].t),
                           "deviceID": device},
        "sensorReport": reports,
    }]
    ev["ct:vehicle"] = vehicle
    if simulated:
        ev["ct:simulated"] = True          # demo data must never pass for a real measurement
    if audit_id is not None:
        ev["ct:auditEntry"] = audit_id
    return with_event_id(ev)


STATUS_EVENT = {  # new package status -> (bizStep, disposition, persistentDisposition)
    "UNDER_REVIEW": ("holding", "unknown", None),
    "QUARANTINED": ("inspecting", "non_conformant", {"set": ["non_conformant"]}),
    "NORMAL": ("inspecting", "conformant", None),
}


def status_event(epcs: list[str], when: float, tz: str, new_status: str, *, read_point: str | None, cause: str,
                 vehicle: str, was_quarantined: bool = False, supervisor: str | None = None,
                 reason: str | None = None, audit_id: int | None = None) -> dict[str, Any]:
    biz_step, disposition, persistent = STATUS_EVENT[new_status]
    if new_status != "QUARANTINED" and was_quarantined:
        persistent = {"unset": ["non_conformant"]}  # only a supervisor release lifts a quarantine
    ev = _base(epcs, when, tz, biz_step, disposition)
    if persistent:
        ev["persistentDisposition"] = persistent
    if read_point:
        ev["readPoint"] = {"id": read_point}
    ev["ct:cause"] = cause
    ev["ct:vehicle"] = vehicle
    if supervisor:
        ev["ct:supervisor"] = supervisor
    if reason:
        ev["ct:reason"] = reason
    if audit_id is not None:
        ev["ct:auditEntry"] = audit_id
    return with_event_id(ev)


def dumps(doc: dict[str, Any]) -> str:
    return json.dumps(doc, ensure_ascii=False)
