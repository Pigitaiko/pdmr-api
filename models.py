"""SQLAlchemy 2.x models for the PDMR platform (see CLAUDE.md schema)."""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Issuer(Base):
    __tablename__ = "issuers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(512), index=True)
    short_name: Mapped[str | None] = mapped_column(String(256))
    lei: Mapped[str | None] = mapped_column(String(20), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    filings: Mapped[list[Filing]] = relationship(back_populates="issuer")


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    first_name: Mapped[str | None] = mapped_column(String(256))
    last_name: Mapped[str | None] = mapped_column(String(256))
    is_legal_person: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    filings: Mapped[list[Filing]] = relationship(back_populates="person")


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    source_url: Mapped[str | None] = mapped_column(String(1024))
    title: Mapped[str | None] = mapped_column(String(1024))
    market: Mapped[str | None] = mapped_column(String(128))
    tipologia: Mapped[str | None] = mapped_column(String(32))
    position_status: Mapped[str | None] = mapped_column(String(128))
    role_raw: Mapped[str | None] = mapped_column(String(512))
    role_code: Mapped[str | None] = mapped_column(String(16), index=True)
    notification_type: Mapped[str | None] = mapped_column(String(32))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    parse_status: Mapped[str] = mapped_column(String(16), default="success", index=True)
    raw_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    issuer: Mapped[Issuer] = relationship(back_populates="filings")
    person: Mapped[Person] = relationship(back_populates="filings")
    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="filing", cascade="all, delete-orphan"
    )


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=1)
    instrument_type: Mapped[str | None] = mapped_column(String(256))
    isin: Mapped[str | None] = mapped_column(String(32), index=True)
    nature_raw: Mapped[str | None] = mapped_column(Text)
    transaction_type: Mapped[str] = mapped_column(String(4), default="O", index=True)
    price: Mapped[Decimal | None] = mapped_column(Numeric(20, 6))
    currency: Mapped[str] = mapped_column(String(8), default="EUR")
    volume: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    # generated/stored column: notional value used for signal filtering
    signal_value: Mapped[Decimal | None] = mapped_column(
        Numeric(28, 6), Computed("price * volume", persisted=True)
    )
    transaction_date: Mapped[date | None] = mapped_column(Date, index=True)
    time_from: Mapped[time | None] = mapped_column(Time)
    time_to: Mapped[time | None] = mapped_column(Time)
    venue: Mapped[str | None] = mapped_column(String(256))
    venue_mic: Mapped[str | None] = mapped_column(String(16))
    linked_to_option_programme: Mapped[bool | None] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    filing: Mapped[Filing] = relationship(back_populates="transactions")


# composite/secondary indexes
Index("ix_transactions_signal_value", Transaction.signal_value)
Index("ix_transactions_type_value", Transaction.transaction_type, Transaction.signal_value)
