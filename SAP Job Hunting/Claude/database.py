"""
database.py — Central SQLite schema and connection manager.
All layers share this module.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "sap_hunter.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they don't exist."""
    with get_connection() as conn:
        conn.executescript("""
        -- ─────────────────────────────────────────
        -- LAYER 1: Company database
        -- ─────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS companies (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            name                TEXT NOT NULL UNIQUE,
            website             TEXT,
            size                TEXT,           -- e.g. "500-1000", "10000+"
            hq_country          TEXT,
            hq_city             TEXT,
            remote_policy       TEXT,           -- "remote", "hybrid", "onsite", "unknown"
            glassdoor_rating    REAL,
            kununu_rating       REAL,
            sap_usage_confirmed INTEGER DEFAULT 0,   -- boolean 0/1
            sap_products        TEXT,           -- comma-separated: "CPI, BTP, S/4HANA"
            is_sap_partner      INTEGER DEFAULT 0,
            hires_contractors   INTEGER DEFAULT 0,
            visa_sponsorship    TEXT,           -- JSON list of countries
            preferred_languages TEXT,           -- "en,es,de"
            avoidance_flag      INTEGER DEFAULT 0,  -- 1 = layoff history / red flags
            avoidance_reason    TEXT,
            last_job_post_date  TEXT,           -- ISO date
            source              TEXT,           -- where we found them
            notes               TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );

        -- ─────────────────────────────────────────
        -- LAYER 2: Contact tracker
        -- ─────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS contacts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            full_name           TEXT NOT NULL,
            title               TEXT,
            company_id          INTEGER REFERENCES companies(id),
            company_name        TEXT,           -- denormalized for speed
            linkedin_url        TEXT UNIQUE,
            email               TEXT,
            timezone            TEXT,           -- e.g. "Europe/Berlin"
            country             TEXT,
            city                TEXT,
            language_preference TEXT DEFAULT 'en',  -- en / es / de
            connection_status   TEXT DEFAULT 'pending',
                                -- pending / connected / no_response / replied / meeting_set / declined
            last_interaction    TEXT,           -- ISO date
            follow_up_due       TEXT,           -- ISO date
            priority            INTEGER DEFAULT 2,  -- 1=high, 2=medium, 3=low
            role_category       TEXT,
                                -- sap_manager / hiring_manager / talent_acquisition /
                                -- it_director / sap_practice_lead / other
            notes               TEXT,
            created_at          TEXT DEFAULT (datetime('now')),
            updated_at          TEXT DEFAULT (datetime('now'))
        );

        -- ─────────────────────────────────────────
        -- LAYER 3: Email log
        -- ─────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS email_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id      INTEGER REFERENCES contacts(id),
            subject         TEXT,
            body            TEXT,
            sent_at         TEXT DEFAULT (datetime('now')),
            status          TEXT DEFAULT 'sent',    -- sent / failed / bounced
            template_used   TEXT,
            error_msg       TEXT
        );

        -- ─────────────────────────────────────────
        -- LAYER 4: Message drafts (personalized)
        -- ─────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS message_drafts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            contact_id      INTEGER REFERENCES contacts(id),
            language        TEXT,
            subject         TEXT,
            body            TEXT,
            generated_at    TEXT DEFAULT (datetime('now')),
            used            INTEGER DEFAULT 0
        );
        """)
    print(f"[DB] Database initialized at {DB_PATH}")


if __name__ == "__main__":
    init_db()
