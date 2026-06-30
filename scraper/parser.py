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

_ACQUISITION = ("ACQUIS", "SOTTOSCRIZIONE", "SUBSCRIPTION", "SUBSCRIBE", "PURCHASE")
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
    if "direzione" in text or "management" in text or "executive" in text:
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
    country: str = "IT"
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


def _parse_operation(
    block: str, seq: int
) -> tuple[ParsedTransaction, list[tuple[Decimal | None, Decimal | None]]]:
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
    pairs: list[tuple[Decimal | None, Decimal | None]] = [
        (parse_decimal(p), parse_decimal(v))
        for p, v in re.findall(r"([\d][\d.,]*)\s*EUR\s+([\d][\d.,]*)", pv_block)
    ]
    if pairs:
        tx.price, tx.volume = pairs[0]

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


# ---- 1Info layout (different rendering of the same Allegato 3F) ------------------------------

_LEGAL_SUFFIX = re.compile(
    r"\b(S\.?P\.?A\.?|S\.?R\.?L\.?|S\.?A\.?P\.?A\.?|S\.?C\.?P\.?A\.?)\b", re.I
)


def _is_oneinfo_layout(text: str) -> bool:
    """1Info filings have no eMarketStorage 'Informazione Regolamentata' cover page. They appear
    in several per-issuer renderings (bilingual 'ALLEGATO/ANNEX'; Italian-only). Detect by the
    absence of the cover and the presence of an Allegato 3F section-1 header."""
    head = text[:300]
    if "Informazione" in head and "Regolamentata" in head:
        return False
    return bool(
        re.search(r"ALLEGATO/ANNEX|Nome\s*/\s*First Name", text)
        or re.search(r"(?m)^\s*1\s+Dati relativi alla persona", text)
        or "COMUNICAZIONE INTERNAL DEALING" in head.upper()
    )


def parse_volume(raw: str | None) -> Decimal | None:
    """Parse a volume. Dot-grouped thousands ('460.634' -> 460634); else like parse_decimal."""
    if raw is None:
        return None
    s = raw.strip()
    if re.fullmatch(r"\d{1,3}(\.\d{3})+", s):  # 1.234.567 style thousands
        s = s.replace(".", "")
    return parse_decimal(s)


def _parse_italian_date(text: str) -> date | None:
    m = re.search(r"\b(\d{1,2})\s+([A-Za-zàèéìòù]+)\s+(20\d\d)\b", text)
    if not m:
        return None
    month = ITALIAN_MONTHS.get(m.group(2).lower())
    if not month:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(1)))
    except ValueError:
        return None


_SECTION_HEADERS = (
    (1, re.compile(r"Dati relativi alla persona che esercit")),
    (2, re.compile(r"Motivo della notifica")),
    (3, re.compile(r"Dati relativi all['’]emittente")),
    (4, re.compile(r"Dati relativi all['’]operazione")),
)


def _oneinfo_sections(text: str) -> dict[int, str]:
    """Split an Allegato 3F into its four sections (1 person, 2 reason, 3 issuer, 4 transaction)
    by descriptive header phrases. Works across 1Info renderings where the section *number* may
    be prefixed (Italian-only) or floated onto its own line (bilingual)."""
    found = [(num, m.start()) for num, rx in _SECTION_HEADERS if (m := rx.search(text))]
    found.sort(key=lambda x: x[1])
    sections: dict[int, str] = {}
    for i, (num, start) in enumerate(found):
        end = found[i + 1][1] if i + 1 < len(found) else len(text)
        sections[num] = text[start:end]
    return sections


def _parse_oneinfo_fields(text: str, out: ParsedFiling) -> None:
    sec = _oneinfo_sections(text)
    s1, s2, s3 = sec.get(1, ""), sec.get(2, ""), sec.get(3, "")

    # person (section 1) — 'a) Nome[/First Name] <NAME>' (+ optional 'Cognome <LAST>')
    name = _first(r"a\)\s*Nome(?:\s*/\s*First Name)?\s+([A-ZÀ-Ù][^\n]+)", s1) or _first(
        r"Denominazione[^\n]*?\s+([A-ZÀ-Ù][^\n]+)", s1
    )
    last = _first(r"Cognome(?:\s*/\s*Last Name)?\s+([A-ZÀ-Ù][^\n]+)", s1)
    if name:
        name = re.sub(r"\s+", " ", name).strip()
        out.person_full_name = f"{name} {last}".strip() if last else name
        out.is_legal_person = bool(_LEGAL_SUFFIX.search(out.person_full_name or ""))

    # position / role (section 2) — Italian 'Posizione/qualifica X' or bilingual 'Status(1) - X'
    role = (
        _first(r"Posizione\s*[-/]\s*[Qq]ualifica\s+([^\n]+)", s2)
        or _first(r"Position\s*-\s*Status\s*\(?\d?\)?\s*-?\s*([^\n]+)", s2)
        or _first(r"Posizione[^\n]*?\bqualifica\b\s*[-:]?\s*([^\n]+)", s2)
    )
    if role:
        out.position_status = role.strip()
        out.role_raw = role.strip()

    _amended = re.search(r"\bModifica\b", s2) and not re.search(
        r"Nuova notifica|Notifica [Ii]niziale", s2
    )
    out.notification_type = "amendment" if _amended else "initial"

    # issuer (section 3) — 'a) Nome[/Name] <ISSUER>'
    issuer = _first(r"a\)\s*Nome(?:\s*/\s*Name)?\s+([A-ZÀ-Ù0-9][^\n]+)", s3)
    if issuer:
        out.issuer_name = re.sub(r"\s+", " ", issuer).strip()
    out.issuer_lei = _first(r"LEI\s*\(?\d?\)?\s*:?\s*([A-Z0-9]{20})\b", s3)


