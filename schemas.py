"""Pydantic response schemas for the REST API.

Monetary values are serialised as strings (decimal-safe); timestamps as ISO-8601 UTC.
"""

from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_serializer

if TYPE_CHECKING:
    from models import Transaction


class _Issuer(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    lei: str | None


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
    # denormalised filing context (populated when the filing/issuer/person are eager-loaded)
    issuer_name: str | None = None
    person_name: str | None = None
    is_legal_person: bool | None = None
    role_code: str | None = None
    position_status: str | None = None
    filing_ref: str | None = None

    @field_serializer("price", "volume", "signal_value")
    def _ser_decimal(self, v: Decimal | None) -> str | None:
        return None if v is None else format(v, "f")

    @classmethod
    def from_tx(cls, tx: Transaction) -> TransactionOut:
        out = cls.model_validate(tx)
        filing = tx.filing
        if filing is not None:
            out.role_code = filing.role_code
            out.position_status = filing.position_status
            out.filing_ref = filing.filing_id
            if filing.issuer is not None:
                out.issuer_name = filing.issuer.name
            if filing.person is not None:
                out.person_name = filing.person.full_name
                out.is_legal_person = filing.person.is_legal_person
        return out


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
