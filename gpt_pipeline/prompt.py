"""Prompt construction helpers."""

from __future__ import annotations

from typing import List, Dict

from .schema import GPTParseRequest

SYSTEM_PROMPT = (
    "You are a focused assistant that extracts personalization details from e-commerce orders. "
    "Respond only with valid JSON matching this schema: "
    '{"names": ["Name"], "year": "2025", "requestedProof": false, "needsManualReview": false, "notes": ""}. '
    "A proof is requested only when the customer explicitly asks to see a preview. "
    "Flag manual review when instructions are unclear, conflicting, or request something outside the standard personalization (e.g., custom graphics, icons like paw prints, special fonts, or additional decorations). "
    "If the customer specifies a name order (for example top-to-bottom, bottom-to-top, placing a name last, etc.), keep the names in that requested order instead of flagging manual review. "
    "Names must be a clean list of individual names. Use the provided default year unless the customer clearly requests a different year."
)


def build_user_prompt(data: GPTParseRequest) -> str:
    """Create a compact user prompt using the parsed request payload."""
    sections: List[str] = []

    personalization = data.personalization_text.strip()
    if personalization:
        sections.append(f"Personalization Input: {personalization}")
    else:
        sections.append("Personalization Input: <none provided>")

    if data.buyer_note:
        sections.append(f"Buyer Note: {data.buyer_note.strip()}")

    sections.append(f"Default Year: {data.default_year}")

    return "\n".join(sections)


def build_messages(data: GPTParseRequest) -> List[Dict[str, str]]:
    """Return the chat message payload for the OpenAI API."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(data)},
    ]
