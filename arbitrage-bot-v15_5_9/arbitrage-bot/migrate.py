"""
One-off migration script to v2 schema.

Safe to run multiple times. Creates a backup before any changes.
Adds missing columns and tables only if they don't already exist.

Run with:  python migrate.py
"""
from __future__ import annotations
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ── Resolve DB path ──────────────────────────────────────────────────
HERE = Path(__file__).parent
DB_PATH = HERE / "arbitrage.db"


def _backup_db() -> Path | None:
    """Copy the DB to a timestamped backup. Returns backup path."""
    if not DB_PATH.exists():
        print(f"No existing DB at {DB_PATH} — nothing to back up.")
        return None
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = HERE / f"arbitrage.db.backup.{ts}"
    shutil.copy2(DB_PATH, backup)
    print(f"✓ Backup created: {backup}")
    return backup


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    cur = conn.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cur.fetchall())


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    )
    return cur.fetchone() is not None


def _add_column_if_missing(
    conn: sqlite3.Connection, table: str, column: str, ddl: str,
) -> bool:
    """Add column if not present. Returns True if added.
    If the table doesn't exist yet, this is a logic error in the migration
    (tables should be created before columns are added to them) — we raise
    instead of silently skipping."""
    if not _table_exists(conn, table):
        raise RuntimeError(
            f"Migration ordering bug: tried to add column "
            f"{table}.{column} but table {table} doesn't exist yet. "
            f"Tables must be created before column-adds run on them."
        )
    if _column_exists(conn, table, column):
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    print(f"  ✓ added {table}.{column}")
    return True


# ── Schema changes ───────────────────────────────────────────────────

NEW_COLUMNS_REVIEW_CANDIDATES = [
    ("decision",        "TEXT NOT NULL DEFAULT 'pending'"),
    ("decision_notes",  "TEXT"),
    ("reviewed_at",     "DATETIME"),
    ("lifecycle_stage", "TEXT NOT NULL DEFAULT 'none'"),
    ("watchlist",       "BOOLEAN NOT NULL DEFAULT 0"),
    ("is_mock",         "BOOLEAN NOT NULL DEFAULT 0"),
]

NEW_COLUMNS_SCAN_RUNS = [
    ("comp_scanned",         "INTEGER NOT NULL DEFAULT 0"),
    ("comp_scored",          "INTEGER NOT NULL DEFAULT 0"),
    ("comp_exact",           "INTEGER NOT NULL DEFAULT 0"),
    ("comp_partial",         "INTEGER NOT NULL DEFAULT 0"),
    ("comp_broad_rejected",  "INTEGER NOT NULL DEFAULT 0"),
    ("comp_no_comps",        "INTEGER NOT NULL DEFAULT 0"),
    ("comp_weak_match",      "INTEGER NOT NULL DEFAULT 0"),
    # v15.4
    ("engine_version",       "TEXT NOT NULL DEFAULT 'unknown'"),
]

# Added v15.1 — split raw fetch from negative-filtering so we can see
# how much of each query's quota is wasted on accessory listings
NEW_COLUMNS_QUERY_PERFORMANCE = [
    ("raw_returned",       "INTEGER NOT NULL DEFAULT 0"),
    ("negative_filtered",  "INTEGER NOT NULL DEFAULT 0"),
    # v15.4 — failure reason counters + engine version
    ("failed_profit",         "INTEGER NOT NULL DEFAULT 0"),
    ("failed_roi",            "INTEGER NOT NULL DEFAULT 0"),
    ("failed_score",          "INTEGER NOT NULL DEFAULT 0"),
    ("failed_confidence",     "INTEGER NOT NULL DEFAULT 0"),
    ("failed_match_quality",  "INTEGER NOT NULL DEFAULT 0"),
    ("failed_active_only",    "INTEGER NOT NULL DEFAULT 0"),
    ("failed_battery_health", "INTEGER NOT NULL DEFAULT 0"),
    ("failed_risk_flags",     "INTEGER NOT NULL DEFAULT 0"),
    ("failed_comp_pool",      "INTEGER NOT NULL DEFAULT 0"),
    ("failed_no_comps",       "INTEGER NOT NULL DEFAULT 0"),
    ("failed_other",          "INTEGER NOT NULL DEFAULT 0"),
    ("engine_version",        "TEXT NOT NULL DEFAULT 'unknown'"),
]

