import re
import argparse
import json
import sqlite3
from pathlib import Path
from typing import List, Tuple


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_number TEXT NOT NULL,
    item_id TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    shipped INTEGER NOT NULL DEFAULT 0,
    file_found INTEGER NOT NULL DEFAULT 0,
    product TEXT,
    quantity INTEGER NOT NULL DEFAULT 0,
    options TEXT,
    custom_field1 TEXT,
    buyer_note TEXT,
    year TEXT DEFAULT '2025',
    is_generated INTEGER NOT NULL DEFAULT 0,
    generation_error TEXT,
    output_filename TEXT,
    requested_proof INTEGER NOT NULL DEFAULT 0,
    needs_manual_review INTEGER NOT NULL DEFAULT 0,
    names TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(order_number, item_id)
);
"""

ADD_COLUMNS = {
    "names": "ALTER TABLE order_items ADD COLUMN names TEXT",
    "shipped": "ALTER TABLE order_items ADD COLUMN shipped INTEGER NOT NULL DEFAULT 0",
    "file_found": "ALTER TABLE order_items ADD COLUMN file_found INTEGER NOT NULL DEFAULT 0",
    "product": "ALTER TABLE order_items ADD COLUMN product TEXT",
    "quantity": "ALTER TABLE order_items ADD COLUMN quantity INTEGER NOT NULL DEFAULT 0",
    "options": "ALTER TABLE order_items ADD COLUMN options TEXT",
    "custom_field1": "ALTER TABLE order_items ADD COLUMN custom_field1 TEXT",
    "buyer_note": "ALTER TABLE order_items ADD COLUMN buyer_note TEXT",
    "year": "ALTER TABLE order_items ADD COLUMN year TEXT DEFAULT '2025'",
    "is_generated": "ALTER TABLE order_items ADD COLUMN is_generated INTEGER NOT NULL DEFAULT 0",
    "generation_error": "ALTER TABLE order_items ADD COLUMN generation_error TEXT",
    "output_filename": "ALTER TABLE order_items ADD COLUMN output_filename TEXT",
    "requested_proof": "ALTER TABLE order_items ADD COLUMN requested_proof INTEGER NOT NULL DEFAULT 0",
    "needs_manual_review": "ALTER TABLE order_items ADD COLUMN needs_manual_review INTEGER NOT NULL DEFAULT 0",
    "created_at": "ALTER TABLE order_items ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
    "updated_at": "ALTER TABLE order_items ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
}

CREATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_order_items_order_number ON order_items(order_number)",
    "CREATE INDEX IF NOT EXISTS idx_order_items_item_id ON order_items(item_id)",
    "CREATE INDEX IF NOT EXISTS idx_order_items_shipped ON order_items(shipped)",
    "CREATE INDEX IF NOT EXISTS idx_order_items_product ON order_items(product)",
    "CREATE INDEX IF NOT EXISTS idx_order_items_file_found ON order_items(file_found)",
    "CREATE INDEX IF NOT EXISTS idx_order_items_is_generated ON order_items(is_generated)",
]


def ensure_table(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(CREATE_TABLE_SQL)
    conn.commit()


def ensure_columns(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("PRAGMA table_info(order_items)")
    existing_cols = {row[1] for row in cur.fetchall()}

    for column, statement in ADD_COLUMNS.items():
        if column not in existing_cols:
            cur.execute(statement)

    conn.commit()


def ensure_indexes(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    for statement in CREATE_INDEXES:
        cur.execute(statement)
    conn.commit()


def normalise_quantity(value) -> int:
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def serialise_options(options) -> str:
    if options is None:
        return "[]"
    try:
        return json.dumps(options, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return "[]"


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


def ensure_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return parsed
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    return []


def extract_json_data(payload: dict) -> dict:
    json_data = payload.get("jsonData") or payload.get("json_data") or {}
    return ensure_dict(json_data)


def extract_file_found(payload: dict) -> int:
    value = payload.get("file_found") or payload.get("fileFound")
    return 1 if bool(value) else 0


def extract_buyer_note(payload: dict, json_data: dict, fallback: str = "") -> str:
    for key in ("buyerNotes", "customerNotes", "note_from_buyer", "noteFromBuyer"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for key in ("customerNotes", "note_from_buyer", "noteFromBuyer"):
        value = json_data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return fallback or ""


def extract_year(payload: dict, json_data: dict, fallback: str = "") -> str:
    def normalise_year(value) -> str | None:
        if value is None:
            return None
        if isinstance(value, int):
            value = str(value)
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        match = re.search(r"(20\\d{2}|19\\d{2})", value)
        if match:
            return match.group(1)
        return None

    for key in ("year", "Year"):
        direct = payload.get(key)
        result = normalise_year(direct)
        if result:
            return result

    for options_key in ("options", "extendedOptions"):
        options = ensure_list(payload.get(options_key) or [])
        for option in options:
            name = str(option.get("name") or "").lower()
            if "year" in name:
                result = normalise_year(option.get("value"))
                if result:
                    return result

    for key in ("year", "Year"):
        direct = json_data.get(key)
        result = normalise_year(direct)
        if result:
            return result

    for options_key in ("options", "extendedOptions"):
        options = ensure_list(json_data.get(options_key) or [])
        for option in options:
            name = str(option.get("name") or "").lower()
            if "year" in name:
                result = normalise_year(option.get("value"))
                if result:
                    return result

    return fallback or "2025"


def backfill_item_metadata(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT order_number, item_id, raw_json,
               COALESCE(product, ''), quantity, options, file_found, custom_field1, buyer_note, year,
               requested_proof, needs_manual_review
        FROM order_items
        """
    )
    rows = cur.fetchall()

    updates: List[Tuple[int, str, int, str, str, str, str, str, str, int, int]] = []
    for (
        order_number,
        item_id,
        raw_json,
        product,
        quantity,
        options,
        file_found,
        custom_field1,
        buyer_note,
        year,
        requested_proof,
        needs_manual_review,
    ) in rows:
        try:
            payload = json.loads(raw_json)
        except json.JSONDecodeError:
            continue

        json_data = extract_json_data(payload)
        new_file_found = extract_file_found(payload)
        new_custom_field1 = payload.get("customField1") or payload.get("custom_field1") or custom_field1 or ""
        if isinstance(new_custom_field1, (dict, list)):
            new_custom_field1 = ""

        new_buyer_note = extract_buyer_note(payload, json_data, buyer_note or "")
        new_year = extract_year(payload, json_data, year or "")
        new_requested_proof = int(bool(requested_proof))
        new_manual_review = int(bool(needs_manual_review))

        new_product = payload.get("product") or json_data.get("product") or product or ""
        if isinstance(new_product, (dict, list)):
            new_product = ""

        quantity_candidate = payload.get("quantity")
        if quantity_candidate is None:
            quantity_candidate = json_data.get("quantity") or json_data.get("qty")
        new_quantity = normalise_quantity(quantity_candidate) or quantity or 0

        options_source = (
            payload.get("options")
            if payload.get("options") is not None
            else json_data.get("options")
        )
        if options_source is not None:
            new_options = serialise_options(options_source)
        else:
            new_options = options if options not in (None, "") else "[]"

        changed = False
        if new_product != (product or ""):
            changed = True
        if new_quantity != (quantity or 0):
            changed = True
        if new_options != (options if options not in (None, "") else "[]"):
            changed = True
        if new_file_found != (file_found or 0):
            changed = True

        if new_custom_field1 != (custom_field1 or ""):
            changed = True
        if new_buyer_note != (buyer_note or ""):
            changed = True
        if new_year != (year or ""):
            changed = True

        if changed:
            updates.append(
                (
                    new_file_found,
                    new_product,
                    new_quantity,
                    new_options,
                    new_custom_field1,
                    new_buyer_note,
                    new_year,
                    new_requested_proof,
                    new_manual_review,
                    order_number,
                    item_id,
                )
            )

    if updates:
        cur.executemany(
            """
            UPDATE order_items
            SET file_found = ?, product = ?, quantity = ?, options = ?, custom_field1 = ?, buyer_note = ?, year = ?, requested_proof = ?, needs_manual_review = ?, updated_at = CURRENT_TIMESTAMP
            WHERE order_number = ? AND item_id = ?
            """,
            updates,
        )
        conn.commit()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Initialise or migrate the order_items SQLite table.")
    parser.add_argument(
        "--db",
        dest="db_path",
        type=Path,
        default=Path("tree3.db"),
        help="Path to the SQLite database file (default: tree3.db).",
    )
    return parser.parse_args()


def initialise_database(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        ensure_table(conn)
        ensure_columns(conn)
        ensure_indexes(conn)
        backfill_item_metadata(conn)
    finally:
        conn.close()


def main() -> None:
    args = parse_args()
    initialise_database(args.db_path)


if __name__ == "__main__":
    main()

