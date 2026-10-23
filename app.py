"""AgriQueue Flask application and JSON API."""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
from datetime import date, datetime, timezone
from functools import wraps
from pathlib import Path
from typing import Any

import click
from flask import Flask, current_app, jsonify, request, session
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from database import close_db, get_db, init_db

BASE_DIR = Path(__file__).resolve().parent
SEED_FILE = BASE_DIR / "data" / "seed.json"
STATUSES = ("Waiting", "In Progress", "Completed")
SCHEME_STATUSES = ("Not recorded", "Not applied", "Applied", "Pending", "Approved", "Rejected")
MAX_QUANTITY_KG = 100_000


def _error(message: str, code: str, status: int):
    return jsonify(error={"code": code, "message": message}), status


def _json_object():
    if not request.is_json:
        return None, _error("Content-Type must be application/json.", "unsupported_media_type", 415)
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        return None, _error("Request body must be a JSON object.", "invalid_json", 400)
    return value, None


def _required_text(data: dict[str, Any], key: str, label: str, maximum: int):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        return None, f"{label} is required and must be at most {maximum} characters."
    return value.strip(), None


def _admin_required():
    if session.get("role") == "official":
        return None
    expected = current_app.config["ADMIN_API_TOKEN"]
    if not expected:
        return _error("Administrative API is not configured.", "admin_api_unavailable", 503)
    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not supplied
        or not hmac.compare_digest(supplied, expected)
    ):
        return _error("A valid bearer token is required.", "unauthorized", 401)
    return None


def _pagination():
    try:
        limit = int(request.args.get("limit", "100"))
        offset = int(request.args.get("offset", "0"))
    except ValueError:
        return None, _error("limit and offset must be integers.", "invalid_request", 400)
    if not 1 <= limit <= 100 or offset < 0:
        return None, _error(
            "limit must be between 1 and 100, and offset cannot be negative.",
            "invalid_request",
            400,
        )
    return (limit, offset), None


def _farmer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        farmer_id = session.get("farmer_id")
        if session.get("role") != "farmer" or not farmer_id:
            return _error("Farmer sign-in is required.", "unauthorized", 401)
        return view(*args, **kwargs)

    return wrapped


def _official_session_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("role") != "official":
            return _error("Official sign-in is required.", "unauthorized", 401)
        return view(*args, **kwargs)

    return wrapped


