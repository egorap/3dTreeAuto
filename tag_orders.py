"""Apply ShipStation tags for generated or manual-review orders."""

from __future__ import annotations

import argparse
import os
import sqlite3
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import requests

DEFAULT_PRODUCT = "3d-Christmas-Tree-Ornament"
API_URL = "https://ssapi.shipstation.com/orders/addtag"

GEN_TAG_ID = int(os.getenv("SS_GENERATED_TAG_ID", "130516"))
SECONDARY_TAG_ID = int(os.getenv("SS_SECONDARY_TAG_ID", "76648"))
MANUAL_TAG_ID = int(os.getenv("SS_MANUAL_TAG_ID", "130517"))
RATE_LIMIT_THRESHOLD = int(os.getenv("SS_RATE_LIMIT_THRESHOLD", "15"))


def parse_order_id_selector(selector: str) -> List[str]:
    values: List[str] = []
    for part in selector.split(","):
        part = part.strip()
        if part:
            values.append(part)
    return values


def fetch_manual_orders(
    conn: sqlite3.Connection,
    product: str,
    order_ids: Sequence[str],
    limit: Optional[int],
) -> List[Tuple[str, str]]:
    clauses = [
        "order_id IS NOT NULL",
        "TRIM(order_id) != ''",
        "tags_applied = 0",
        "product = ?",
        "(requested_proof != 0 OR needs_manual_review != 0 OR (generation_error IS NOT NULL AND TRIM(generation_error) != ''))",
    ]
    params: List[object] = [product]

    if order_ids:
        placeholders = ",".join("?" for _ in order_ids)
        clauses.append(f"order_id IN ({placeholders})")
        params.extend(order_ids)

    query = f"""
        SELECT order_id, MIN(order_number) AS order_number
        FROM order_items
        WHERE {' AND '.join(clauses)}
        GROUP BY order_id
        ORDER BY order_id ASC
    """
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    return [(row[0], row[1]) for row in cursor.fetchall()]


def fetch_generated_orders(
    conn: sqlite3.Connection,
    product: str,
    order_ids: Sequence[str],
    limit: Optional[int],
) -> List[Tuple[str, str]]:
    clauses = [
        "order_id IS NOT NULL",
        "TRIM(order_id) != ''",
        "tags_applied = 0",
        "product = ?",
    ]
    params: List[object] = [product]

    if order_ids:
        placeholders = ",".join("?" for _ in order_ids)
        clauses.append(f"order_id IN ({placeholders})")
        params.extend(order_ids)

    query = f"""
        SELECT
            order_id,
            MIN(order_number) AS order_number,
            SUM(CASE WHEN is_generated = 1 AND (generation_error IS NULL OR TRIM(generation_error) = '') THEN 1 ELSE 0 END) AS success_count,
            COUNT(*) AS total_count,
            SUM(CASE WHEN requested_proof != 0 OR needs_manual_review != 0 THEN 1 ELSE 0 END) AS manual_flags
        FROM order_items
        WHERE {' AND '.join(clauses)}
        GROUP BY order_id
        HAVING success_count = total_count AND manual_flags = 0
        ORDER BY order_id ASC
    """
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    return [(row[0], row[1]) for row in cursor.fetchall()]


def add_tag(order_id: str, tag_id: int) -> Tuple[bool, Optional[int], Optional[int], Optional[str]]:
    headers = {
        "Host": "ssapi.shipstation.com",
        "Authorization": os.getenv("SS_KEY", ""),
        "Content-Type": "application/json",
        "x-partner": os.getenv("X_PARTNER_KEY", ""),
    }
    payload = {"orderId": order_id, "tagId": tag_id}

    response = requests.post(API_URL, headers=headers, json=payload)
    remaining = response.headers.get("X-Rate-Limit-Remaining")
    reset = response.headers.get("X-Rate-Limit-Reset")

    try:
        remaining_int = int(remaining) if remaining is not None else None
    except ValueError:
        remaining_int = None

    try:
        reset_int = int(reset) if reset is not None else None
    except ValueError:
        reset_int = None

    error_text = None if response.ok else response.text
    return response.ok, remaining_int, reset_int, error_text


