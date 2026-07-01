"""Norway — Oslo Børs NewsWeb, the OAM (Officially Appointed Mechanism) for the Oslo market.

A clean public JSON API (``api3.oslo.oslobors.no/v1/newsreader``) lists disclosures; category
**1102 = MANAGERS' TRANSACTION**. Each message carries the Art. 19 data as a PDF attachment — most
often Finanstilsynet's standard **KRT-1500** form (system-generated, so its numbered Norwegian
labels are identical across issuers and parse reliably). When the attachment is a custom PDF (e.g.
multi-person LTIP grants, takeover-offer acceptances) we fall back to a ``partial`` filing from the
listing + free-text body.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, date, datetime

import pdfplumber

from scraper.http import PoliteClient
from scraper.nasdaq_nordic import parse_mar_text
from scraper.parser import (
    ParsedFiling,
    ParsedTransaction,
    map_role_code,
    map_transaction_type,
    parse_decimal,
)

BASE = "https://api3.oslo.oslobors.no/v1/newsreader"
LIST_URL = BASE + "/list?category=1102"
MSG_URL = BASE + "/message?messageId="
ATT_URL = BASE + "/attachment?messageId={mid}&attachmentId={aid}"

_ISIN_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}[0-9])\b")
_LEI_RE = re.compile(r"\b([A-Z0-9]{18}[0-9]{2})\b")
_MIC_RE = re.compile(r"\b(X[A-Z0-9]{3})\b")
_LEGAL = re.compile(r"\b(AS|ASA|A/S|ANS|DA|BA|SA|LTD|AB|OYJ|HOLDING|INVEST)\b", re.I)


def _krt_field(text: str, num: str, *, stop: int = 160) -> str | None:
    """Read a KRT-1500 numbered field, e.g. ``2.3.2 ISIN-kode : NO0013683409``.

    The value is the rest of the label's line after an optional ``:``; if empty, the next line.
    Finanstilsynet writes a placeholder when a field is blank — treat that as absent.
    """
    m = re.search(rf"(?m)^{re.escape(num)}\b[^\n:]*:?\s*(.*)$", text)
    if not m:
        return None
    val = m.group(1).strip()
    if not val:
        after = text[m.end() :].lstrip("\n")
        val = after.split("\n", 1)[0].strip() if after else ""
    if not val or val.lower().startswith("du har ikke lagt inn"):
        return None
    return val[:stop]


def _parse_no_date(raw: str | None) -> date | None:
    if not raw:
        return None
    m = re.search(r"(\d{1,2})[.](\d{1,2})[.](\d{4})", raw)  # 01.07.2026
    if m:
        d, mo, y = (int(x) for x in m.groups())
    else:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", raw)
        if not m:
            return None
        y, mo, d = (int(x) for x in m.groups())
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def is_krt1500(text: str) -> bool:
    return "KRT-1500" in text or "primærinnsider" in text.lower()


def parse_krt1500(text: str) -> dict:
    """Parse Finanstilsynet's KRT-1500 managers'-transaction form (label-anchored, reliable)."""
    isin = _krt_field(text, "2.3.2") or ""
    isin_m = _ISIN_RE.search(isin) or _ISIN_RE.search(text)
    lei = _krt_field(text, "2.2.1") or ""
    lei_m = _LEI_RE.search(lei)
    venue = _krt_field(text, "2.10.1")
    mic = _MIC_RE.search(venue or "")
    nature = _krt_field(text, "2.4.1")
    # holder: the primary insider (1.7.1); when traded via an associated company, note it (1.6.2)
    person = _krt_field(text, "1.7.1")
    assoc = _krt_field(text, "1.6.2")
    return {
        "person": person,
        "assoc_company": assoc,
        "role": _krt_field(text, "1.7.2"),
        "issuer": _krt_field(text, "2.2.2"),
        "lei": lei_m.group(1) if lei_m else None,
        "instrument": _krt_field(text, "2.3.1"),
        "isin": isin_m.group(1) if isin_m else None,
        "nature": nature,
        "currency": (_krt_field(text, "2.6.1") or "NOK")[:8],
        "price": parse_decimal(_krt_field(text, "2.8.1")),
        "volume": parse_decimal(_krt_field(text, "2.8.2")),
        "txdate": _parse_no_date(_krt_field(text, "2.9.1")),
        "venue": venue,
        "venue_mic": mic.group(1) if mic else None,
    }


def _pdf_text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def build_filing(item: dict, fields: dict, source_url: str) -> ParsedFiling:
    issuer = item.get("issuerName") or fields.get("issuer")
    person = fields.get("person")
    assoc = fields.get("assoc_company")
    published = None
    pt = item.get("publishedTime")
    if pt:
        try:
            published = datetime.fromisoformat(pt.replace("Z", "+00:00")).astimezone(UTC)
        except ValueError:
            published = None
    markets = item.get("markets") or []
    nature = fields.get("nature")
    tx = ParsedTransaction(
        seq=1,
        instrument_type=fields.get("instrument"),
        isin=fields.get("isin"),
        nature_raw=nature,
        transaction_type=map_transaction_type(nature),
        price=fields.get("price"),
        currency=(fields.get("currency") or "NOK")[:8],
        volume=fields.get("volume"),
        transaction_date=fields.get("txdate"),
        venue=fields.get("venue"),
        venue_mic=fields.get("venue_mic"),
    )
    role = fields.get("role")
    # a trade via an associated legal entity: the entity is the holder, the PDMR is the person
    is_legal = bool(assoc) or bool(person and _LEGAL.search(person))
    out = ParsedFiling(
        filing_id=f"oslo-{item.get('messageId')}",
        source="oslo_bors_no",
        country="NO",
        source_url=source_url,
        title=item.get("title") or "Managers' transaction",
        market=(markets[0] if markets else "Oslo Børs"),
        issuer_name=issuer,
        issuer_lei=fields.get("lei"),
        person_full_name=person,
        is_legal_person=is_legal,
        position_status=role,
        role_raw=role,
        role_code=map_role_code(role),
        notification_type="initial",
        published_at=published,
        raw_text=(item.get("title") or "")[:4000],
        transactions=[tx],
    )
    ok = bool(issuer and person and fields.get("isin") and fields.get("txdate"))
    out.parse_status = "success" if ok else "partial"
    return out


async def fetch_filings(client: PoliteClient, *, limit: int = 40) -> list[ParsedFiling]:
    """List Oslo Børs managers' transactions (category 1102) and parse each KRT-1500 attachment."""
    import json

    resp = await client.get(LIST_URL)
    messages = json.loads(resp.text).get("data", {}).get("messages", [])
    out: list[ParsedFiling] = []
    for item in messages[:limit]:
        mid = item.get("messageId")
        source_url = f"https://newsweb.oslobors.no/message/{mid}"
        fields: dict = {}
        try:
            if item.get("numbAttachments"):
                detail = json.loads((await client.get(f"{MSG_URL}{mid}")).text)
                msg = detail.get("data", {}).get("message", {})
                for att in msg.get("attachments") or []:
                    raw = (await client.get(ATT_URL.format(mid=mid, aid=att.get("id")))).content
                    if raw[:5] != b"%PDF-":
                        continue
                    text = _pdf_text(raw)
                    if is_krt1500(text):
                        fields = parse_krt1500(text)
                        break
                    if re.search(r"discharging managerial|Name of the issuer", text, re.I):
                        # some issuers attach the English EU-harmonised template instead of KRT-1500
                        fields = parse_mar_text(text)
                        fields["role"] = fields.get("position")
                        if fields.get("person") and fields.get("isin"):
                            break
        except Exception:  # noqa: BLE001 - keep the batch; emit a listing-only partial
            fields = {}
        out.append(build_filing(item, fields, source_url))
    return out