def _split_operations_oneinfo(text: str) -> list[str]:
    sec4 = _oneinfo_sections(text).get(4, text)
    # operation headers: 'Operazione/Operation - 1' or 'Operazione 1' (dash optional, line-start)
    parts = re.split(r"(?m)^\s*Operazione\s*(?:/\s*Operation)?\s*-?\s*\d+\b", sec4)
    return [p for p in parts[1:] if p.strip()] if len(parts) > 1 else [sec4]


def _parse_operation_oneinfo(
    block: str, seq: int
) -> tuple[ParsedTransaction, list[tuple[Decimal | None, Decimal | None]]]:
    tx = ParsedTransaction(seq=seq)
    # instrument
    instr = (
        _first(r"strumento finanziario,\s*tipo di\s+([^\n]+?)\s+strumento\s*/", block)
        or _first(r"a\)\s*Descrizione dello\s+([A-ZÀ-Ù][^\n]+)", block)
        or _first(r"\b(Azion[ei][^\n/]{0,30}|Diritti[^\n/]{0,40})", block)
    )
    if instr:
        tx.instrument_type = re.sub(r"\s+", " ", instr.split("/")[0]).strip()[:120]
    # ISIN — '... ISIN: <ISIN>' or 'Identification code <ISIN>'
    tx.isin = _first(r"ISIN:?\s*([A-Z]{2}[A-Z0-9]{9,10})\b", block) or _first(
        r"Identification code\s+([A-Z]{2}[A-Z0-9]{9,10})\b", block
    )
    # nature — 'b) Natura dell'operazione <text>' (Italian) or 'Nature of the transaction(5) <text>'
    nat = _first(r"b\)\s*Natura dell'operazione\s+([^\n]+)", block) or _first(
        r"Nature of the\s*\n?\s*transaction\s*\(?\d?\)?\s*([^\n]+)", block
    )
    if nat:
        tx.nature_raw = re.sub(r"\s+", " ", nat).strip()[:300]
    tx.transaction_type = map_transaction_type(tx.nature_raw)
    opt = re.search(r"share option programme\s*(SI\s*/\s*YES|NO|YES|SI)\b", block, re.IGNORECASE)
    if opt:
        tx.linked_to_option_programme = opt.group(1).upper().startswith(("SI", "YES"))

    # price/volume — scope to 'Prezzo/i e volume/i' .. 'Informazioni aggregate'
    pv_block = block
    c = re.search(r"Prezzo/i e volume/i|Price\(s\) and Volume", block, re.IGNORECASE)
    d = re.search(r"Informazioni\s+aggregat[ei]|Aggregated information", block, re.IGNORECASE)
    if c:
        pv_block = block[c.start() : d.start() if d else None]
    pairs: list[tuple[Decimal | None, Decimal | None]] = []
    # bilingual: '<price> EUR <volume>'
    for p, v in re.findall(r"([\d][\d.,]*)\s*EUR\s+([\d][\d.,]*)", pv_block):
        pairs.append((parse_decimal(p), parse_volume(v)))
    # currency-prefixed: 'EUR 0 1420' or 'Euro <price> <volume>[ <ora>]' (free grants: price 0)
    if not pairs:
        for p, v in re.findall(r"(?:EUR|Euro)\s+([\d][\d.,]*)\s+([\d][\d.,]*)", pv_block):
            pairs.append((parse_decimal(p), parse_volume(v)))
    if pairs:
        tx.price, tx.volume = pairs[0]

    # date — ISO 'YYYY-MM-DD ...' or Italian 'DD mese YYYY'
    dm = re.search(
        r"(20\d\d-\d{2}-\d{2})(?:[^\n]*?(?:Dalle/From|From)\s*([\d:]+)[^\n]*?(?:alle/to|To)\s*([\d:]+))?",
        block,
    )
    if dm:
        try:
            tx.transaction_date = date.fromisoformat(dm.group(1))
        except ValueError:
            tx.transaction_date = None
        tx.time_from = _parse_time(dm.group(2))
        tx.time_to = _parse_time(dm.group(3))
    else:
        edate = re.search(r"Data dell'operazione\s+([^\n]+)", block)
        if edate:
            tx.transaction_date = _parse_italian_date(edate.group(1))
            tm = re.search(r"ore\s+(\d{1,2}:\d{2})", edate.group(1))
            tx.time_from = tx.time_to = _parse_time(tm.group(1)) if tm else None

    # venue — bilingual 'Place of the <MIC> transaction' or Italian 'sede di negoziazione: <X>'
    venue = _first(r"Place of the\s+([A-Z0-9][A-Za-z0-9 .,&'-]*?)\s+transaction", block) or _first(
        r"sede di negoziazione:?\s*([^\n]+)", block
    )
    if venue:
        tx.venue = venue.strip()
        mic = _first(r"Codice di identificazione:?\s*([A-Z0-9]{3,6})\b", block) or (
            re.search(r"\b([A-Z0-9]{3,4})$", venue.strip()).group(1)  # type: ignore[union-attr]
            if re.search(r"\b[A-Z0-9]{3,4}$", venue.strip())
            else None
        )
        tx.venue_mic = mic
    return tx, pairs


