"""Parser for the Italian Allegato 3F PDMR notification form (Art. 19 MAR).

Input: path to a filing PDF (eMarketStorage cover page + bilingual IT/EN Allegato 3F).
Output: a typed ``ParsedFiling`` (Pydantic) with issuer, person and a list of transactions.

Design: locate section *labels* (never fixed coordinates) so the parser tolerates layout drift.
Missing fields are set to ``None`` and ``parse_status`` is downgraded to ``partial`` rather than
crashing. A wholly unparseable input yields ``parse_status='failed'`` with the raw text preserved.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

import pdfplumber
from pydantic import BaseModel, Field

ROME = ZoneInfo("Europe/Rome")
UTC = ZoneInfo("UTC")

ITALIAN_MONTHS = {
    "gennaio": 1,
    "febbraio": 2,
    "marzo": 3,
    "aprile": 4,
    "maggio": 5,
    "giugno": 6,
    "luglio": 7,
    "agosto": 8,
    "settembre": 9,
    "ottobre": 10,
    "novembre": 11,
    "dicembre": 12,
}

# ---- code mappings (see CLAUDE.md "Code mappings") -------------------------------------------

_ACQUISITION = ("ACQUIST", "ACQUISIZIONE", "SOTTOSCRIZIONE", "SUBSCRIPTION", "PURCHASE")
_DISPOSAL = ("CESSIONE", "VENDITA", "SALE", "DISPOSAL")


def map_transaction_type(nature_raw: str | None) -> str:
    """Map a free-text 'Natura dell'operazione' to A (acquisition) / D (disposal) / O (other)."""
    if not nature_raw:
        return "O"
    up = nature_raw.upper()
    if any(k in up for k in _ACQUISITION):
        return "A"
    if any(k in up for k in _DISPOSAL):
        return "D"
    return "O"


def map_role_code(role_raw: str | None, position_status: str | None = None) -> str:
    """Derive a coarse role code from the free-text 'Ruolo'/role field."""
    text = (role_raw or "").lower()
    if "amministratore delegato" in text or "chief executive" in text or "ceo" in text:
        return "AD"
    if "direttore finanziario" in text or "cfo" in text or "chief financial" in text:
        return "CFO"
    if "presidente" in text or "chair" in text:
        return "CHAIR"
    if (
        "consigliere" in text
        or "amministrazione" in text
        or "administration" in text
        or "board" in text
    ):
        return "DIR"
    if "direzione" in text or "management" in text:
        return "MGMT"
    if "controllo" in text or "control" in text:
        return "CTRL"
    if position_status and "associat" in position_status.lower():
        return "CAP"
    return "OTHER"


# ---- number / date parsing -------------------------------------------------------------------


def parse_decimal(raw: str | None) -> Decimal | None:
    """Parse a European-formatted number. Handles '92.5', '0.911', '1.234,56', '45000'.

    Rule: if both '.' and ',' present, the right-most is the decimal separator and the other is a
    thousands separator. A single '.' or ',' is treated as the decimal separator; repeated
    occurrences of one symbol are treated as thousands separators. (The CONSOB forms observed use
    '.' as the decimal separator with no thousands grouping.)
    """
    if raw is None:
        return None
    s = raw.strip().replace(" ", "")
    if not s:
        return None
    has_dot, has_comma = "." in s, "," in s
    if has_dot and has_comma:
        dec = "." if s.rfind(".") > s.rfind(",") else ","
        thou = "," if dec == "." else "."
        s = s.replace(thou, "").replace(dec, ".")
    elif has_comma:
        s = s.replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif has_dot and s.count(".") > 1:
        s = s.replace(".", "")
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _parse_italian_datetime(raw: str) -> datetime | None:
    """'19 Giugno 2026 09:25:28' (Europe/Rome) -> tz-aware UTC datetime."""
    m = re.search(r"(\d{1,2})\s+([A-Za-zÀ-ù]+)\s+(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})", raw)
    if not m:
        return None
    day, month_name, year, hh, mm, ss = m.groups()
    month = ITALIAN_MONTHS.get(month_name.lower())
    if not month:
        return None
    naive = datetime(int(year), month, int(day), int(hh), int(mm), int(ss))
    return naive.replace(tzinfo=ROME).astimezone(UTC)


def _parse_time(raw: str | None) -> time | None:
    if not raw:
        return None
    m = re.match(r"(\d{1,2}):(\d{2})(?::(\d{2}))?", raw.strip())
    if not m:
        return None
    h, mi, s = m.group(1), m.group(2), m.group(3) or "0"
    return time(int(h), int(mi), int(s))


# ---- output models ---------------------------------------------------------------------------