# Added v15.4 — engine version + recheck policy fields
NEW_COLUMNS_LISTINGS = [
    ("last_scored_at",   "DATETIME"),
    ("last_seen_price",  "REAL"),
    ("is_near_miss",     "BOOLEAN NOT NULL DEFAULT 0"),
]

NEW_COLUMNS_OPPORTUNITIES = [
    ("engine_version",    "TEXT NOT NULL DEFAULT 'unknown'"),
    ("became_candidate",  "BOOLEAN NOT NULL DEFAULT 0"),
    ("failure_reasons",   "TEXT"),
]

NEW_COLUMNS_COMP_SNAPSHOTS_V15_4 = [
    ("engine_version", "TEXT NOT NULL DEFAULT 'unknown'"),
]

NEW_COLUMNS_REVIEW_CANDIDATES_V15_4 = [
    ("engine_version", "TEXT NOT NULL DEFAULT 'unknown'"),
]

# v15.5 — Valuation Engine v2 fields. Nullable so existing rows are
# unaffected; new rows populated by app.valuation.engine.
NEW_COLUMNS_OPPORTUNITIES_V15_5 = [
    ("valuation_version",         "TEXT"),
    ("valuation_method",          "TEXT"),
    ("valuation_confidence",      "REAL"),
    ("conservative_resale",       "REAL"),
    ("optimistic_resale",         "REAL"),
    ("valuation_warnings",        "TEXT"),
    ("valuation_breakdown_json",  "TEXT"),
]

NEW_COLUMNS_REVIEW_CANDIDATES_V15_5 = [
    ("valuation_version",         "TEXT"),
    ("valuation_method",          "TEXT"),
    ("valuation_confidence",      "REAL"),
    ("conservative_resale",       "REAL"),
    ("optimistic_resale",         "REAL"),
    ("valuation_warnings",        "TEXT"),
    ("valuation_breakdown_json",  "TEXT"),
]

# v15.5.1 — explicit v1/v2 separation columns. The legacy expected_resale
# column is now whichever number was used for profit math (v2 if enabled).
# These columns preserve the originals.
NEW_COLUMNS_OPPORTUNITIES_V15_5_1 = [
    ("v1_expected_resale", "REAL"),
    ("v2_expected_resale", "REAL"),
]

NEW_COLUMNS_REVIEW_CANDIDATES_V15_5_1 = [
    ("v1_expected_resale", "REAL"),
    ("v2_expected_resale", "REAL"),
]

# v15.5.3 — strict identity key for own_outcomes lookup, includes
# carrier bucket so unlocked / locked / unknown phones don't mix.
NEW_COLUMNS_NORMALIZED_LISTINGS_V15_5_3 = [
    ("valuation_identity_key", "TEXT"),
]

