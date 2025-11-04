"""Typed structures used across the GPT parsing pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class OrderRow:
    id: int
    order_number: str
    item_id: str
    raw_json: str
    product: Optional[str]
    quantity: int
    options: Optional[str]
    names: Optional[str]
    buyer_note: Optional[str]
    year: Optional[str]
    requested_proof: Optional[int]
    needs_manual_review: Optional[int]


@dataclass(frozen=True)
class GPTParseRequest:
    order_number: str
    item_id: str
    personalization_text: str
    buyer_note: Optional[str]
    quantity: int
    product: Optional[str]
    default_year: str


@dataclass(frozen=True)
class ParseResult:
    names: List[str]
    requested_proof: bool
    needs_manual_review: bool
    year: str
    notes: Optional[str]
    raw_response: str
