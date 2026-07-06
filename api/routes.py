"""REST API routes (v1). See CLAUDE.md "REST API" for the contract."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from api.deps import RateLimitDep, SessionDep
from models import Filing, Issuer, Person, Transaction
from schemas import (
    FilingListOut,
    FilingOut,
    IssuerListOut,
    IssuerOut,
    Meta,
    PersonListOut,
    PersonOut,
    TransactionListOut,
    TransactionOut,
)

router = APIRouter(prefix="/v1")

_TX_LOADERS = (
    selectinload(Transaction.filing).selectinload(Filing.issuer),
    selectinload(Transaction.filing).selectinload(Filing.person),
)


@router.get("/transactions", response_model=TransactionListOut, dependencies=[RateLimitDep])
async def list_transactions(
    session: AsyncSession = SessionDep,
    issuer: str | None = Query(None, description="issuer name or LEI substring"),
    country: str | None = Query(None, description="ISO-2 country code, e.g. IT, SE"),
    from_: date | None = Query(None, alias="from", description="transaction_date >="),
    to: date | None = Query(None, description="transaction_date <="),
    type: str | None = Query(None, description="transaction_type A/D/O"),
    role: str | None = Query(None, description="role_code (e.g. AD, CFO, DIR)"),
    min_value: Decimal | None = Query(None, ge=0, description="signal_value >="),
    person: int | None = Query(None, description="person id — that insider's full history"),
    source: str | None = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> TransactionListOut:
    conditions = []
    if type:
        conditions.append(Transaction.transaction_type == type.upper())
    if from_:
        conditions.append(Transaction.transaction_date >= from_)
    if to:
        conditions.append(Transaction.transaction_date <= to)
    if min_value is not None:
        conditions.append(Transaction.signal_value >= min_value)

    filing_conditions = []
    if role:
        filing_conditions.append(Filing.role_code == role.upper())
    if person is not None:
        filing_conditions.append(Filing.person_id == person)
    if country:
        filing_conditions.append(Filing.country == country.upper())
    if source:
        filing_conditions.append(Filing.source == source)
    if issuer:
        like = f"%{issuer}%"
        filing_conditions.append(or_(Issuer.name.ilike(like), Issuer.lei.ilike(like)))

    needs_join = bool(filing_conditions or issuer)

    base = select(Transaction)
    count_q = select(func.count()).select_from(Transaction)
    if needs_join:
        base = base.join(Transaction.filing).join(Filing.issuer, isouter=True)
        count_q = count_q.join(Transaction.filing).join(Filing.issuer, isouter=True)

    for c in conditions + filing_conditions:
        base = base.where(c)
        count_q = count_q.where(c)

    total = (await session.execute(count_q)).scalar_one()
    rows = (
        (
            await session.execute(
                base.options(*_TX_LOADERS)
                .order_by(Transaction.transaction_date.desc().nullslast(), Transaction.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return TransactionListOut(
        data=[TransactionOut.from_tx(r) for r in rows],
        meta=Meta(total=total, limit=limit, offset=offset),
    )


@router.get("/transactions/{tx_id}", response_model=TransactionOut, dependencies=[RateLimitDep])
async def get_transaction(tx_id: int, session: AsyncSession = SessionDep) -> TransactionOut:
    row = (
        await session.execute(
            select(Transaction).where(Transaction.id == tx_id).options(*_TX_LOADERS)
        )
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="transaction not found")
    return TransactionOut.from_tx(row)


@router.get("/issuers", response_model=IssuerListOut, dependencies=[RateLimitDep])
async def list_issuers(
    session: AsyncSession = SessionDep,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> IssuerListOut:
    total = (await session.execute(select(func.count()).select_from(Issuer))).scalar_one()
    rows = (
        await session.execute(
            select(Issuer, func.count(Filing.id))
            .outerjoin(Filing, Filing.issuer_id == Issuer.id)
            .group_by(Issuer.id)
            .order_by(Issuer.name)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    data = []
    for issuer, count in rows:
        out = IssuerOut.model_validate(issuer)
        out.filing_count = count
        data.append(out)
    return IssuerListOut(data=data, meta=Meta(total=total, limit=limit, offset=offset))


@router.get("/persons", response_model=PersonListOut, dependencies=[RateLimitDep])
async def list_persons(
    session: AsyncSession = SessionDep,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> PersonListOut:
    total = (await session.execute(select(func.count()).select_from(Person))).scalar_one()
    rows = (
        await session.execute(
            select(Person, func.count(Filing.id))
            .outerjoin(Filing, Filing.person_id == Person.id)
            .group_by(Person.id)
            .order_by(Person.full_name)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    data = []
    for person, count in rows:
        out = PersonOut.model_validate(person)
        out.filing_count = count
        data.append(out)
    return PersonListOut(data=data, meta=Meta(total=total, limit=limit, offset=offset))


@router.get("/feed", response_model=FilingListOut, dependencies=[RateLimitDep])
async def feed(
    session: AsyncSession = SessionDep,
    since: datetime | None = Query(None, description="filings ingested after this ISO timestamp"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> FilingListOut:
    conditions = []
    if since:
        conditions.append(Filing.ingested_at > since)

    count_q = select(func.count()).select_from(Filing)
    base = select(Filing).options(
        selectinload(Filing.issuer),
        selectinload(Filing.person),
        selectinload(Filing.transactions),
    )
    for c in conditions:
        base = base.where(c)
        count_q = count_q.where(c)
    total = (await session.execute(count_q)).scalar_one()
    rows = (
        (await session.execute(base.order_by(Filing.ingested_at.asc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return FilingListOut(
        data=[FilingOut.model_validate(r) for r in rows],
        meta=Meta(total=total, limit=limit, offset=offset),
    )


@router.get("/signals", response_model=TransactionListOut, dependencies=[RateLimitDep])
async def signals(
    session: AsyncSession = SessionDep,
    min_value: Decimal = Query(Decimal("50000"), ge=0),
    country: str | None = Query(None, description="ISO-2 country code, e.g. IT, SE"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> TransactionListOut:
    """Open-market senior-insider buys: type=A, price>0, senior role, signal>=min_value."""
    roles = ("AD", "CFO", "CHAIR", "DIR", "MGMT")
    cond = (
        (Transaction.transaction_type == "A")
        & (Transaction.price > 0)
        & (Transaction.signal_value >= min_value)
        & (Filing.role_code.in_(roles))
    )
    if country:
        cond = cond & (Filing.country == country.upper())
    count_q = select(func.count()).select_from(Transaction).join(Transaction.filing).where(cond)
    total = (await session.execute(count_q)).scalar_one()
    rows = (
        (
            await session.execute(
                select(Transaction)
                .join(Transaction.filing)
                .where(cond)
                .options(*_TX_LOADERS)
                .order_by(Transaction.signal_value.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return TransactionListOut(
        data=[TransactionOut.from_tx(r) for r in rows],
        meta=Meta(total=total, limit=limit, offset=offset),
    )


@router.get("/admin/refresh")
async def admin_refresh(
    token: str = Query(..., description="must match the ADMIN_TOKEN env var"),
    source: str = Query("all", description="'all' or a single source, e.g. nasdaq_nordic"),
    max_pages: int = Query(2, ge=1, le=20),
) -> dict:
    """Trigger a scrape on demand (token-gated). Runs in the **background** and returns
    immediately; poll ``GET /status`` for progress and the per-source breakdown. Ingestion is
    idempotent, so re-running is safe (existing filings are skipped)."""
    from config import get_settings
    from scraper.bg import SCRAPE_STATE, launch_scrape

    settings = get_settings()
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=403, detail="invalid or unset admin token")
    started = launch_scrape(source, max_pages, trigger="manual")
    if not started:
        return {"status": "already_running", "scrape": SCRAPE_STATE}
    return {"status": "started", "source": source, "poll": "/status"}