NEW_TABLES = {
    "review_decision_events": """
        CREATE TABLE IF NOT EXISTS review_decision_events (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id      INTEGER NOT NULL,
            previous_decision TEXT NOT NULL,
            new_decision      TEXT NOT NULL,
            reason_code       TEXT,
            notes             TEXT,
            created_at        DATETIME NOT NULL,
            FOREIGN KEY (candidate_id) REFERENCES review_candidates(id)
        )
    """,
    "purchase_records": """
        CREATE TABLE IF NOT EXISTS purchase_records (
            id                          INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id                INTEGER NOT NULL UNIQUE,
            purchased_at                DATETIME NOT NULL,
            actual_purchase_price       REAL NOT NULL,
            tax_paid                    REAL NOT NULL DEFAULT 0,
            inbound_shipping_cost       REAL NOT NULL DEFAULT 0,
            repair_cost                 REAL NOT NULL DEFAULT 0,
            misc_buy_costs              REAL NOT NULL DEFAULT 0,
            marketplace_purchased_from  TEXT,
            purchase_url                TEXT,
            purchase_notes              TEXT,
            seller_risk_notes           TEXT,
            predicted_resale            REAL NOT NULL,
            predicted_profit            REAL NOT NULL,
            predicted_roi               REAL NOT NULL,
            predicted_confidence        REAL NOT NULL,
            created_at                  DATETIME NOT NULL,
            updated_at                  DATETIME NOT NULL,
            FOREIGN KEY (candidate_id) REFERENCES review_candidates(id)
        )
    """,
    "sale_records": """
        CREATE TABLE IF NOT EXISTS sale_records (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id              INTEGER NOT NULL UNIQUE,
            sale_status              TEXT NOT NULL DEFAULT 'listed',
            listed_at                DATETIME,
            sale_date                DATETIME,
            sale_platform            TEXT,
            actual_sale_price        REAL NOT NULL DEFAULT 0,
            outbound_shipping_cost   REAL NOT NULL DEFAULT 0,
            selling_fees             REAL NOT NULL DEFAULT 0,
            payment_processing_fees  REAL NOT NULL DEFAULT 0,
            return_costs             REAL NOT NULL DEFAULT 0,
            days_to_sell             INTEGER,
            final_notes              TEXT,
            created_at               DATETIME NOT NULL,
            updated_at               DATETIME NOT NULL,
            FOREIGN KEY (purchase_id) REFERENCES purchase_records(id)
        )
    """,
    "pnl_snapshots": """
        CREATE TABLE IF NOT EXISTS pnl_snapshots (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            purchase_id            INTEGER NOT NULL UNIQUE,
            actual_gross_proceeds  REAL NOT NULL DEFAULT 0,
            actual_total_cost      REAL NOT NULL DEFAULT 0,
            actual_net_profit      REAL NOT NULL DEFAULT 0,
            actual_roi             REAL NOT NULL DEFAULT 0,
            predicted_resale       REAL NOT NULL DEFAULT 0,
            predicted_profit       REAL NOT NULL DEFAULT 0,
            predicted_roi          REAL NOT NULL DEFAULT 0,
            resale_error           REAL NOT NULL DEFAULT 0,
            profit_error           REAL NOT NULL DEFAULT 0,
            roi_error              REAL NOT NULL DEFAULT 0,
            is_finalized           BOOLEAN NOT NULL DEFAULT 0,
            updated_at             DATETIME NOT NULL,
            FOREIGN KEY (purchase_id) REFERENCES purchase_records(id)
        )
    """,
    "lifecycle_events": """
        CREATE TABLE IF NOT EXISTS lifecycle_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id    INTEGER NOT NULL,
            purchase_id     INTEGER,
            sale_id         INTEGER,
            event_type      TEXT NOT NULL,
            previous_stage  TEXT,
            new_stage       TEXT,
            payload         TEXT,
            notes           TEXT,
            occurred_at     DATETIME NOT NULL,
            created_at      DATETIME NOT NULL,
            FOREIGN KEY (candidate_id) REFERENCES review_candidates(id),
            FOREIGN KEY (purchase_id) REFERENCES purchase_records(id),
            FOREIGN KEY (sale_id) REFERENCES sale_records(id)
        )
    """,
    "query_performance": """
        CREATE TABLE IF NOT EXISTS query_performance (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_run_id           INTEGER NOT NULL,
            query_terms           TEXT NOT NULL,
            category              TEXT NOT NULL,
            raw_returned          INTEGER NOT NULL DEFAULT 0,
            negative_filtered     INTEGER NOT NULL DEFAULT 0,
            listings_fetched      INTEGER NOT NULL DEFAULT 0,
            new_listings          INTEGER NOT NULL DEFAULT 0,
            duplicates_skipped    INTEGER NOT NULL DEFAULT 0,
            listings_scored       INTEGER NOT NULL DEFAULT 0,
            exact_match_total     INTEGER NOT NULL DEFAULT 0,
            partial_match_total   INTEGER NOT NULL DEFAULT 0,
            broad_rejected_total  INTEGER NOT NULL DEFAULT 0,
            candidates_created    INTEGER NOT NULL DEFAULT 0,
            alerts_sent           INTEGER NOT NULL DEFAULT 0,
            created_at            DATETIME NOT NULL,
            FOREIGN KEY (scan_run_id) REFERENCES scan_runs(id)
        )
    """,
}

# Columns that may need adding to comp_snapshots (added in v13)
NEW_COLUMNS_COMP_SNAPSHOTS = [
    ("opportunity_id", "INTEGER"),
    ("titles",         "TEXT"),
    ("match_quality",  "REAL DEFAULT 0"),
    ("match_details",  "TEXT DEFAULT ''"),
]

