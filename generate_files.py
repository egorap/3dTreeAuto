"""Generate Illustrator files for ready orders."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_PRODUCT = "3d-Christmas-Tree-Ornament"
DEFAULT_JSX = PROJECT_ROOT / "scripts" / "make_3d_tree.jsx"
DATA_JSON = PROJECT_ROOT / "data" / "tree_data.json"
DEFAULT_ILLUSTRATOR = Path(
    os.getenv(
        "ILLUSTRATOR_PATH",
        r"C:\Program Files\Adobe\Adobe Illustrator 2022\Support Files\Contents\Windows\Illustrator.exe",
    )
)
SAVE_DIR = Path(
    os.getenv(
        "SAVE_DIR",
        r"D:/APKcompany Dropbox/Kirill Apkalikov/etsy/All Orders/3D Christmas tree/2025/",
    )
).expanduser().resolve()


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


def ensure_paths(illustrator_path: Path, jsx_path: Path) -> tuple[Path, Path, Path]:
    illustrator_path = illustrator_path.expanduser().resolve()
    if not illustrator_path.exists():
        raise FileNotFoundError(
            f"Illustrator executable not found at {illustrator_path}. "
            "Specify it via --illustrator or ILLUSTRATOR_PATH."
        )

    if not jsx_path.is_absolute():
        jsx_path = (PROJECT_ROOT / jsx_path).resolve()
    else:
        jsx_path = jsx_path.expanduser().resolve()
    if not jsx_path.exists():
        raise FileNotFoundError(f"JSX script not found at {jsx_path}.")

    data_json = DATA_JSON.resolve()
    data_json.parent.mkdir(parents=True, exist_ok=True)

    if not SAVE_DIR.exists():
        raise FileNotFoundError(f"SAVE_DIR does not exist: {SAVE_DIR}")

    return illustrator_path, jsx_path, data_json


def load_rows(
    conn: sqlite3.Connection,
    product: str,
    limit: int,
    ids: Sequence[int],
    force: bool,
) -> List[sqlite3.Row]:
    clauses = ["product = ?"]
    params: List[object] = [product]

    if ids:
        placeholders = ",".join("?" for _ in ids)
        clauses.append(f"id IN ({placeholders})")
        params.extend(ids)
    else:
        clauses.append("names IS NOT NULL AND TRIM(names) != ''")
        clauses.append("requested_proof = 0")
        clauses.append("needs_manual_review = 0")
        if not force:
            clauses.append("file_found = 0")
            clauses.append("is_generated = 0")

    query = f"""
        SELECT id, order_number, item_id, names, year, is_generated
        FROM order_items
        WHERE {' AND '.join(clauses)}
        ORDER BY id ASC
        LIMIT ?
    """
    params.append(limit)

    cursor = conn.cursor()
    cursor.execute(query, params)
    return cursor.fetchall()


def normalise_names(value: str) -> List[str]:
    try:
        data = json.loads(value)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except json.JSONDecodeError:
        pass
    return [segment.strip() for segment in value.split(",") if segment.strip()]


def write_job_json(data_path: Path, names: List[str], layer_name: str, filename: str) -> None:
    payload = {
        "names": names,
        "name": names,
        "layerName": layer_name,
        "filename": filename,
    }
    data_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def run_illustrator(illustrator_path: Path, jsx_path: Path) -> None:
    print('run_illustrator started')
    result = subprocess.run(
        [str(illustrator_path), "-s", str(jsx_path)],
        cwd=str(PROJECT_ROOT),
        check=True,
        timeout=17,
    )
    time.sleep(5)
    print('run_illustrator ended')
    if result.returncode != 0:
        raise RuntimeError(f"Illustrator exited with code {result.returncode}")


def update_row(
    cursor: sqlite3.Cursor,
    row_id: int,
    filename: str,
    success: bool,
    error: Optional[str],
) -> None:
    cursor.execute(
        """
        UPDATE order_items
        SET
            is_generated = ?,
            generation_error = ?,
            output_filename = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            int(success),
            None if success else (error or ""),
            filename if success else None,
            row_id,
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate Illustrator files for ready orders.")
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
    parser.add_argument("--limit", type=int, default=25, help="Maximum number of rows to process.")
    parser.add_argument(
        "--ids",
        help="Comma-separated list or ranges of row IDs to process (e.g., 1,5,10-15).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Process rows even if they are already marked as generated.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run without committing database changes.",
    )
    parser.add_argument(
        "--illustrator",
        type=Path,
        default=DEFAULT_ILLUSTRATOR,
        help="Path to Illustrator executable (default: env ILLUSTRATOR_PATH or 2023 install).",
    )
    parser.add_argument(
        "--jsx",
        type=Path,
        default=DEFAULT_JSX,
        help="Path to the Illustrator JSX script (default: scripts/make_3d_tree.jsx).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed information for each generated file.",
    )
    return parser


def main(argv: Optional[Iterable[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    illustrator_path, jsx_path, data_json_path = ensure_paths(args.illustrator, args.jsx)

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    ids = parse_id_selector(args.ids) if args.ids else []

    try:
        rows = load_rows(
            conn=conn,
            product=args.product,
            limit=args.limit,
            ids=ids,
            force=args.force,
        )
        if not rows:
            print("No matching rows to generate.")
            return 0

        generated = 0
        failures = 0

        for row in rows:
            names = normalise_names(row["names"])
            if not names:
                logging.warning("Skipping row %s because names list is empty.", row["id"])
                update_row(cursor, row["id"], "", False, "Empty names list")
                failures += 1
                continue

            year = row["year"] or "2025"
            names_with_year = [year] + names
            layer_name = str(len(names_with_year))
            filename = f"{row['order_number']}_{row['item_id']}.pdf"
            save_path = SAVE_DIR / filename

            try:
                if len(names) > 10:
                    raise FileNotFoundError('Too Many Names')

                write_job_json(data_json_path, names_with_year, layer_name, filename)
                run_illustrator(illustrator_path, jsx_path)
                if not save_path.exists():
                    raise FileNotFoundError(f"Expected file not found: {save_path}")
                update_row(cursor, row["id"], filename, True, None)
                generated += 1

                if args.verbose:
                    print("=" * 60)
                    print(f"Row {row['id']} | Order {row['order_number']} | Item {row['item_id']}")
                    print(f"Names: {', '.join(names)}")
                    print(f"Layer: {layer_name}")
                    print(f"Output: {filename}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                logging.error(
                    "Failed to generate row %s (order %s, item %s): %s",
                    row["id"],
                    row["order_number"],
                    row["item_id"],
                    exc,
                )
                update_row(cursor, row["id"], filename, False, str(exc))

        if not args.dry_run:
            conn.commit()
        else:
            conn.rollback()

        print(
            f"Generation complete. Success: {generated}. Failures: {failures}. "
            f"{'Changes rolled back (dry run).' if args.dry_run else ''}"
        )
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
