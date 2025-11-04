"""Production parser that processes pending orders and saves GPT results."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

CURRENT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = CURRENT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from gpt_pipeline import OrderRow  # noqa: E402
from gpt_pipeline.service import build_request, parse_order  # noqa: E402


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
    conn: sqlite3.Connection,
    product: str,
    limit: int,
    ids: Sequence[int],
    force: bool,
    include_shipped: bool,
) -> List[OrderRow]:
    clauses = ["product = ?"]
    params: List[object] = [product]

    if ids:
        placeholders = ",".join("?" for _ in ids)
        clauses.append(f"id IN ({placeholders})")
        params.extend(ids)
    elif not force:
        clauses.append("(names IS NULL OR TRIM(names) = '')")

    if not include_shipped:
        clauses.append("shipped = 0")

    where_clause = " AND ".join(clauses)

    query = f"""
        SELECT id, order_number, item_id, raw_json, product, quantity, options,
               names, buyer_note, year, requested_proof, needs_manual_review
        FROM order_items
        WHERE {where_clause}
        ORDER BY id ASC
        LIMIT ?
    """
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    rows = cursor.fetchall()

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


def update_row(
    cursor: sqlite3.Cursor,
    row_id: int,
    names: List[str],
    year: str,
    requested_proof: bool,
    needs_manual_review: bool,
) -> None:
    cursor.execute(
        """
        UPDATE order_items
        SET names = ?,
            year = ?,
            requested_proof = ?,
            needs_manual_review = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            json.dumps(names, ensure_ascii=False),
            year,
            int(requested_proof),
            int(needs_manual_review),
            row_id,
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run GPT parser on pending order items.")
    parser.add_argument(
        "--db",
        dest="db_path",
        type=Path,
        default=Path("tree3.db"),
        help="Path to the SQLite database (default: tree3.db).",
    )
    parser.add_argument(
        "--product",
        default="3d-Christmas-Tree-Ornament",
        help="Only process items for this product (default: 3d-Christmas-Tree-Ornament).",
    )
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of rows to process.")
    parser.add_argument(
        "--ids",
        help="Comma-separated list or ranges of row IDs to process (e.g., 1,5,10-15). Overrides automatic selection.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Process rows even if names already exist (useful for reprocessing).",
    )
    parser.add_argument(
        "--include-shipped",
        action="store_true",
        help="Include rows that already have shipped=1 (default: skip shipped rows).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without committing changes to the database.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed information for each processed row.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    ids = parse_id_selector(args.ids) if args.ids else []

    try:
        rows = fetch_rows(
            conn=conn,
            product=args.product,
            limit=args.limit,
            ids=ids,
            force=args.force,
            include_shipped=args.include_shipped,
        )
        if not rows:
            print("No matching rows to process.")
            return 0

        processed = 0
        updated = 0
        failures = 0

        for row in rows:
            request = build_request(row)
            try:
                result = parse_order(row, request=request)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(
                    f"[ERROR] Failed to parse row {row.id} (order {row.order_number}, item {row.item_id}): {exc}"
                )
                continue

            if args.verbose:
                print("=" * 80)
                print(f"Row {row.id} | Order {row.order_number} | Item {row.item_id}")
                print(f"Names: {', '.join(result.names) if result.names else '<none>'}")
                print(f"Year: {result.year}")
                print(f"Requested proof: {result.requested_proof}")
                print(f"Manual review: {result.needs_manual_review}")
                if result.notes:
                    print(f"Notes: {result.notes}")

            update_row(
                cursor=cursor,
                row_id=row.id,
                names=result.names,
                year=result.year,
                requested_proof=result.requested_proof,
                needs_manual_review=result.needs_manual_review,
            )
            updated += 1
            processed += 1

        if updated and not args.dry_run:
            conn.commit()
        elif args.dry_run:
            conn.rollback()

        print(
            f"Processed {processed} row(s). Updated {updated}. Failures {failures}. "
            f"{'Changes rolled back (dry run).' if args.dry_run else ''}"
        )

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
