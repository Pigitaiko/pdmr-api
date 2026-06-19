"""Idempotent persistence of a ParsedFiling into the database.

Dedup rules (CLAUDE.md): issuer on LEI (fallback name), person on full_name, filing on filing_id.
Re-ingesting the same filing_id is a no-op. One transaction per (price, volume) pair.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Filing, Issuer, Person, Transaction
from scraper.parser import ParsedFiling


async def _get_or_create_issuer(session: AsyncSession, parsed: ParsedFiling) -> Issuer:
    issuer: Issuer | None = None
    if parsed.issuer_lei:
        issuer = (
            await session.execute(select(Issuer).where(Issuer.lei == parsed.issuer_lei))
        ).scalar_one_or_none()
    if issuer is None and parsed.issuer_name:
        issuer = (
            await session.execute(select(Issuer).where(Issuer.name == parsed.issuer_name))
        ).scalar_one_or_none()
    if issuer is None:
        issuer = Issuer(
            name=parsed.issuer_name or "UNKNOWN",
            short_name=(parsed.issuer_name or "").split(" SPA")[0] or None,
            lei=parsed.issuer_lei,
        )
        session.add(issuer)
        await session.flush()
    elif parsed.issuer_lei and not issuer.lei:
        issuer.lei = parsed.issuer_lei
    return issuer


async def _get_or_create_person(session: AsyncSession, parsed: ParsedFiling) -> Person:
    full_name = parsed.person_full_name or "UNKNOWN"
    person = (
        await session.execute(select(Person).where(Person.full_name == full_name))
    ).scalar_one_or_none()
    if person is None:
        person = Person(
            full_name=full_name,
            first_name=parsed.person_first_name,
            last_name=parsed.person_last_name,
            is_legal_person=parsed.is_legal_person,
        )
        session.add(person)
        await session.flush()
    return person


async def upsert_filing(session: AsyncSession, parsed: ParsedFiling) -> tuple[Filing, bool]:
    """Insert the filing (+issuer/person/transactions) if new. Returns (filing, created)."""
    if not parsed.filing_id:
        raise ValueError("cannot persist a filing without filing_id")

    existing = (
        await session.execute(select(Filing).where(Filing.filing_id == parsed.filing_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing, False

    issuer = await _get_or_create_issuer(session, parsed)
    person = await _get_or_create_person(session, parsed)

    filing = Filing(
        filing_id=parsed.filing_id,
        issuer_id=issuer.id,
        person_id=person.id,
        source=parsed.source,
        source_url=parsed.source_url,
        title=parsed.title,
        market=parsed.market,
        tipologia=parsed.tipologia,
        position_status=parsed.position_status,
        role_raw=parsed.role_raw,
        role_code=parsed.role_code,
        notification_type=parsed.notification_type,
        published_at=parsed.published_at,
        parse_status=parsed.parse_status,
        raw_text=parsed.raw_text,
    )
    filing.transactions = [
        Transaction(
            seq=t.seq,
            instrument_type=t.instrument_type,
            isin=t.isin,
            nature_raw=t.nature_raw,
            transaction_type=t.transaction_type,
            price=t.price,
            currency=t.currency,
            volume=t.volume,
            transaction_date=t.transaction_date,
            time_from=t.time_from,
            time_to=t.time_to,
            venue=t.venue,
            venue_mic=t.venue_mic,
            linked_to_option_programme=t.linked_to_option_programme,
        )
        for t in parsed.transactions
    ]
    session.add(filing)
    await session.flush()
    return filing, True