class ParsedTransaction(BaseModel):
    seq: int
    instrument_type: str | None = None
    isin: str | None = None
    nature_raw: str | None = None
    transaction_type: str = "O"
    price: Decimal | None = None
    currency: str = "EUR"
    volume: Decimal | None = None
    transaction_date: date | None = None
    time_from: time | None = None
    time_to: time | None = None
    venue: str | None = None
    venue_mic: str | None = None
    linked_to_option_programme: bool | None = None


class ParsedFiling(BaseModel):
    filing_id: str | None = None
    source: str = "emarketstorage"
    source_url: str | None = None
    title: str | None = None
    market: str | None = None
    tipologia: str | None = None
    issuer_name: str | None = None
    issuer_lei: str | None = None
    person_full_name: str | None = None
    person_first_name: str | None = None
    person_last_name: str | None = None
    is_legal_person: bool = False
    position_status: str | None = None
    role_raw: str | None = None
    role_code: str | None = None
    notification_type: str | None = None
    published_at: datetime | None = None
    parse_status: str = "success"
    raw_text: str = ""
    transactions: list[ParsedTransaction] = Field(default_factory=list)


# ---- extraction helpers ----------------------------------------------------------------------


def _first(pattern: str, text: str, flags: int = 0, group: int = 1) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(group).strip() if m else None


