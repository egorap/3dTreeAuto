"""High-level service helpers for parsing orders with GPT."""

from __future__ import annotations

import json
import logging
import re
from typing import List, Optional

from . import client, prompt
from .schema import GPTParseRequest, OrderRow, ParseResult


def _safe_json_loads(payload: str) -> dict:
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ValueError("raw_json column does not contain valid JSON data") from exc


def _extract_personalization(item: dict) -> str:
    candidates: List[str] = []

    def ensure_list(value):
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return parsed
            except (json.JSONDecodeError, TypeError, ValueError):
                return []
        return []

    def find_personalization(options_list):
        for option in options_list:
            name = str(option.get("name") or "").strip()
            value = str(option.get("value") or "").strip()
            if not value:
                continue
            lower_name = name.lower()
            if any(keyword in lower_name for keyword in ("personalization", "personalisation", "list of names")):
                return value
        return None

    # Check top-level options first
    for options_key in ("options", "extendedOptions"):
        options = ensure_list(item.get(options_key) or [])
        if not isinstance(options, list):
            continue
        personalization_value = find_personalization(options)
        if personalization_value:
            return personalization_value

    json_data = item.get("jsonData") or item.get("json_data")
    if isinstance(json_data, dict):
        for options_key in ("options", "extendedOptions"):
            options = ensure_list(json_data.get(options_key) or [])
            personalization_value = find_personalization(options)
            if personalization_value:
                return personalization_value

        for key in ("personalization", "names", "customization"):
            value = json_data.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    # Fallbacks for older data shapes
    raw = item.get("personalization") or item.get("customization")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()

    return ""


def _extract_buyer_note(item: dict) -> Optional[str]:
    for key in ("buyerNotes", "customerNotes", "note_from_buyer", "noteFromBuyer"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    json_data = item.get("jsonData") or item.get("json_data")
    if isinstance(json_data, dict):
        value = json_data.get("note_from_buyer")
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def _parse_names(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(v).strip() for v in parsed if str(v).strip()]
        except json.JSONDecodeError:
            pass
        return [part.strip() for part in value.split(",") if part.strip()]
    return []


def _normalise_model_response(content: str, default_year: str = "2025") -> ParseResult:
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Model response is not valid JSON: {content}") from exc

    names = _parse_names(data.get("names"))
    requested_proof = bool(data.get("requestedProof") or data.get("requested_proof"))
    needs_manual = bool(
        data.get("needsManualReview") or data.get("needs_manual_review") or data.get("manualReview")
    )
    notes_value = data.get("notes") or data.get("explanation") or data.get("comment")
    notes = str(notes_value).strip() if isinstance(notes_value, str) and notes_value.strip() else None

    year_value = data.get("year")
    if isinstance(year_value, int):
        year_value = str(year_value)
    if not isinstance(year_value, str) or not year_value.strip():
        year_value = "2025"
    else:
        match = re.search(r"(20\\d{2}|19\\d{2})", year_value)
        year_value = match.group(1) if match else "2025"

    return ParseResult(
        names=names,
        requested_proof=requested_proof,
        needs_manual_review=needs_manual,
        year=year_value,
        notes=notes,
        raw_response=content,
    )


def build_request(row: OrderRow) -> GPTParseRequest:
    item_payload = _safe_json_loads(row.raw_json)

    personalization_text = _extract_personalization(item_payload)
    buyer_note = row.buyer_note or _extract_buyer_note(item_payload)

    return GPTParseRequest(
        order_number=row.order_number,
        item_id=row.item_id,
        personalization_text=personalization_text,
        buyer_note=buyer_note,
        quantity=row.quantity,
        product=row.product,
        default_year=row.year or "2025",
    )

def parse_order(row: OrderRow, request: GPTParseRequest | None = None) -> ParseResult:
    """Create the GPT request for a DB row and return the parsed response."""

    if request is None:
        request = build_request(row)

    messages = prompt.build_messages(request)
    logging.debug("Sending GPT request for order %s item %s", row.order_number, row.item_id)

    response_text = client.fetch_completion(messages)
    return _normalise_model_response(response_text, request.default_year)
