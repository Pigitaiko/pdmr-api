"""Pydantic response schemas for the REST API.

Monetary values are serialised as strings (decimal-safe); timestamps as ISO-8601 UTC.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, field_serializer


class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filing_id: int
    seq: int
    instrument_type: str | None
    isin: str | None
    nature_raw: str | None
    transaction_type: str
    price: Decimal | None
    currency: str
    volume: Decimal | None
    signal_value: Decimal | None
    transaction_date: date | None
    time_from: time | None
    time_to: time | None
    venue: str | None
    venue_mic: str | None
    linked_to_option_programme: bool | None

    @field_serializer("price", "volume", "signal_value")
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return None if v is None else format(v, "f")


class IssuerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    short_name: str | None
    lei: str | None
    filing_count: int | None = None


class PersonOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    full_name: str
    first_name: str | None
    last_name: str | None
    is_legal_person: bool
    filing_count: int | None = None


class FilingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filing_id: str
    source: str
    source_url: str | None
    title: str | None
    market: str | None
    role_code: str | None
    position_status: str | None
    notification_type: str | None
    parse_status: str
    published_at: datetime | None
    ingested_at: datetime | None
    issuer: IssuerOut
    person: PersonOut
    transactions: list[TransactionOut] = []


class Meta(BaseModel):
    total: int
    limit: int
    offset: int


class TransactionListOut(BaseModel):
    data: list[TransactionOut]
    meta: Meta


class FilingListOut(BaseModel):
    data: list[FilingOut]
    meta: Meta


class IssuerListOut(BaseModel):
    data: list[IssuerOut]
    meta: Meta


class PersonListOut(BaseModel):
    data: list[PersonOut]
    meta: Meta
