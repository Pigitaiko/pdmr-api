"""France — AMF directors' declarations ("déclarations des dirigeants", MAR Art. 19).

The AMF's BDIF database is an Angular SPA, but its backend is a clean public JSON API reachable
over plain HTTP (no headless browser needed for the data):

- ``GET /back/api/v1/informations?TypesInformation=DD&From=&Size=`` — index of every directors'
  declaration (id, issuer + LEI, the PDF document path).
- ``GET /back/api/v1/documents/<path>`` — the declaration PDF.

The PDFs are cleanly labelled French text (NOM/FONCTION, EMETTEUR, NATURE, PRIX UNITAIRE, VOLUME …),
which we parse into a ``ParsedFiling``.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pdfplumber

from scraper.http import PoliteClient
from scraper.parser import (
    ParsedFiling,
    ParsedTransaction,
    map_role_code,
    map_transaction_type,
    parse_decimal,
)

BASE = "https://bdif.amf-france.org"
INFO_API = "/back/api/v1/informations?TypesInformation=DD"
DOC_API = "/back/api/v1/documents/"
PARIS = ZoneInfo("Europe/Paris")

_FR_MONTHS = {
    "janvier": 1,
    "février": 2,
    "fevrier": 2,
    "mars": 3,
    "avril": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7,
    "août": 8,
    "aout": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "décembre": 12,
    "decembre": 12,
}


def _fr_date(raw: str) -> date | None:
    m = re.search(r"(\d{1,2})\s+([A-Za-zéûà]+)\s+(\d{4})", raw or "")
    if not m:
        return None
    month = _FR_MONTHS.get(m.group(2).lower())
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


def _field(label: str, text: str) -> str | None:
    m = re.search(rf"{label}\s*:?\s*([^\n]+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def parse_amf_pdf(data: bytes, *, meta: dict | None = None) -> ParsedFiling:
    meta = meta or {}
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    except Exception:  # noqa: BLE001
        return ParsedFiling(
            source="amf_fr",
            country="FR",
            parse_status="failed",
            filing_id=meta.get("filing_id"),
            issuer_name=meta.get("issuer_name"),
        )

    person = _field("PERSONNE ETROITEMENT LIEE", text)
    # the name/role line sits on the line after the 'NOM /FONCTION ...' heading
    nf = re.search(r"PERSONNE ETROITEMENT LIEE\s*:?\s*\n?\s*([^\n]+)", text, re.IGNORECASE)
    person = nf.group(1).strip() if nf else person
    role_raw = person.split(",")[-1].strip() if person and "," in person else None
    person_name = person.split(",")[0].strip() if person else None

    issuer = _field(r"NOM", text)  # first 'NOM :' is the issuer block ('COORDONNEES DE L.EMETTEUR')
    im = re.search(r"NOM\s*:\s*([^\n]+)\s*\n\s*LEI\s*:", text, re.IGNORECASE)
    issuer = im.group(1).strip() if im else _field(r"NOM DE L.EMETTEUR", text)
    lei = _field("LEI", text)
    isin = None
    hm = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b", text)  # ISIN in the header line
    if hm:
        isin = hm.group(1)

    nature = _field("NATURE DE LA TRANSACTION", text)
    instrument = _field(r"DESCRIPTION DE L.INSTRUMENT FINANCIER", text)
    venue = _field("LIEU DE LA TRANSACTION", text)
    txdate = _fr_date(_field("DATE DE LA TRANSACTION", text) or "")
    price = parse_decimal(
        (_field("PRIX UNITAIRE", text) or _field(r"PRIX", text) or "").split(" ")[0]
    )
    vol_raw = _field("VOLUME", text) or ""
    volume = parse_decimal(vol_raw.replace(" ", ""))  # '84 516.0000' -> 84516
    opt = (_field(r"TRANSACTION LIEE A L.EXERCICE", text) or "").upper().startswith("OUI")

    tx = ParsedTransaction(
        seq=1,
        instrument_type=instrument,
        isin=isin,
        nature_raw=nature,
        transaction_type=map_transaction_type(nature),
        price=price,
        currency="EUR",
        volume=volume,
        transaction_date=txdate,
        venue=venue,
        linked_to_option_programme=opt or None,
    )
    out = ParsedFiling(
        filing_id=meta.get("filing_id"),
        source="amf_fr",
        country="FR",
        source_url=meta.get("source_url"),
        title="Déclaration de dirigeant",
        market=venue or "Euronext Paris",
        issuer_name=issuer or meta.get("issuer_name"),
        issuer_lei=(lei if lei and len(lei) == 20 else None),
        person_full_name=person_name,
        role_raw=role_raw,
        role_code=map_role_code(role_raw),
        notification_type=(
            "amendment"
            if "modification" in (_field("NOTIFICATION INITIALE", text) or "").lower()
            else "initial"
        ),
        published_at=(
            datetime.combine(txdate, datetime.min.time(), PARIS).astimezone(UTC) if txdate else None
        ),
        raw_text=text,
        transactions=[tx],
    )
    ok = bool(
        out.issuer_name and person_name and txdate and volume is not None and price is not None
    )
    out.parse_status = "success" if ok else "partial"
    return out


async def fetch_filings(client: PoliteClient, *, limit: int = 40) -> list[ParsedFiling]:
    """Read the DD index, then download + parse the most recent ``limit`` declaration PDFs."""
    resp = await client.get(f"{INFO_API}&From=0&Size={limit}")
    records = resp.json().get("result", [])
    out: list[ParsedFiling] = []
    for rec in records:
        docs = rec.get("documents") or []
        if not docs or not docs[0].get("path"):
            continue
        path = docs[0]["path"]
        meta = {
            "filing_id": "fr-" + str(rec.get("numero") or rec.get("id")),
            "issuer_name": (rec.get("societes") or [{}])[0].get("raisonSociale"),
            "source_url": BASE + DOC_API + path,
        }
        try:
            pdf = await client.get(DOC_API + path)
            out.append(parse_amf_pdf(pdf.content, meta=meta))
        except Exception:  # noqa: BLE001 - skip a bad PDF, keep the batch
            continue
    return out