def apply_tags(
    cursor: sqlite3.Cursor,
    order_id: str,
    order_number: str,
    tag_ids: Sequence[int],
    dry_run: bool,
    verbose: bool,
) -> bool:
    if not tag_ids:
        return True

    for tag_id in tag_ids:
        if dry_run:
            if verbose:
                print(f"[DRY-RUN] Would apply tag {tag_id} to order {order_id} ({order_number})")
            continue

        ok, remaining, reset, error_text = add_tag(order_id, tag_id)
        if not ok:
            print(f"[ERROR] Failed to apply tag {tag_id} to order {order_id}: {error_text}")
            return False

        if verbose:
            print(f"Applied tag {tag_id} to order {order_id}. Remaining={remaining} reset={reset}")

        if remaining is not None and remaining <= RATE_LIMIT_THRESHOLD and reset is not None:
            sleep_for = max(reset + 1, 1)
            if verbose:
                print(f"Rate limit low ({remaining}); sleeping {sleep_for}s")
            time.sleep(sleep_for)

    return True


def mark_tagged(cursor: sqlite3.Cursor, order_id: str) -> None:
    cursor.execute(
        """
        UPDATE order_items
        SET tags_applied = 1,
            updated_at = CURRENT_TIMESTAMP
        WHERE order_id = ?
        """,
        (order_id,),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Apply ShipStation tags for generated/manual orders.")
    parser.add_argument(
        "--db",
        dest="db_path",
        type=Path,
        default=Path("tree3.db"),
        help="Path to the SQLite database (default: tree3.db).",
    )
    parser.add_argument(
        "--product",
        default=DEFAULT_PRODUCT,
        help=f"Only process items for this product (default: {DEFAULT_PRODUCT}).",
    )
    parser.add_argument("--limit", type=int, default=50, help="Maximum number of orders to tag in this run.")
    parser.add_argument(
        "--order-ids",
        help="Comma-separated ShipStation order IDs to process (overrides automatic selection).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without committing changes or calling the ShipStation API.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed logs.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    order_ids = parse_order_id_selector(args.order_ids) if args.order_ids else []

    try:
        remaining = args.limit if args.limit is not None else None

        manual_orders = fetch_manual_orders(
            conn=conn,
            product=args.product,
            order_ids=order_ids,
            limit=remaining,
        )

        if remaining is not None:
            remaining = max(remaining - len(manual_orders), 0)

        generated_orders: List[Tuple[str, str]] = []
        if remaining is None or remaining > 0:
            gen_limit = remaining if remaining is not None else None
            generated_orders = fetch_generated_orders(
                conn=conn,
                product=args.product,
                order_ids=order_ids,
                limit=gen_limit,
            )

        processed = 0
        successes = 0
        failures = 0

        for order_id, order_number in manual_orders:
            if args.verbose:
                print(f"Applying manual tag to order {order_id} ({order_number})")
            success = apply_tags(
                cursor=cursor,
                order_id=order_id,
                order_number=order_number,
                tag_ids=[MANUAL_TAG_ID],
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            if success:
                if not args.dry_run:
                    mark_tagged(cursor, order_id)
                successes += 1
            else:
                failures += 1
            processed += 1

        for order_id, order_number in generated_orders:
            if args.verbose:
                print(f"Applying generated tags to order {order_id} ({order_number})")
            success = apply_tags(
                cursor=cursor,
                order_id=order_id,
                order_number=order_number,
                tag_ids=[SECONDARY_TAG_ID, GEN_TAG_ID],
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            if success:
                if not args.dry_run:
                    mark_tagged(cursor, order_id)
                successes += 1
            else:
                failures += 1
            processed += 1

        if args.dry_run:
            conn.rollback()
        else:
            conn.commit()

        print(
            f"Tagging complete. Processed {processed} order(s). Success: {successes}. Failures: {failures}. "
            f"{'No API calls made (dry run).' if args.dry_run else ''}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
