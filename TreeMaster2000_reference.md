# TreeMaster2000 Generator Reference

## Core Data Store
- `treeItems.db` is the single queue that each script reads from and updates.
- Schema (PRAGMA table_info): `id`, `itemId`, `order_number`, `color`, `data`, `gtp_respone`, `is_parsed`, `is_generated`, `is_nested`, `names`, `requestedProof`, `customRequest`, `gpt_worked`, `gpt_response`, `gen_worked`, `orderId`, `note_from_buyer`, `tag_added`, `year`, `keepOrder`, `is_approved`, `is_shipped`.
- `data` stores the raw ShipStation item JSON; `names` and `year` are saved as JSON strings; boolean states are stored as integer flags (0/1).
- Status fields track pipeline progress: `is_parsed`/`gpt_worked` (data readiness), `is_generated`/`gen_worked` (AI file output), `is_nested` (sheeted), `is_approved`, `is_shipped`, plus hold flags `requestedProof` and `customRequest`.

## Processing Flow
1. **Order Ingestion (`download.py`)**
   - Uses `utils/OrdersGetter` to pull ShipStation orders per store/SKU, filtering out unpaid tags.
   - Normalizes color per store via `prep_data_*` helpers and inserts new rows into `items` with raw option JSON and buyer notes.
   - Reconciles fulfillment by toggling `is_shipped` for orders no longer in the API response.
2. **Personalization Parsing (`parse.py`)**
   - Splits queue into manually structured entries (`__text_design__`) and regular personalization orders.
   - Manual parser extracts comma-delimited names/year; GPT path calls `gpt-4o` with personalization text plus buyer note.
   - Saves `gpt_response`, `names`, `year`, `keepOrder`, `customRequest`, `requestedProof`; propagates proof/custom flags across the order (`sync_hold`).
3. **Illustrator File Generation (`generate_files.py`)**
   - Selects rows with `is_parsed=1`, `gpt_worked=1`, no holds, and `is_generated=0`.
   - Calls `TreeMaker2.make_tree.run`, which writes `TreeMaker2/data/current_names.json`, drives Illustrator JSX scripts, analyzes/fixes layout (`analize.py`, `fix_tree.py`), and exports `<order>_<itemId>.ai` to the production path.
   - Marks `gen_worked` and `is_generated` based on output success.
4. **Nesting & Sheet Assembly (`group.py`)**
   - Loads generated, un-nested, shippable items; groups by order and derives per-order color (Mixed if needed).
   - Calls `nestTest/nest_trees.run` to pack files by color group. For each resulting sheet, writes instructions to `data/instractions.json` and drives `utils/loadSheet.jsx` to build `nested_files/nestedTrees_<n>.ai` with embedded metadata (`main_color`, `sheet_color`, `item_ids`, `order_numbers`).
5. **Production Prep (`prep.py`)**
   - Operator pastes nesting metadata (JSON blocks) from Illustrator; script deduces station, material, and item IDs.
   - `save_job` creates a numbered `laser_job_<n>.ai`, generates Code128 barcode art via `data/save_name.json`, and automates Illustrator via `utils/saveCurrent.jsx`.
   - Posts job payload to `api.apkordertracker.com/add-job`, updates `is_nested` in the DB, and appends the job file name to ShipStation Custom Field 1 for all related orders.
6. **Tag & Exception Sync (`update_ss_tags.py`)**
   - Adds ShipStation tags for manual intervention (`130517`) or successful generation (`130516`/`76648`) based on queue status, then sets `tag_added=1` to avoid repeats.

## Supporting Utilities
- `checkerApp/` desktop UI reviews queue states, filters items, and highlights parsing/test issues before approval (`is_approved`).
- `promptCrafter.py` provides a GUI to iterate on GPT prompts and inspect responses without persisting changes.
- `stats.py` reads `names` to produce distribution reports (`name_distribution.png`).
- Illustrator automation relies on JSON hand-offs in `data/` and `TreeMaker2/data/`, plus the JSX scripts in `TreeMaker2/ill_scripts` and `utils/`.
- Remote dependencies include ShipStation (env `SS_KEY`, `X_PARTNER_KEY`), OpenAI (`parse.py`), and the production tracker API.