INDEXES = [
    ("idx_review_decision_events_candidate",
     "CREATE INDEX IF NOT EXISTS idx_review_decision_events_candidate "
     "ON review_decision_events(candidate_id)"),
    ("idx_purchase_records_candidate",
     "CREATE INDEX IF NOT EXISTS idx_purchase_records_candidate "
     "ON purchase_records(candidate_id)"),
    ("idx_sale_records_purchase",
     "CREATE INDEX IF NOT EXISTS idx_sale_records_purchase "
     "ON sale_records(purchase_id)"),
    ("idx_pnl_snapshots_purchase",
     "CREATE INDEX IF NOT EXISTS idx_pnl_snapshots_purchase "
     "ON pnl_snapshots(purchase_id)"),
    ("idx_lifecycle_events_candidate",
     "CREATE INDEX IF NOT EXISTS idx_lifecycle_events_candidate "
     "ON lifecycle_events(candidate_id)"),
    ("idx_comp_snapshots_opportunity",
     "CREATE INDEX IF NOT EXISTS idx_comp_snapshots_opportunity "
     "ON comp_snapshots(opportunity_id)"),
    ("idx_query_performance_scan",
     "CREATE INDEX IF NOT EXISTS idx_query_performance_scan "
     "ON query_performance(scan_run_id)"),
    ("idx_query_performance_terms",
     "CREATE INDEX IF NOT EXISTS idx_query_performance_terms "
     "ON query_performance(query_terms)"),
    ("idx_normalized_listings_valuation_key",
     "CREATE INDEX IF NOT EXISTS idx_normalized_listings_valuation_key "
     "ON normalized_listings(valuation_identity_key)"),
]


# ── Data backfill ────────────────────────────────────────────────────

def _backfill_decisions(conn: sqlite3.Connection) -> int:
    """
    Map legacy `status` values to the new `decision` field where decision
    is still 'pending' (i.e. wasn't already migrated).
    """
    if not _table_exists(conn, "review_candidates"):
        return 0

    rows = conn.execute("""
        SELECT id, status, source FROM review_candidates
        WHERE decision = 'pending'
    """).fetchall()

    updated = 0
    for cid, status, source in rows:
        new_decision = "pending"
        if status == "approved":
            new_decision = "approved"
        elif status == "rejected":
            # Was the candidate from the mock source? Tag it as such.
            new_decision = "rejected_mock" if source == "mock" else "rejected_other"

        if new_decision != "pending":
            conn.execute(
                "UPDATE review_candidates SET decision = ?, reviewed_at = created_at "
                "WHERE id = ?",
                (new_decision, cid),
            )
            # Insert a synthesized event so the audit trail isn't empty
            conn.execute("""
                INSERT INTO review_decision_events
                  (candidate_id, previous_decision, new_decision,
                   reason_code, notes, created_at)
                VALUES (?, 'pending', ?, ?, 'backfilled from legacy status',
                        (SELECT created_at FROM review_candidates WHERE id = ?))
            """, (cid, new_decision, new_decision, cid))
            updated += 1

    return updated


def _backfill_mock_flag(conn: sqlite3.Connection) -> int:
    """Mark candidates whose source is 'mock' as is_mock = 1."""
    if not _table_exists(conn, "review_candidates"):
        return 0
    cur = conn.execute(
        "UPDATE review_candidates SET is_mock = 1 "
        "WHERE source = 'mock' AND is_mock = 0"
    )
    return cur.rowcount


# ── Main ────────────────────────────────────────────────────────────