def create_app(test_config: dict[str, Any] | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        DATABASE=os.environ.get("DATABASE_PATH", str(Path(app.instance_path) / "agriqueue.sqlite3")),
        ADMIN_API_TOKEN=os.environ.get("ADMIN_API_TOKEN", ""),
        SECRET_KEY=os.environ.get("APP_SECRET_KEY") or secrets.token_urlsafe(48),
        ENVIRONMENT=os.environ.get("APP_ENV", "development").lower(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    if app.config["ENVIRONMENT"] == "production":
        admin_token = app.config["ADMIN_API_TOKEN"]
        if not admin_token or len(admin_token) < 32:
            raise RuntimeError(
                "ADMIN_API_TOKEN must contain at least 32 characters in production."
            )
        if len(app.config["SECRET_KEY"]) < 32:
            raise RuntimeError("APP_SECRET_KEY must contain at least 32 characters in production.")
        app.config["SESSION_COOKIE_SECURE"] = True

    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    app.logger.setLevel(logging.INFO)
    init_db(app)
    app.teardown_appcontext(close_db)

    @app.before_request
    def protect_session_mutations():
        if (
            request.path.startswith("/api/")
            and request.method in {"POST", "PUT", "PATCH", "DELETE"}
            and (
                not request.path.startswith("/api/admin/")
                or session.get("role") == "official"
            )
        ):
            expected = session.get("csrf_token")
            supplied = request.headers.get("X-CSRF-Token", "")
            if not expected or not supplied or not hmac.compare_digest(expected, supplied):
                return _error("Refresh the page and try again.", "csrf_failed", 400)
        return None

    @app.after_request
    def apply_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; base-uri 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    def home():
        from flask import render_template

        return render_template("index.html")

    @app.get("/api/seed")
    def seed():
        data = dict(app.config["SEED"])
        data["farmers"] = []
        return jsonify(data)

    @app.get("/api/health")
    def health():
        try:
            get_db().execute("SELECT 1").fetchone()
        except sqlite3.Error:
            app.logger.exception("Database health check failed.")
            return jsonify(status="error"), 503
        return jsonify(status="ok")

    @app.get("/api/availability")
    def slot_availability():
        try:
            centre_id = int(request.args.get("centre_id", ""))
            service_date = date.fromisoformat(request.args.get("service_date", "")).isoformat()
        except ValueError:
            return _error(
                "Provide a valid centre_id and service_date in YYYY-MM-DD format.",
                "invalid_request",
                400,
            )
        if service_date < date.today().isoformat():
            return _error("service_date cannot be in the past.", "invalid_request", 400)
        centre = next(
            (item for item in app.config["SEED"]["centres"] if item["id"] == centre_id), None
        )
        if centre is None:
            return _error("Select a valid procurement centre.", "invalid_request", 400)
        db = get_db()
        total_booked = db.execute(
            """
            SELECT COUNT(*) FROM bookings
            WHERE centre_id = ? AND service_date = ? AND status != 'Cancelled'
            """,
            (centre_id, service_date),
        ).fetchone()[0]
        slot_rows = db.execute(
            """
            SELECT slot, COUNT(*) AS booked
            FROM bookings
            WHERE centre_id = ? AND service_date = ? AND status != 'Cancelled'
            GROUP BY slot
            """,
            (centre_id, service_date),
        ).fetchall()
        counts = {row["slot"]: row["booked"] for row in slot_rows}
        remaining = max(0, centre["capacity"] - total_booked)
        return jsonify(
            centre_id=centre_id,
            service_date=service_date,
            daily_capacity=centre["capacity"],
            booked_today=total_booked,
            remaining_daily_capacity=remaining,
            slots=[
                {
                    "slot": slot,
                    "booked": counts.get(slot, 0),
                    "available": remaining > 0,
                }
                for slot in app.config["SEED"]["slots"]
            ],
        )

    @app.get("/api/auth/session")
    def auth_session():
        csrf_token = session.get("csrf_token")
        if not csrf_token:
            csrf_token = secrets.token_urlsafe(32)
            session["csrf_token"] = csrf_token
        role = session.get("role")
        user = None
        if role == "farmer":
            farmer = get_db().execute(
                """
                SELECT id, name, mobile, village, crop, kisan_farmer_id,
                       pm_kisan_status, pmfby_status
                FROM farmers WHERE id = ?
                """,
                (session.get("farmer_id"),),
            ).fetchone()
            if farmer:
                user = dict(farmer)
            else:
                session.clear()
                role = None
        elif role == "official":
            user = {"username": session.get("username")}
        return jsonify(authenticated=user is not None, role=role, user=user, csrf_token=csrf_token)

    @app.post("/api/auth/login")
    def login():
        data, error = _json_object()
        if error:
            return error
        identifier, message = _required_text(data, "identifier", "Mobile number or username", 120)
        password = data.get("password")
        if message or not isinstance(password, str) or not password or len(password) > 256:
            return _error("Enter your mobile/username and password.", "invalid_credentials", 400)
        db = get_db()
        official = db.execute(
            "SELECT id, username, password_hash FROM officials WHERE username = ? COLLATE NOCASE",
            (identifier,),
        ).fetchone()
        farmer = None
        if official is None:
            farmer = db.execute(
                """
                SELECT id, name, mobile, village, crop, password_hash
                FROM farmers WHERE mobile = ?
                """,
                (identifier,),
            ).fetchone()
        account = official or farmer
        if account is None or not account["password_hash"] or not check_password_hash(
            account["password_hash"], password
        ):
            return _error("The sign-in details are incorrect.", "invalid_credentials", 401)
        session.clear()
        session.permanent = True
        session["csrf_token"] = secrets.token_urlsafe(32)
        if official:
            session["role"] = "official"
            session["official_id"] = official["id"]
            session["username"] = official["username"]
            user = {"username": official["username"]}
        else:
            session["role"] = "farmer"
            session["farmer_id"] = farmer["id"]
            user = {
                key: farmer[key]
                for key in ("id", "name", "mobile", "village", "crop")
            }
        return jsonify(authenticated=True, role=session["role"], user=user, csrf_token=session["csrf_token"])

    @app.post("/api/auth/logout")
    def logout():
        session.clear()
        return jsonify(authenticated=False)

    @app.cli.command("create-official")
    @click.option("--username", prompt=True, help="Official account username")
    def create_official(username: str):
        """Create or update an official portal account."""
        username = username.strip()
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{3,80}", username):
            raise click.ClickException("Username must be 3-80 letters, digits, or . _ @ - characters.")
        password = click.prompt("Password", hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        db = get_db()
        db.execute(
            """
            INSERT INTO officials (username, password_hash, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET password_hash = excluded.password_hash
            """,
            (username, generate_password_hash(password), datetime.now(timezone.utc).isoformat()),
        )
        db.commit()
        click.echo(f"Official account '{username}' is ready.")

    @app.cli.command("reset-farmer-password")
    @click.option("--mobile", prompt=True, help="Registered farmer's 10-digit mobile number")
    def reset_farmer_password(mobile: str):
        """Set a password for an existing farmer account after identity checks."""
        if not re.fullmatch(r"\d{10}", mobile):
            raise click.ClickException("Mobile number must contain exactly 10 digits.")
        password = click.prompt("New password", hide_input=True, confirmation_prompt=True)
        if len(password) < 12:
            raise click.ClickException("Password must be at least 12 characters.")
        db = get_db()
        cursor = db.execute(
            "UPDATE farmers SET password_hash = ? WHERE mobile = ?",
            (generate_password_hash(password), mobile),
        )
        if cursor.rowcount == 0:
            raise click.ClickException("No farmer account was found for that mobile number.")
        db.commit()
        click.echo("Farmer password updated.")

    @app.post("/api/farmers")
    def register_farmer():
        data, error = _json_object()
        if error:
            return error
        fields = {
            "name": ("Farmer name", 120),
            "mobile": ("Mobile number", 10),
            "village": ("Village", 120),
            "crop": ("Crop", 80),
        }
        values = {}
        for key, (label, maximum) in fields.items():
            value, message = _required_text(data, key, label, maximum)
            if message:
                return _error(message, "invalid_request", 400)
            values[key] = value
        password = data.get("password")
        if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
            return _error(
                "Password must contain between 12 and 256 characters.",
                "invalid_password",
                400,
            )
        if not re.fullmatch(r"\d{10}", values["mobile"]):
            return _error("Mobile number must contain exactly 10 digits.", "invalid_request", 400)
        if values["crop"] not in app.config["SEED"]["crops"]:
            return _error("Select a crop from the supported crop list.", "invalid_request", 400)
        try:
            cursor = get_db().execute(
                """
                INSERT INTO farmers (name, mobile, village, crop, password_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    values["name"],
                    values["mobile"],
                    values["village"],
                    values["crop"],
                    generate_password_hash(password),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            get_db().commit()
        except sqlite3.IntegrityError:
            return _error("This mobile number is already registered.", "duplicate_mobile", 409)
        farmer = get_db().execute(
            """
            SELECT id, name, mobile, village, crop, kisan_farmer_id,
                   pm_kisan_status, pmfby_status
            FROM farmers WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
        session.clear()
        session.permanent = True
        session["role"] = "farmer"
        session["farmer_id"] = farmer["id"]
        session["csrf_token"] = secrets.token_urlsafe(32)
        return jsonify(
            farmer=dict(farmer),
            user={
                key: farmer[key]
                for key in ("id", "name", "mobile", "village", "crop")
            },
            csrf_token=session["csrf_token"],
            authenticated=True,
            role="farmer",
        ), 201

    @app.post("/api/bookings")
    @_farmer_required
    def create_booking():
        data, error = _json_object()
        if error:
            return error
        farmer_id = session["farmer_id"]
        centre_id = data.get("centre_id")
        qty = data.get("quantity_kg")
        slot = data.get("slot")
        service_date = data.get("service_date", date.today().isoformat())
        if type(centre_id) is not int or centre_id < 1:
            return _error("centre_id must be a positive integer.", "invalid_request", 400)
        if type(qty) is not int or not 1 <= qty <= MAX_QUANTITY_KG:
            return _error(
                f"quantity_kg must be an integer between 1 and {MAX_QUANTITY_KG}.",
                "invalid_request",
                400,
            )
        if not isinstance(service_date, str):
            return _error("service_date must use YYYY-MM-DD format.", "invalid_request", 400)
        try:
            service_date = date.fromisoformat(service_date).isoformat()
        except ValueError:
            return _error("service_date must use YYYY-MM-DD format.", "invalid_request", 400)
        if service_date < date.today().isoformat():
            return _error("service_date cannot be in the past.", "invalid_request", 400)
        if not isinstance(slot, str) or slot not in app.config["SEED"]["slots"]:
            return _error("Select a valid procurement slot.", "invalid_request", 400)
        centre = next(
            (item for item in app.config["SEED"]["centres"] if item["id"] == centre_id), None
        )
        if centre is None:
            return _error("Select a valid procurement centre.", "invalid_request", 400)

        db = get_db()
        try:
            db.execute("BEGIN IMMEDIATE")
            if not db.execute("SELECT 1 FROM farmers WHERE id = ?", (farmer_id,)).fetchone():
                db.rollback()
                return _error("Farmer was not found.", "farmer_not_found", 404)
            booked = db.execute(
                """
                SELECT COUNT(*) FROM bookings
                WHERE centre_id = ? AND service_date = ? AND status != 'Cancelled'
                """,
                (centre_id, service_date),
            ).fetchone()[0]
            if booked >= centre["capacity"]:
                db.rollback()
                return _error("Centre is fully booked for this date.", "centre_full", 409)
            token = secrets.token_urlsafe(12)
            created_at = datetime.now(timezone.utc).isoformat()
            cursor = db.execute(
                """
                INSERT INTO bookings
                    (token, farmer_id, centre_id, slot, quantity_kg, service_date, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (token, farmer_id, centre_id, slot, qty, service_date, created_at),
            )
            db.commit()
        except sqlite3.Error:
            db.rollback()
            app.logger.exception("Failed to persist a booking.")
            return _error("Booking could not be created.", "database_error", 500)
        booking = db.execute(
            "SELECT * FROM bookings WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
        return jsonify(booking=dict(booking)), 201

    @app.get("/api/farmer/bookings")
    @_farmer_required
    def farmer_bookings():
        rows = get_db().execute(
            """
            SELECT id, token, centre_id, slot, quantity_kg, actual_quantity_kg,
                   service_date, status, payment, created_at, completed_at
            FROM bookings
            WHERE farmer_id = ?
            ORDER BY service_date DESC, id DESC
            LIMIT 100
            """,
            (session["farmer_id"],),
        ).fetchall()
        bookings = []
        for row in rows:
            booking = dict(row)
            if booking["status"] == "Waiting":
                ahead = get_db().execute(
                    """
                    SELECT COUNT(*) FROM bookings
                    WHERE centre_id = ? AND service_date = ? AND slot = ?
                      AND status = 'Waiting' AND id < ?
                    """,
                    (
                        booking["centre_id"],
                        booking["service_date"],
                        booking["slot"],
                        booking["id"],
                    ),
                ).fetchone()[0]
                booking["queue_position"] = ahead + 1
                booking["estimated_wait_minutes"] = ahead * app.config["SEED"]["config"]["minutesPerToken"]
            else:
                booking["queue_position"] = None
                booking["estimated_wait_minutes"] = None
            bookings.append(booking)
        return jsonify(
            bookings=bookings,
            centres=app.config["SEED"]["centres"],
            minutes_per_token=app.config["SEED"]["config"]["minutesPerToken"],
        )

    @app.get("/api/official/bookings")
    @_official_session_required
    def official_bookings():
        pagination, error = _pagination()
        if error:
            return error
        limit, offset = pagination
        rows = get_db().execute(
            """
            SELECT b.*, f.name AS farmer_name, f.mobile AS farmer_mobile,
                   f.village AS farmer_village, f.crop AS farmer_crop
            FROM bookings b
            JOIN farmers f ON f.id = b.farmer_id
            ORDER BY b.service_date, b.centre_id, b.slot, b.id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        centre_names = {centre["id"]: centre["name"] for centre in app.config["SEED"]["centres"]}
        bookings = []
        for row in rows:
            booking = dict(row)
            booking["centre_name"] = centre_names.get(booking["centre_id"], "Unknown centre")
            bookings.append(booking)
        return jsonify(bookings=bookings, pagination={"limit": limit, "offset": offset})

    @app.get("/api/official/analytics")
    @_official_session_required
    def official_analytics():
        db = get_db()
        total_farmers = db.execute("SELECT COUNT(*) FROM farmers").fetchone()[0]
        total_bookings = db.execute(
            "SELECT COUNT(*) FROM bookings WHERE status != 'Cancelled'"
        ).fetchone()[0]
        completed = db.execute(
            "SELECT COUNT(*) FROM bookings WHERE status = 'Completed'"
        ).fetchone()[0]
        pending_payments = db.execute(
            "SELECT COUNT(*) FROM bookings WHERE status = 'Completed' AND payment = 'Pending'"
        ).fetchone()[0]
        average_wait = db.execute(
            """
            SELECT AVG((julianday(completed_at) - julianday(created_at)) * 1440)
            FROM bookings WHERE completed_at IS NOT NULL
            """
        ).fetchone()[0]
        today = date.today().isoformat()
        centre_rows = db.execute(
            """
            SELECT centre_id,
                   COUNT(CASE WHEN status != 'Cancelled' THEN 1 END) AS booked,
                   COUNT(CASE WHEN status IN ('Waiting', 'In Progress') THEN 1 END) AS queued
            FROM bookings WHERE service_date = ?
            GROUP BY centre_id
            """,
            (today,),
        ).fetchall()
        slot_rows = db.execute(
            """
            SELECT slot, COUNT(*) AS bookings
            FROM bookings
            WHERE service_date = ? AND status != 'Cancelled'
            GROUP BY slot ORDER BY bookings DESC, slot
            """,
            (today,),
        ).fetchall()
        total_today = sum(row["bookings"] for row in slot_rows)
        centre_load = {row["centre_id"]: dict(row) for row in centre_rows}
        centres = []
        for centre in app.config["SEED"]["centres"]:
            load = centre_load.get(centre["id"], {})
            centres.append(
                {
                    **centre,
                    "booked_today": load.get("booked", 0),
                    "queue_now": load.get("queued", 0),
                }
            )
        return jsonify(
            summary={
                "farmers": total_farmers,
                "bookings": total_bookings,
                "completed": completed,
                "pending_payments": pending_payments,
                "average_wait_minutes": round(average_wait, 1) if average_wait is not None else None,
            },
            centres=centres,
            slots=[
                {
                    "slot": row["slot"],
                    "bookings": row["bookings"],
                    "share_percent": round(row["bookings"] / total_today * 100)
                    if total_today
                    else 0,
                }
                for row in slot_rows
            ],
        )

    @app.patch("/api/official/bookings/<int:booking_id>")
    @_official_session_required
    def update_official_booking(booking_id: int):
        data, error = _json_object()
        if error:
            return error
        allowed = {"status", "payment", "actual_quantity_kg"}
        if not data or not set(data).issubset(allowed):
            return _error("Provide a valid procurement or payment update.", "invalid_request", 400)
        db = get_db()
        booking = db.execute(
            "SELECT status, quantity_kg, actual_quantity_kg, payment FROM bookings WHERE id = ?",
            (booking_id,),
        ).fetchone()
        if booking is None:
            return _error("Booking was not found.", "booking_not_found", 404)
        updates = {}
        if "status" in data:
            status = data["status"]
            if status not in STATUSES:
                return _error("Select a valid booking status.", "invalid_request", 400)
            transitions = {
                "Waiting": "In Progress",
                "In Progress": "Completed",
                "Completed": "Completed",
            }
            if transitions.get(booking["status"]) != status:
                return _error("Booking status cannot move backwards or skip a step.", "invalid_transition", 409)
            updates["status"] = status
            if status == "Completed":
                actual_quantity = data.get("actual_quantity_kg", booking["actual_quantity_kg"])
                if actual_quantity is None:
                    return _error(
                        "Record the actual received quantity before completing procurement.",
                        "actual_quantity_required",
                        400,
                    )
                updates["completed_at"] = datetime.now(timezone.utc).isoformat()
        if "actual_quantity_kg" in data:
            quantity = data["actual_quantity_kg"]
            if type(quantity) is not int or not 1 <= quantity <= MAX_QUANTITY_KG:
                return _error(
                    f"actual_quantity_kg must be an integer between 1 and {MAX_QUANTITY_KG}.",
                    "invalid_request",
                    400,
                )
            if booking["status"] == "Cancelled":
                return _error("Cancelled bookings cannot record procurement.", "invalid_transition", 409)
            updates["actual_quantity_kg"] = quantity
        if "payment" in data:
            if data["payment"] != "Paid" or booking["status"] != "Completed":
                return _error(
                    "Payment can only be marked paid after procurement is completed.",
                    "invalid_payment_transition",
                    409,
                )
            updates["payment"] = "Paid"
        if not updates:
            return _error("No booking fields were changed.", "invalid_request", 400)
        assignments = ", ".join(f"{field} = ?" for field in updates)
        if "status" in updates:
            cursor = db.execute(
                f"UPDATE bookings SET {assignments} WHERE id = ? AND status = ?",
                (*updates.values(), booking_id, booking["status"]),
            )
        else:
            cursor = db.execute(
                f"UPDATE bookings SET {assignments} WHERE id = ?",
                (*updates.values(), booking_id),
            )
        if cursor.rowcount == 0:
            db.rollback()
            return _error(
                "Booking was changed by another official. Refresh and try again.",
                "concurrent_booking_update",
                409,
            )
        db.commit()
        updated = db.execute("SELECT * FROM bookings WHERE id = ?", (booking_id,)).fetchone()
        return jsonify(booking=dict(updated))

    @app.delete("/api/official/bookings/<int:booking_id>")
    @_official_session_required
    def cancel_official_booking(booking_id: int):
        db = get_db()
        cursor = db.execute(
            """
            UPDATE bookings SET status = 'Cancelled'
            WHERE id = ? AND status IN ('Waiting', 'In Progress')
            """,
            (booking_id,),
        )
        if cursor.rowcount == 0:
            return _error("Active booking was not found.", "booking_not_found", 404)
        db.commit()
        return jsonify(status="cancelled", booking_id=booking_id)

    @app.get("/api/admin/farmers")
    def list_farmers():
        auth_error = _admin_required()
        if auth_error:
            return auth_error
        pagination, error = _pagination()
        if error:
            return error
        limit, offset = pagination
        farmers = get_db().execute(
            """
            SELECT id, name, mobile, village, crop, kisan_farmer_id,
                   pm_kisan_status, pmfby_status, created_at
            FROM farmers
            ORDER BY id LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return jsonify(
            farmers=[dict(farmer) for farmer in farmers],
            pagination={"limit": limit, "offset": offset},
        )

    @app.patch("/api/admin/farmers/<int:farmer_id>")
    def update_farmer(farmer_id: int):
        auth_error = _admin_required()
        if auth_error:
            return auth_error
        data, error = _json_object()
        if error:
            return error
        allowed_fields = {"kisan_farmer_id", "pm_kisan_status", "pmfby_status"}
        if not data or not set(data).issubset(allowed_fields):
            return _error(
                "Provide one or more supported Kisan ID or scheme tracking fields.",
                "invalid_request",
                400,
            )

        updates = {}
        if "kisan_farmer_id" in data:
            kisan_id = data["kisan_farmer_id"]
            if kisan_id is None:
                updates["kisan_farmer_id"] = None
            elif isinstance(kisan_id, str):
                normalized_id = re.sub(r"\s+", "", kisan_id).upper()
                compact_digits = re.sub(r"[-/]", "", normalized_id)
                if (
                    not re.fullmatch(r"[A-Z0-9][A-Z0-9/-]{3,63}", normalized_id)
                    or re.fullmatch(r"\d{12}", compact_digits)
                ):
                    return _error(
                        "Enter a valid Kisan Farmer ID only; Aadhaar numbers are not accepted.",
                        "invalid_kisan_farmer_id",
                        400,
                    )
                updates["kisan_farmer_id"] = normalized_id
            else:
                return _error(
                    "kisan_farmer_id must be a string or null.",
                    "invalid_kisan_farmer_id",
                    400,
                )

        for field in ("pm_kisan_status", "pmfby_status"):
            if field in data:
                if data[field] not in SCHEME_STATUSES:
                    return _error(
                        f"{field} must be one of: {', '.join(SCHEME_STATUSES)}.",
                        "invalid_scheme_status",
                        400,
                    )
                updates[field] = data[field]

        db = get_db()
        farmer = db.execute("SELECT id FROM farmers WHERE id = ?", (farmer_id,)).fetchone()
        if farmer is None:
            return _error("Farmer was not found.", "farmer_not_found", 404)
        assignments = ", ".join(f"{field} = ?" for field in updates)
        try:
            db.execute(
                f"UPDATE farmers SET {assignments} WHERE id = ?",
                (*updates.values(), farmer_id),
            )
            db.commit()
        except sqlite3.IntegrityError:
            db.rollback()
            return _error(
                "This Kisan Farmer ID is already assigned to another farmer.",
                "duplicate_kisan_farmer_id",
                409,
            )
        updated_farmer = db.execute(
            """
            SELECT id, name, mobile, village, crop, kisan_farmer_id,
                   pm_kisan_status, pmfby_status, created_at
            FROM farmers WHERE id = ?
            """,
            (farmer_id,),
        ).fetchone()
        return jsonify(farmer=dict(updated_farmer))

    @app.get("/api/admin/bookings")
    def list_bookings():
        auth_error = _admin_required()
        if auth_error:
            return auth_error
        pagination, error = _pagination()
        if error:
            return error
        limit, offset = pagination
        rows = get_db().execute(
            """
            SELECT b.*, f.name AS farmer_name, f.mobile AS farmer_mobile,
                   f.village AS farmer_village
            FROM bookings b
            JOIN farmers f ON f.id = b.farmer_id
            ORDER BY b.id
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        centre_names = {centre["id"]: centre["name"] for centre in app.config["SEED"]["centres"]}
        bookings = []
        for row in rows:
            booking = dict(row)
            booking["centre_name"] = centre_names.get(booking["centre_id"], "Unknown centre")
            bookings.append(booking)
        return jsonify(
            bookings=bookings,
            pagination={"limit": limit, "offset": offset},
        )

    @app.patch("/api/admin/bookings/<int:booking_id>")
    def update_booking(booking_id: int):
        auth_error = _admin_required()
        if auth_error:
            return auth_error
        data, error = _json_object()
        if error:
            return error
        if set(data) == {"status"}:
            status = data["status"]
            if status not in STATUSES:
                return _error("Status must be Waiting, In Progress, or Completed.", "invalid_request", 400)
            current = get_db().execute(
                "SELECT status FROM bookings WHERE id = ?", (booking_id,)
            ).fetchone()
            if current is None:
                return _error("Booking was not found.", "booking_not_found", 404)
            transitions = {
                "Waiting": "In Progress",
                "In Progress": "Completed",
                "Completed": "Completed",
            }
            if transitions.get(current["status"]) != status:
                return _error("Booking status cannot move backwards or skip a step.", "invalid_transition", 409)
            completed_at = (
                datetime.now(timezone.utc).isoformat() if status == "Completed" else None
            )
            get_db().execute(
                "UPDATE bookings SET status = ?, completed_at = COALESCE(?, completed_at) WHERE id = ?",
                (status, completed_at, booking_id),
            )
        elif set(data) == {"payment"} and data["payment"] == "Paid":
            cursor = get_db().execute(
                "UPDATE bookings SET payment = 'Paid' WHERE id = ? AND status != 'Cancelled'",
                (booking_id,),
            )
            if cursor.rowcount == 0:
                return _error("Active booking was not found.", "booking_not_found", 404)
        else:
            return _error("Provide a valid status or payment update.", "invalid_request", 400)
        get_db().commit()
        booking = get_db().execute(
            "SELECT * FROM bookings WHERE id = ?", (booking_id,)
        ).fetchone()
        return jsonify(booking=dict(booking))

    @app.delete("/api/admin/bookings/<int:booking_id>")
    def cancel_booking(booking_id: int):
        auth_error = _admin_required()
        if auth_error:
            return auth_error
        cursor = get_db().execute(
            "UPDATE bookings SET status = 'Cancelled' WHERE id = ? AND status != 'Cancelled'",
            (booking_id,),
        )
        if cursor.rowcount == 0:
            return _error("Active booking was not found.", "booking_not_found", 404)
        get_db().commit()
        return jsonify(status="cancelled", booking_id=booking_id)

    @app.errorhandler(HTTPException)
    def handle_http_error(error: HTTPException):
        return _error(error.description, error.name.lower().replace(" ", "_"), error.code or 500)

    @app.errorhandler(Exception)
    def handle_unexpected_error(error: Exception):
        app.logger.exception("Unhandled application error.", exc_info=error)
        return _error("An unexpected server error occurred.", "internal_server_error", 500)

    try:
        with SEED_FILE.open(encoding="utf-8") as seed_file:
            app.config["SEED"] = json.load(seed_file)
        for key in ("centres", "slots", "crops"):
            if not isinstance(app.config["SEED"].get(key), list):
                raise ValueError(f"Seed data must define {key} as a list.")
    except (OSError, json.JSONDecodeError, ValueError) as error:
        raise RuntimeError("Invalid procurement reference data in data/seed.json.") from error

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
