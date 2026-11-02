"""
Database layer.

Uses plain sqlite3 (Python stdlib) so the prototype runs with zero extra
installs. Swap-in note: every query here is plain SQL, so migrating to
PostgreSQL later mainly means swapping this module for SQLAlchemy + a
Postgres URL -- the schema below maps almost 1:1 onto the `database/`
section of the original spec (users, authorities, road_damage_reports,
complaints, complaint_status_history, notifications, upvotes).
"""
import sqlite3
import os
from contextlib import contextmanager

DB_PATH = os.environ.get("RDM_DB_PATH", os.path.join(
    os.path.dirname(__file__), "..", "data", "app.db"))


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def db_session():
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('citizen','authority','admin')),
    authority_id INTEGER REFERENCES authorities(id),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS authorities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Municipal Corporation',
    contact_email TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS road_damage_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    image_path TEXT NOT NULL,
    annotated_image_path TEXT,
    damage_type TEXT NOT NULL,
    confidence REAL NOT NULL,
    severity TEXT NOT NULL,
    bbox TEXT,                       -- JSON list [x1,y1,x2,y2]
    needs_manual_review INTEGER DEFAULT 0,
    latitude REAL,
    longitude REAL,
    address TEXT,
    city TEXT,
    state TEXT,
    country TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_ref TEXT UNIQUE NOT NULL,
    report_id INTEGER NOT NULL REFERENCES road_damage_reports(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    authority_id INTEGER REFERENCES authorities(id),
    assigned_to TEXT,
    status TEXT NOT NULL DEFAULT 'Submitted',
    is_demo INTEGER DEFAULT 1,
    repair_evidence_path TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS complaint_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    status TEXT NOT NULL,
    remarks TEXT,
    changed_by INTEGER REFERENCES users(id),
    changed_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS upvotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    complaint_id INTEGER NOT NULL REFERENCES complaints(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT DEFAULT (datetime('now')),
    UNIQUE(complaint_id, user_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    is_read INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id),
    action TEXT NOT NULL,
    details TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with db_session() as conn:
        conn.executescript(SCHEMA)