def migrate() -> None:
    print(f"\n=== Arbitrage Bot Schema Migration ===\n")

    backup = _backup_db()

    if not DB_PATH.exists():
        print("\nNo DB found. SQLAlchemy will create a fresh one on next start.")
        print("Migration skipped.")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    try:
        # 1. Create new tables FIRST
        # Important: tables must exist before we try ALTER TABLE on them.
        # If a table is created here with the latest schema, the column-add
        # step below will simply skip it (idempotent).
        print("\n[1/8] Creating new tables...")
        for name, ddl in NEW_TABLES.items():
            existed = _table_exists(conn, name)
            conn.execute(ddl)
            if existed:
                print(f"  · {name} already exists — skipped")
            else:
                print(f"  ✓ created {name}")

        # 2. Add columns to existing tables
        print("\n[2/8] Adding new columns to review_candidates...")
        added_cols = 0
        for col, ddl in NEW_COLUMNS_REVIEW_CANDIDATES:
            if _add_column_if_missing(conn, "review_candidates", col, ddl):
                added_cols += 1
        if added_cols == 0:
            print("  · no new columns needed (already migrated)")

        # 3. Comp snapshots column upgrades
        print("\n[3/8] Adding new columns to comp_snapshots...")
        added_comp = 0
        for col, ddl in NEW_COLUMNS_COMP_SNAPSHOTS:
            if _add_column_if_missing(conn, "comp_snapshots", col, ddl):
                added_comp += 1
        if added_comp == 0:
            print("  · no new columns needed (already migrated)")

        # 4. Scan runs column upgrades (v14)
        print("\n[4/8] Adding new columns to scan_runs...")
        added_scan = 0
        for col, ddl in NEW_COLUMNS_SCAN_RUNS:
            if _add_column_if_missing(conn, "scan_runs", col, ddl):
                added_scan += 1
        if added_scan == 0:
            print("  · no new columns needed (already migrated)")

        # 5. Query performance column upgrades (v15.1 + v15.4)
        # Now safe — table is guaranteed to exist after step 1.
        print("\n[5/8] Adding new columns to query_performance...")
        added_qp = 0
        for col, ddl in NEW_COLUMNS_QUERY_PERFORMANCE:
            if _add_column_if_missing(conn, "query_performance", col, ddl):
                added_qp += 1
        if added_qp == 0:
            print("  · no new columns needed (already migrated)")

        # 5b. v15.4 column upgrades — engine_version + failure tracking
        print("\n[6/9] Adding v15.4 columns (engine_version + recheck fields)...")
        v154_added = 0
        for col, ddl in NEW_COLUMNS_LISTINGS:
            if _add_column_if_missing(conn, "listings", col, ddl):
                v154_added += 1
        for col, ddl in NEW_COLUMNS_OPPORTUNITIES:
            if _add_column_if_missing(conn, "opportunities", col, ddl):
                v154_added += 1
        for col, ddl in NEW_COLUMNS_COMP_SNAPSHOTS_V15_4:
            if _add_column_if_missing(conn, "comp_snapshots", col, ddl):
                v154_added += 1
        for col, ddl in NEW_COLUMNS_REVIEW_CANDIDATES_V15_4:
            if _add_column_if_missing(conn, "review_candidates", col, ddl):
                v154_added += 1
        if v154_added == 0:
            print("  · no new v15.4 columns needed (already migrated)")

        # 5c. v15.5 column upgrades — Valuation Engine v2
        print("\n[7/10] Adding v15.5 columns (valuation engine v2)...")
        v155_added = 0
        for col, ddl in NEW_COLUMNS_OPPORTUNITIES_V15_5:
            if _add_column_if_missing(conn, "opportunities", col, ddl):
                v155_added += 1
        for col, ddl in NEW_COLUMNS_REVIEW_CANDIDATES_V15_5:
            if _add_column_if_missing(conn, "review_candidates", col, ddl):
                v155_added += 1
        if v155_added == 0:
            print("  · no new v15.5 columns needed (already migrated)")

        # 5d. v15.5.1 column upgrades — explicit v1/v2 separation
        print("\n[8/11] Adding v15.5.1 columns (v1/v2 expected_resale)...")
        v1551_added = 0
        for col, ddl in NEW_COLUMNS_OPPORTUNITIES_V15_5_1:
            if _add_column_if_missing(conn, "opportunities", col, ddl):
                v1551_added += 1
        for col, ddl in NEW_COLUMNS_REVIEW_CANDIDATES_V15_5_1:
            if _add_column_if_missing(conn, "review_candidates", col, ddl):
                v1551_added += 1
        if v1551_added == 0:
            print("  · no new v15.5.1 columns needed (already migrated)")

        # 5e. v15.5.3 column upgrades — valuation_identity_key
        print("\n[9/11] Adding v15.5.3 columns (valuation_identity_key)...")
        v1553_added = 0
        for col, ddl in NEW_COLUMNS_NORMALIZED_LISTINGS_V15_5_3:
            if _add_column_if_missing(conn, "normalized_listings", col, ddl):
                v1553_added += 1
        if v1553_added == 0:
            print("  · no new v15.5.3 columns needed (already migrated)")

        # Indexes
        print("\n[10/11] Creating indexes...")
        for name, ddl in INDEXES:
            conn.execute(ddl)
        print(f"  ✓ {len(INDEXES)} indexes ensured")

        # Backfills
        print("\n[11/11] Backfilling existing data...")
        decisions_filled = _backfill_decisions(conn)
        mocks_flagged = _backfill_mock_flag(conn)
        print(f"  ✓ {decisions_filled} candidates migrated to new decision field")
        print(f"  ✓ {mocks_flagged} candidates flagged as mock")

        conn.commit()
        print("\n✓ Migration complete.\n")
        if backup:
            print(f"Backup retained at: {backup}")
            print("(Safe to delete after verifying everything works.)")

    except Exception as e:
        conn.rollback()
        print(f"\n✗ Migration failed: {e}")
        if backup:
            print(f"Restore from backup: {backup}")
        sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
