import argparse
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Iterable, List, Set, Tuple

import requests

import setup_order_db

DEFAULT_PRODUCTS: List[str] = ["3d-Christmas-Tree-Ornament"]
REQUEST_TIMEOUT = 30


def configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")


def resolve_products() -> List[str]:
    raw = os.getenv("ORDER_PRODUCTS", "")
    products = [part.strip() for part in raw.split(",") if part.strip()]
    if not products:
        logging.info(
            "ORDER_PRODUCTS env var not set; defaulting to %s", ", ".join(DEFAULT_PRODUCTS)
        )
        return DEFAULT_PRODUCTS.copy()
    return products


def fetch_orders(product: str) -> List[dict]:
    api_url = os.getenv("API_URL")
    if not api_url:
        raise SystemExit("API_URL environment variable is required to download orders.")

    url = f"{api_url.rstrip('/')}/get-product-orders"
    logging.debug("Requesting %s orders from %s", product, url)

    try:
        response = requests.get(
            url,
            params={"product": product},
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        logging.error("Failed to fetch orders for %s: %s", product, exc)
        raise

    payload = response.json()
    if not isinstance(payload, list):
        logging.error("Unexpected payload for %s: %s", product, payload)
        raise ValueError("API must return a list of orders.")
    logging.info("Fetched %d order(s) for %s", len(payload), product)
    return payload


def ensure_dict(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def extract_json_data(item: dict) -> dict:
    json_data = item.get("jsonData") or item.get("json_data") or {}
    return ensure_dict(json_data)


def extract_file_found(item: dict) -> int:
    value = item.get("file_found") or item.get("fileFound")
    return 1 if bool(value) else 0


def extract_product(item: dict, json_data: dict, default_product: str) -> str:
    candidate = item.get("product") or json_data.get("product") or default_product
    return str(candidate) if candidate is not None else ""


def extract_quantity(item: dict, json_data: dict) -> int:
    value = item.get("quantity")
    if value is None:
        value = json_data.get("quantity") or json_data.get("qty")
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def extract_options(item: dict, json_data: dict) -> str:
    options = item.get("options")
    if options is None:
        options = json_data.get("options")
    if options is None:
        return "[]"
    try:
        return json.dumps(options, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return "[]"


def extract_custom_field1(order: dict, item: dict, json_data: dict) -> str:
    for key in ("customField1", "custom_field1"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    order_cf1 = order.get("advancedOptions", {}).get("customField1")
    if isinstance(order_cf1, str) and order_cf1.strip():
        return order_cf1.strip()
    json_cf1 = json_data.get("customField1") or json_data.get("custom_field1")
    if isinstance(json_cf1, str) and json_cf1.strip():
        return json_cf1.strip()
    return ""


def extract_buyer_note(order: dict, item: dict, json_data: dict) -> str:
    for key in ("buyerNotes", "customerNotes", "note_from_buyer", "noteFromBuyer"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("customerNotes", "note_from_buyer", "noteFromBuyer"):
        value = json_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    order_note = order.get("customerNotes") or order.get("giftMessage")
    if isinstance(order_note, str) and order_note.strip():
        return order_note.strip()

    return ""


def item_has_only_customized_url_option(item: dict) -> bool:
    options = item.get('jsonData').get("options") or item.get("extendedOptions") or []
    if not isinstance(options, list):
        return False
    if len(options) != 1:
        return False
    name = str(options[0].get("name") or "").strip()
    return name == "CustomizedURL"


def upsert_items(
    conn: sqlite3.Connection, orders: Iterable[dict], product_name: str
) -> Tuple[int, int, Set[Tuple[str, str]]]:
    inserted = 0
    updated = 0
    active_items: Set[Tuple[str, str]] = set()
    cur = conn.cursor()

    for order in orders:
        order_number = str(order.get("orderNumber") or order.get("order_number") or "").strip()
        if not order_number:
            logging.debug("Skipping order without orderNumber: %s", order)
            continue

        items = order.get("items") or []
        if any(item_has_only_customized_url_option(item) for item in items):
            logging.info("Skipping order %s because it contains items waiting for Amazon personalization.", order_number)
            continue

        for item in items:
            raw_item_id = (
                item.get("orderItemId")
                or item.get("order_item_id")
                or item.get("itemId")
                or item.get("id")
            )
            if raw_item_id is None:
                logging.debug("Skipping item without ID: order %s payload %s", order_number, item)
                continue
            item_id = str(raw_item_id)

            raw_json = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
            json_data = extract_json_data(item)
            product_value = extract_product(item, json_data, product_name)
            quantity_value = extract_quantity(item, json_data)
            options_value = extract_options(item, json_data)
            custom_field1_value = extract_custom_field1(order, item, json_data)
            buyer_note_value = extract_buyer_note(order, item, json_data)
            file_found_value = extract_file_found(item)

            cur.execute(
                """
                INSERT INTO order_items (order_number, item_id, raw_json, shipped, file_found, product, quantity, options, custom_field1, buyer_note)
                VALUES (?, ?, ?, 0, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(order_number, item_id) DO NOTHING
                """,
                (
                    order_number,
                    item_id,
                    raw_json,
                    file_found_value,
                    product_value,
                    quantity_value,
                    options_value,
                    custom_field1_value,
                    buyer_note_value,
                ),
            )
            if cur.rowcount:
                inserted += 1
            else:
                cur.execute(
                    """
                    UPDATE order_items
                    SET raw_json = ?, shipped = 0, file_found = ?, product = ?, quantity = ?, options = ?, custom_field1 = ?, buyer_note = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE order_number = ? AND item_id = ?
                    """,
                    (
                        raw_json,
                        file_found_value,
                        product_value,
                        quantity_value,
                        options_value,
                        custom_field1_value,
                        buyer_note_value,
                        order_number,
                        item_id,
                    ),
                )
                if cur.rowcount:
                    updated += 1

            active_items.add((order_number, item_id))

    conn.commit()
    logging.info("Upserted items: inserted=%d updated=%d", inserted, updated)
    return inserted, updated, active_items


def sync_shipped_flags(conn: sqlite3.Connection, active_items: Set[Tuple[str, str]]) -> Tuple[int, int]:
    cur = conn.cursor()

    cur.execute("SELECT order_number, item_id FROM order_items WHERE shipped = 0")
    not_shipped = {(row[0], row[1]) for row in cur.fetchall()}
    to_mark = not_shipped - active_items
    if to_mark:
        cur.executemany(
            "UPDATE order_items SET shipped = 1, updated_at = CURRENT_TIMESTAMP WHERE order_number = ? AND item_id = ?",
            list(to_mark),
        )

    cur.execute("SELECT order_number, item_id FROM order_items WHERE shipped = 1")
    already_shipped = {(row[0], row[1]) for row in cur.fetchall()}
    to_unmark = already_shipped & active_items
    if to_unmark:
        cur.executemany(
            "UPDATE order_items SET shipped = 0, updated_at = CURRENT_TIMESTAMP WHERE order_number = ? AND item_id = ?",
            list(to_unmark),
        )

    conn.commit()
    logging.info("Shipped flags updated: marked=%d unmarked=%d", len(to_mark), len(to_unmark))
    return len(to_mark), len(to_unmark)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Download current orders and update tree3 queue. "
            "Products are read from ORDER_PRODUCTS env var (default: 3d-Christmas-Tree-Ornament)."
        )
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        type=Path,
        default=Path("tree3.db"),
        help="Path to the SQLite database (default: tree3.db in the current directory).",
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    configure_logging(args.verbose)

    setup_order_db.initialise_database(args.db_path)
    conn = sqlite3.connect(args.db_path)

    try:
        products = resolve_products()
        all_active: Set[Tuple[str, str]] = set()
        total_inserted = 0
        total_updated = 0

        for product in products:
            orders = fetch_orders(product)
            inserted, updated, active_items = upsert_items(conn, orders, product)
            total_inserted += inserted
            total_updated += updated
            all_active |= active_items

        marked, unmarked = sync_shipped_flags(conn, all_active)
        logging.info(
            "Download complete: %d inserted, %d updated, %d marked shipped, %d unmarked shipped",
            total_inserted,
            total_updated,
            marked,
            unmarked,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