def extract_text(pdf_path: str | Path) -> str:
    with pdfplumber.open(str(pdf_path)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _parse_cover(text: str, out: ParsedFiling) -> None:
    # filing id: 'n.<id>' on cover or footer 'Fine Comunicato n.<id>'
    fid = _first(r"Fine Comunicato n\.?\s*(\d+-\d+-\d{4})", text) or _first(
        r"\b(\d+-\d+-\d{4})\b", text
    )
    out.filing_id = fid
    out.market = _first(r"Regolamentata n\.\s*([^\n]+)", text)
    out.tipologia = _first(r"Tipologia\s*:\s*([^\n]+)", text)
    out.title = _first(r"Oggetto\s*:\s*([^\n]+)", text)
    pub = _first(r"Data/Ora Inizio Diffusione\s*:\s*([^\n]+)", text)
    if pub:
        out.published_at = _parse_italian_datetime(pub)


def _parse_person(text: str, out: ParsedFiling) -> None:
    out.is_legal_person = "Per le persone giuridiche" in text or "For legal persons" in text
    out.position_status = _first(r"Position\s*/\s*Status\s+([^\n]+)", text)
    out.role_raw = _first(r"Ruolo:\s*([^\n]+)", text)
    if out.is_legal_person:
        # entity name sits on the 'Denominazione completa, <NAME>' line
        name = _first(r"Denominazione completa,\s*([^\n]+)", text)
        if name and name.lower().startswith("compresa la forma"):
            name = None  # label wrapped without a value on this line
        out.person_full_name = name.strip() if name else None
    else:
        first = _first(r"Nome:\s*([^\n]+?)\s+Cognome:", text)
        last = _first(r"Cognome:\s*([^\n]+?)\s*\n", text)
        out.person_first_name = first
        out.person_last_name = last
        if first and last:
            out.person_full_name = f"{first} {last}"
    if not out.notification_type:
        out.notification_type = (
            "amendment"
            if re.search(r"Modifica notifica", text) and "Nuova notifica" not in text
            else "initial"
        )


def _parse_issuer(text: str, out: ParsedFiling) -> None:
    out.issuer_name = _first(r"dell'entità:\s*([^\n]+)", text) or _first(
        r"Full name of the entity:\s*([^\n]+)", text
    )
    # LEI: 20-char alnum, appears after the LEI label / ISO 17442 reference
    lei = (
        _first(r"ISO 17442[^\n]*\s+([A-Z0-9]{20})\b", text)
        or _first(r"\bLEI[^\n]*?\b([A-Z0-9]{20})\b", text)
        or _first(r"\b([A-Z0-9]{18,20})\b", text)
    )
    out.issuer_lei = lei


def _split_operations(text: str) -> list[str]:
    """Split section 4 into per-operation blocks on 'Operazione - N' headers."""
    sec4 = text
    m = re.search(r"4\s+Dati relativi all'operazione", text)
    if m:
        sec4 = text[m.start() :]
    parts = re.split(r"Operazione\s*-\s*\d+", sec4)
    return [p for p in parts[1:]] if len(parts) > 1 else [sec4]


def _parse_operation(block: str, seq: int) -> tuple[ParsedTransaction, list[tuple[str, str]]]:
    tx = ParsedTransaction(seq=seq)

    instr = _first(r"Description of the (.+)", block)
    if instr and not instr.lower().startswith("financial"):
        tx.instrument_type = instr
    tx.isin = _first(r"ISIN:\s*([A-Z0-9]+)", block)

    # nature: lines between the 4b label's terminal '596/2014.' and the 'A norma ... paragrafo 6'
    anchor = re.search(r"A norma dell['’]articolo 19, paragrafo 6, lettera e\)", block)
    if anchor:
        seg = block[: anchor.start()]
        i = seg.rfind("596/2014.")
        if i != -1:
            nature = seg[i + len("596/2014.") :].strip()
            tx.nature_raw = re.sub(r"\s+", " ", nature) or None
    tx.transaction_type = map_transaction_type(tx.nature_raw)

    # share-option-programme flag: the 'No'/'Si' right after the 19(6)(e) label
    opt = re.search(r"share option programme\.\s*\n\s*(S[iì]|No)\b", block, re.IGNORECASE)
    if opt:
        tx.linked_to_option_programme = opt.group(1).lower().startswith("s")

    # price/volume: '<price> EUR <volume>' pairs within the 4c block only
    pv_block = block
    c = re.search(r"Prezzo/i e Volume/i|Price\(s\) and volume", block)
    d = re.search(r"Informazioni aggregate|Aggregated information", block)
    if c:
        pv_block = block[c.start() : d.start() if d else None]
    pairs = re.findall(r"([\d][\d.,]*)\s*EUR\s+([\d][\d.,]*)", pv_block)
    if pairs:
        tx.price = parse_decimal(pairs[0][0])
        tx.volume = parse_decimal(pairs[0][1])

    # date + UTC time window
    dm = re.search(r"(20\d\d-\d{2}-\d{2})(?:\s+From:\s*([\d:]+)\s+To:\s*([\d:]+))?", block)
    if dm:
        try:
            tx.transaction_date = date.fromisoformat(dm.group(1))
        except ValueError:
            tx.transaction_date = None
        tx.time_from = _parse_time(dm.group(2))
        tx.time_to = _parse_time(dm.group(3))

    # venue: text after 'ai sensi della ' up to EOL; MIC is the trailing token
    venue = _first(r"ai sensi della\s+([A-Z0-9][^\n]+)", block)
    if venue and venue.upper().startswith("MIFID"):
        venue = None
    if not venue:
        vm = re.search(r"\b([A-Z][A-Za-z0-9 .,&'À-Ù-]+?\s-\s[A-Z0-9]{3,4})\b", block)
        venue = vm.group(1) if vm else None
    if venue:
        venue = re.sub(r"\s*-\s*$", "", venue.strip()).strip()  # drop trailing dangling dash
        tx.venue = venue
        mic = re.search(r"-\s*([A-Z0-9]{3,4})\s*$", venue)
        tx.venue_mic = mic.group(1) if mic else None
    return tx, pairs


def parse_filing(
    pdf_path: str | Path, source_url: str | None = None, source: str = "emarketstorage"
) -> ParsedFiling:
    """Parse an Allegato 3F PDF into a ``ParsedFiling``. Never raises on malformed content."""
    try:
        text = extract_text(pdf_path)
    except Exception:  # noqa: BLE001 - corrupt/non-PDF input
        return ParsedFiling(
            source=source, source_url=source_url, parse_status="failed", raw_text=""
        )

    out = ParsedFiling(source=source, source_url=source_url, raw_text=text)

    # If this does not look like an Allegato 3F at all, mark failed but keep raw text.
    if "persone che esercitano funzioni" not in text and "managerial responsibilities" not in text:
        out.parse_status = "failed"
        _parse_cover(text, out)
        return out

    _parse_cover(text, out)
    _parse_person(text, out)
    _parse_issuer(text, out)
    out.role_code = map_role_code(out.role_raw, out.position_status)

    transactions: list[ParsedTransaction] = []
    for op_block in _split_operations(text):
        base, pairs = _parse_operation(op_block, seq=len(transactions) + 1)
        if len(pairs) <= 1:
            transactions.append(base)
        else:
            # one transaction row per price/volume pair (CLAUDE.md D-006)
            for price_raw, vol_raw in pairs:
                row = base.model_copy()
                row.seq = len(transactions) + 1
                row.price = parse_decimal(price_raw)
                row.volume = parse_decimal(vol_raw)
                transactions.append(row)
    out.transactions = transactions

    # decide parse_status
    required_ok = bool(out.filing_id and out.issuer_name and out.person_full_name)
    tx_ok = bool(transactions) and all(
        t.price is not None and t.volume is not None and t.transaction_date is not None
        for t in transactions
    )
    out.parse_status = "success" if (required_ok and tx_ok) else "partial"
    return out