def _expand_pairs(
    transactions: list[ParsedTransaction],
    base: ParsedTransaction,
    pairs: list[tuple[Decimal | None, Decimal | None]],
) -> None:
    if len(pairs) <= 1:
        transactions.append(base)
        return
    for price, volume in pairs:  # one row per price/volume pair (CLAUDE.md D-006)
        row = base.model_copy()
        row.seq = len(transactions) + 1
        row.price = price
        row.volume = volume
        transactions.append(row)


def parse_filing(
    pdf_path: str | Path,
    source_url: str | None = None,
    source: str = "emarketstorage",
    meta: dict | None = None,
) -> ParsedFiling:
    """Parse an Allegato 3F PDF into a ``ParsedFiling``. Never raises on malformed content.

    ``meta`` carries source-listing hints (filing_id, issuer_name, published_at, title, market)
    used to fill fields the PDF layout does not expose — notably 1Info, whose PDFs have no
    eMarketStorage cover page (so no 'Comunicato n.' id).
    """
    try:
        text = extract_text(pdf_path)
    except Exception:  # noqa: BLE001 - corrupt/non-PDF input
        return ParsedFiling(
            source=source, source_url=source_url, parse_status="failed", raw_text=""
        )

    out = ParsedFiling(source=source, source_url=source_url, raw_text=text)

    # If this does not look like an Allegato 3F at all, mark failed but keep raw text.
    _markers = (
        "persone che esercitano funzioni",
        "persona che esercita funzioni",
        "managerial responsibilities",
        "Dati relativi alla persona",
    )
    if not any(m in text for m in _markers):
        out.parse_status = "failed"
        if not _is_oneinfo_layout(text):
            _parse_cover(text, out)
        _apply_meta(out, meta)
        return out

    transactions: list[ParsedTransaction] = []
    if _is_oneinfo_layout(text):
        _parse_oneinfo_fields(text, out)
        out.role_code = map_role_code(out.role_raw, out.position_status)
        for op_block in _split_operations_oneinfo(text):
            base, pairs = _parse_operation_oneinfo(op_block, seq=len(transactions) + 1)
            _expand_pairs(transactions, base, pairs)
    else:
        _parse_cover(text, out)
        _parse_person(text, out)
        _parse_issuer(text, out)
        out.role_code = map_role_code(out.role_raw, out.position_status)
        for op_block in _split_operations(text):
            base, pairs = _parse_operation(op_block, seq=len(transactions) + 1)
            _expand_pairs(transactions, base, pairs)

    out.transactions = transactions
    _apply_meta(out, meta)

    # decide parse_status
    required_ok = bool(out.filing_id and out.issuer_name and out.person_full_name)
    tx_ok = bool(transactions) and all(
        t.price is not None and t.volume is not None and t.transaction_date is not None
        for t in transactions
    )
    out.parse_status = "success" if (required_ok and tx_ok) else "partial"
    return out


def _apply_meta(out: ParsedFiling, meta: dict | None) -> None:
    """Fill missing top-level fields from source-listing metadata (does not override PDF data)."""
    if not meta:
        return
    if not out.filing_id and meta.get("filing_id"):
        out.filing_id = str(meta["filing_id"])
    if not out.issuer_name and meta.get("issuer_name"):
        out.issuer_name = meta["issuer_name"]
    if not out.title and meta.get("title"):
        out.title = meta["title"]
    if not out.market and meta.get("market"):
        out.market = meta["market"]
    if out.published_at is None and meta.get("published_at") is not None:
        out.published_at = meta["published_at"]
