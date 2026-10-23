"""SQLite connection management and schema initialization."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS farmers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL CHECK(length(trim(name)) BETWEEN 1 AND 120),
    mobile TEXT NOT NULL UNIQUE CHECK(length(mobile) = 10),
    village TEXT NOT NULL CHECK(length(trim(village)) BETWEEN 1 AND 120),
    crop TEXT NOT NULL CHECK(length(trim(crop)) BETWEEN 1 AND 80),
    password_hash TEXT,
    kisan_farmer_id TEXT,
    pm_kisan_status TEXT NOT NULL DEFAULT 'Not recorded'
        CHECK(pm_kisan_status IN (
            'Not recorded', 'Not applied', 'Applied', 'Pending', 'Approved', 'Rejected'
        )),
    pmfby_status TEXT NOT NULL DEFAULT 'Not recorded'
        CHECK(pmfby_status IN (
            'Not recorded', 'Not applied', 'Applied', 'Pending', 'Approved', 'Rejected'
        )),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,
    farmer_id INTEGER NOT NULL REFERENCES farmers(id) ON DELETE RESTRICT,
    centre_id INTEGER NOT NULL,
    slot TEXT NOT NULL,
    quantity_kg INTEGER NOT NULL CHECK(quantity_kg BETWEEN 1 AND 100000),
    actual_quantity_kg INTEGER CHECK(
        actual_quantity_kg IS NULL OR actual_quantity_kg BETWEEN 1 AND 100000
    ),
    service_date TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Waiting'
        CHECK(status IN ('Waiting', 'In Progress', 'Completed', 'Cancelled')),
    payment TEXT NOT NULL DEFAULT 'Pending' CHECK(payment IN ('Pending', 'Paid')),
    created_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS officials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bookings_service_centre
    ON bookings(service_date, centre_id, status);
CREATE INDEX IF NOT EXISTS idx_bookings_farmer
    ON bookings(farmer_id, id);
"""


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(
            current_app.config["DATABASE"], timeout=10, detect_types=sqlite3.PARSE_DECLTYPES
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        g.db = connection
    return g.db


def close_db(_error: BaseException | None = None) -> None:
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db(app: Flask) -> None:
    database_path = Path(app.config["DATABASE"])
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA)
        farmer_columns = {
            column["name"]
            for column in connection.execute("PRAGMA table_info(farmers)").fetchall()
        }
        booking_columns = {
            column["name"]
            for column in connection.execute("PRAGMA table_info(bookings)").fetchall()
        }
        migrations = {
            "kisan_farmer_id": "ALTER TABLE farmers ADD COLUMN kisan_farmer_id TEXT",
            "pm_kisan_status": """
                ALTER TABLE farmers ADD COLUMN pm_kisan_status TEXT NOT NULL
                DEFAULT 'Not recorded'
                CHECK(pm_kisan_status IN (
                    'Not recorded', 'Not applied', 'Applied', 'Pending', 'Approved', 'Rejected'
                ))
            """,
            "pmfby_status": """
                ALTER TABLE farmers ADD COLUMN pmfby_status TEXT NOT NULL
                DEFAULT 'Not recorded'
                CHECK(pmfby_status IN (
                    'Not recorded', 'Not applied', 'Applied', 'Pending', 'Approved', 'Rejected'
                ))
            """,
        }
        for column, migration in migrations.items():
            if column not in farmer_columns:
                connection.execute(migration)
        if "password_hash" not in farmer_columns:
            connection.execute("ALTER TABLE farmers ADD COLUMN password_hash TEXT")
        if "actual_quantity_kg" not in booking_columns:
            connection.execute(
                """
                ALTER TABLE bookings ADD COLUMN actual_quantity_kg INTEGER
                CHECK(actual_quantity_kg IS NULL OR actual_quantity_kg BETWEEN 1 AND 100000)
                """
            )
        if "completed_at" not in booking_columns:
            connection.execute("ALTER TABLE bookings ADD COLUMN completed_at TEXT")
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_farmers_kisan_farmer_id
            ON farmers(kisan_farmer_id)
            WHERE kisan_farmer_id IS NOT NULL
            """
        )
    finally:
        connection.close()
    try:
        os.chmod(database_path, 0o600)
    except OSError:
        app.logger.exception("Unable to restrict database file permissions.")
        raise
