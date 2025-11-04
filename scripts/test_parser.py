"""Interactive harness for exercising the GPT parsing pipeline."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import List, Optional, Sequence

# Ensure project root is on the path when running as a script.
CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gpt_pipeline import OrderRow
from gpt_pipeline.prompt import build_user_prompt
from gpt_pipeline.service import build_request, parse_order


def parse_id_selector(selector: str) -> List[int]:
    values: List[int] = []
    for part in selector.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_str, end_str = part.split("-", 1)
            start = int(start_str, 10)
            end = int(end_str, 10)
            if end < start:
                start, end = end, start
            values.extend(range(start, end + 1))
        else:
            values.append(int(part, 10))
    return sorted(set(values))


def fetch_rows(
    db_path: Path,
    ids: Sequence[int],
    limit: int,
    offset: int,
    product_filter: str,
) -> List[OrderRow]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()

        base_query = """
            SELECT id, order_number, item_id, raw_json, product, quantity, options, names, buyer_note, year, requested_proof, needs_manual_review
            FROM order_items
            WHERE product = ?
        """

        params: List[object] = [product_filter]

        if ids:
            placeholders = ",".join("?" for _ in ids)
            query = f"{base_query} AND id IN ({placeholders}) ORDER BY id ASC"
            params.extend(ids)
            cur.execute(query, params)
        else:
            query = (
                f"{base_query} ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?"
            )
            params.extend([limit, offset])
            cur.execute(query, params)

        rows = cur.fetchall()
    finally:
        conn.close()

    order_rows: List[OrderRow] = []
    for row in rows:
        order_rows.append(
            OrderRow(
                id=row["id"],
                order_number=row["order_number"],
                item_id=row["item_id"],
                raw_json=row["raw_json"],
                product=row["product"],
                quantity=int(row["quantity"] or 0),
                options=row["options"],
                names=row["names"],
                buyer_note=row["buyer_note"],
                year=row["year"],
                requested_proof=row["requested_proof"],
                needs_manual_review=row["needs_manual_review"],
            )
        )
    return order_rows


def parse_existing_names(value: Optional[str]) -> List[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [segment.strip() for segment in value.split(",") if segment.strip()]
def run(args: argparse.Namespace) -> None:
    ids = parse_id_selector(args.ids) if args.ids else []
    rows = fetch_rows(args.db_path, ids, args.limit, args.offset, args.product)

    if not rows:
        print("No rows matched the provided criteria.")
        return

    updater = sqlite3.connect(args.db_path)
    updater.row_factory = sqlite3.Row
    cur = updater.cursor()
    updates_made = 0

    for row in rows:
        print("=" * 80)
        print(f"Row #{row.id} | Order {row.order_number} | Item {row.item_id}")
        existing_names = parse_existing_names(row.names)
        if existing_names:
            print(f"Existing names: {', '.join(existing_names)}")
        if row.year:
            print(f"Existing year: {row.year}")
        print(f"Existing requested proof: {bool(row.requested_proof)}")
        print(f"Existing manual review: {bool(row.needs_manual_review)}")

        request = build_request(row)
        prompt_text = build_user_prompt(request)
        print("User prompt:")
        print(prompt_text)
        print()

        try:
            result = parse_order(row, request=request)
        except Exception as exc:  # noqa: BLE001
            print(f"Error while parsing: {exc}")
            print()
            continue

        print(f"GPT names: {', '.join(result.names) if result.names else '<none>'}")
        print(f"Requested proof: {result.requested_proof}")
        print(f"Needs manual review: {result.needs_manual_review}")
        print(f"Year: {result.year}")
        if result.notes:
            print(f"Notes: {result.notes}")
        if args.show_raw:
            print("Raw response:")
            print(result.raw_response)

        should_update = False
        set_clause = []
        values = []

        if not existing_names and result.names:
            set_clause.append("names = ?")
            values.append(json.dumps(result.names, ensure_ascii=False))
            should_update = True

        if (not row.year or not str(row.year).strip()) and result.year:
            set_clause.append("year = ?")
            values.append(result.year)
            should_update = True

        current_requested = 0 if row.requested_proof is None else int(row.requested_proof)
        new_requested = 1 if result.requested_proof else 0
        if current_requested != new_requested:
            set_clause.append("requested_proof = ?")
            values.append(new_requested)
            should_update = True

        current_manual = 0 if row.needs_manual_review is None else int(row.needs_manual_review)
        new_manual = 1 if result.needs_manual_review else 0
        if current_manual != new_manual:
            set_clause.append("needs_manual_review = ?")
            values.append(new_manual)
            should_update = True

        if should_update:
            set_clause.append("updated_at = CURRENT_TIMESTAMP")
            values.append(row.id)
            cur.execute(
                f"UPDATE order_items SET {', '.join(set_clause)} WHERE id = ?",
                values,
            )
            updates_made += 1
            print("Saved parsed data to database.")

        print()

    if updates_made:
        updater.commit()
        print(f"Updated {updates_made} row(s).")
    updater.close()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Test the GPT personalization parser.")
    parser.add_argument(
        "--db",
        dest="db_path",
        type=Path,
        default=Path("tree3.db"),
        help="Path to the SQLite database (default: tree3.db).",
    )
    parser.add_argument(
        "--ids",
        help="Comma-separated list or ranges of row IDs to test (e.g., 1,5,10-15).",
    )
    parser.add_argument("--limit", type=int, default=5, help="Number of rows to sample when --ids is not set.")
    parser.add_argument("--offset", type=int, default=0, help="Row offset when sampling without --ids.")
    parser.add_argument("--show-raw", action="store_true", help="Print the raw JSON response from the model.")
    parser.add_argument(
        "--product",
        default="3d-Christmas-Tree-Ornament",
        help="Limit testing to rows for this product (default: 3d-Christmas-Tree-Ornament).",
    )
    return parser


if __name__ == "__main__":
    parser = build_parser()
    run(parser.parse_args())
