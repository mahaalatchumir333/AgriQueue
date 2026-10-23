import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app import create_app
from werkzeug.security import generate_password_hash


class AgriQueueApiTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        database_path = str(Path(self.temporary_directory.name) / "test.sqlite3")
        self.app = create_app(
            {
                "TESTING": True,
                "DATABASE": database_path,
                "ADMIN_API_TOKEN": "test-admin-token",
                "ENVIRONMENT": "test",
            }
        )
        self.client = self.app.test_client()
        self.admin_headers = {"Authorization": "Bearer test-admin-token"}
        self.csrf_token = self.client.get("/api/auth/session").json["csrf_token"]

    @property
    def csrf_headers(self):
        return {"X-CSRF-Token": self.csrf_token}
        self.admin_headers = {"Authorization": "Bearer test-admin-token"}

    def tearDown(self):
        self.temporary_directory.cleanup()

    def register_farmer(self, **overrides):
        data = {
            "name": "Test Farmer",
            "mobile": "9876543210",
            "village": "Test Village",
            "crop": "Paddy",
            "password": "FarmerPassword123!",
        }
        data.update(overrides)
        response = self.client.post(
            "/api/farmers", json=data, headers=self.csrf_headers
        )
        if response.status_code == 201:
            self.csrf_token = response.json["csrf_token"]
        return response

    def create_booking(self, farmer_id, **overrides):
        data = {
            "farmer_id": farmer_id,
            "centre_id": 1,
            "slot": "8:00 AM - 10:00 AM",
            "quantity_kg": 500,
        }
        data.update(overrides)
        return self.client.post(
            "/api/bookings", json=data, headers=self.csrf_headers
        )

    def test_health_and_reference_data(self):
        health = self.client.get("/api/health")
        self.assertEqual(health.json, {"status": "ok"})
        self.assertEqual(health.headers["Cache-Control"], "no-store")
        self.assertEqual(health.headers["X-Frame-Options"], "DENY")
        self.assertEqual(health.headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("frame-ancestors 'none'", health.headers["Content-Security-Policy"])
        seed = self.client.get("/api/seed").json
        self.assertTrue(seed["centres"])
        self.assertEqual(seed["farmers"], [])
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_registration_validates_and_persists_farmer(self):
        response = self.register_farmer()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["farmer"]["mobile"], "9876543210")
        duplicate = self.register_farmer()
        self.assertEqual(duplicate.status_code, 409)
        invalid = self.register_farmer(mobile="123")
        self.assertEqual(invalid.status_code, 400)
        short_password = self.register_farmer(mobile="9876543211", password="short")
        self.assertEqual(short_password.status_code, 400)
        auth = self.client.get("/api/auth/session").json
        self.assertTrue(auth["authenticated"])
        self.assertEqual(auth["role"], "farmer")

    def test_farmer_login_csrf_and_record_isolation(self):
        self.client.get("/api/auth/session")
        no_csrf = self.client.post(
            "/api/farmers",
            json={
                "name": "Missing CSRF",
                "mobile": "9000000010",
                "village": "Village",
                "crop": "Paddy",
                "password": "FarmerPassword123!",
            },
        )
        self.assertEqual(no_csrf.status_code, 400)

        farmer = self.register_farmer().json["farmer"]
        booking = self.create_booking(farmer["id"]).json["booking"]
        own_bookings = self.client.get("/api/farmer/bookings")
        self.assertEqual(len(own_bookings.json["bookings"]), 1)
        self.assertEqual(own_bookings.json["bookings"][0]["token"], booking["token"])
        self.assertEqual(self.client.get("/api/official/analytics").status_code, 401)
        self.assertEqual(self.client.get("/api/admin/farmers").status_code, 401)

        self.client.post(
            "/api/auth/logout", json={}, headers=self.csrf_headers
        )
        csrf_token = self.client.get("/api/auth/session").json["csrf_token"]
        bad_login = self.client.post(
            "/api/auth/login",
            json={"identifier": farmer["mobile"], "password": "wrong-password"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(bad_login.status_code, 401)
        good_login = self.client.post(
            "/api/auth/login",
            json={"identifier": farmer["mobile"], "password": "FarmerPassword123!"},
            headers={"X-CSRF-Token": csrf_token},
        )
        self.assertEqual(good_login.status_code, 200)
        self.assertEqual(good_login.json["role"], "farmer")
        self.assertEqual(
            self.client.get("/api/farmer/bookings").json["bookings"][0]["token"],
            booking["token"],
        )

    def test_booking_persists_and_admin_api_requires_token(self):
        farmer = self.register_farmer().json["farmer"]
        response = self.create_booking(farmer["id"])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json["booking"]["status"], "Waiting")
        self.assertEqual(len(response.json["booking"]["token"]), 16)
        self.assertEqual(self.client.get("/api/admin/bookings").status_code, 401)
        admin_response = self.client.get(
            "/api/admin/bookings", headers=self.admin_headers
        )
        self.assertEqual(admin_response.status_code, 200)
        self.assertEqual(admin_response.json["bookings"][0]["farmer_mobile"], "9876543210")
        self.assertEqual(admin_response.json["pagination"], {"limit": 100, "offset": 0})
        invalid_page = self.client.get(
            "/api/admin/bookings?limit=101", headers=self.admin_headers
        )
        self.assertEqual(invalid_page.status_code, 400)

    def test_availability_counts_bookings_and_rejects_past_dates(self):
        today = date.today().isoformat()
        farmer = self.register_farmer().json["farmer"]
        booking = self.create_booking(
            farmer["id"],
            service_date=today,
            slot="10:00 AM - 12:00 PM",
        )
        self.assertEqual(booking.status_code, 201)
        availability = self.client.get(
            f"/api/availability?centre_id=1&service_date={today}"
        )
        self.assertEqual(availability.status_code, 200)
        self.assertEqual(availability.json["booked_today"], 1)
        chosen_slot = next(
            slot for slot in availability.json["slots"]
            if slot["slot"] == "10:00 AM - 12:00 PM"
        )
        self.assertEqual(chosen_slot["booked"], 1)
        invalid = self.client.get("/api/availability?centre_id=999&service_date=bad")
        self.assertEqual(invalid.status_code, 400)
        past_availability = self.client.get(
            "/api/availability?centre_id=1&service_date=2000-01-01"
        )
        self.assertEqual(past_availability.status_code, 400)
        past_booking = self.create_booking(
            farmer["id"],
            service_date="2000-01-01",
        )
        self.assertEqual(past_booking.status_code, 400)

    def test_invalid_booking_and_status_transition(self):
        farmer_id = self.register_farmer().json["farmer"]["id"]
        self.assertEqual(self.create_booking(farmer_id, quantity_kg=0).status_code, 400)
        self.assertEqual(
            self.create_booking(
                farmer_id,
                slot=["8:00 AM - 10:00 AM", "10:00 AM - 12:00 PM"],
            ).status_code,
            400,
        )
        booking_id = self.create_booking(farmer_id).json["booking"]["id"]
        invalid_transition = self.client.patch(
            f"/api/admin/bookings/{booking_id}",
            json={"status": "Completed"},
            headers=self.admin_headers,
        )
        self.assertEqual(invalid_transition.status_code, 409)
        advanced = self.client.patch(
            f"/api/admin/bookings/{booking_id}",
            json={"status": "In Progress"},
            headers=self.admin_headers,
        )
        self.assertEqual(advanced.status_code, 200)
        self.assertEqual(advanced.json["booking"]["status"], "In Progress")

    def test_booking_survives_app_restart_and_respects_daily_capacity(self):
        farmer = self.register_farmer().json["farmer"]
        booking = self.create_booking(farmer["id"]).json["booking"]
        restarted_app = create_app(
            {
                "TESTING": True,
                "DATABASE": self.app.config["DATABASE"],
                "ADMIN_API_TOKEN": "test-admin-token",
                "ENVIRONMENT": "test",
            }
        )
        restarted_client = restarted_app.test_client()
        persisted = restarted_client.get(
            "/api/admin/bookings", headers=self.admin_headers
        )
        self.assertEqual(persisted.json["bookings"][0]["token"], booking["token"])
        for _ in range(39):
            self.assertEqual(
                self.create_booking(farmer["id"]).status_code,
                201,
            )
        full = self.create_booking(farmer["id"])
        self.assertEqual(full.status_code, 409)

    def test_admin_can_update_kisan_id_and_scheme_tracking(self):
        farmer = self.register_farmer().json["farmer"]
        response = self.client.patch(
            f"/api/admin/farmers/{farmer['id']}",
            json={
                "kisan_farmer_id": "tn-farm-1234",
                "pm_kisan_status": "Applied",
                "pmfby_status": "Pending",
            },
            headers=self.admin_headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["farmer"]["kisan_farmer_id"], "TN-FARM-1234")
        self.assertEqual(response.json["farmer"]["pm_kisan_status"], "Applied")
        self.assertEqual(response.json["farmer"]["pmfby_status"], "Pending")

        listed = self.client.get("/api/admin/farmers", headers=self.admin_headers)
        self.assertEqual(listed.json["farmers"][0]["kisan_farmer_id"], "TN-FARM-1234")

        duplicate_farmer = self.register_farmer(mobile="9876543211").json["farmer"]
        duplicate = self.client.patch(
            f"/api/admin/farmers/{duplicate_farmer['id']}",
            json={"kisan_farmer_id": "TN-FARM-1234"},
            headers=self.admin_headers,
        )
        self.assertEqual(duplicate.status_code, 409)

        aadhaar = self.client.patch(
            f"/api/admin/farmers/{farmer['id']}",
            json={"kisan_farmer_id": "123456789012"},
            headers=self.admin_headers,
        )
        self.assertEqual(aadhaar.status_code, 400)
        invalid_scheme_status = self.client.patch(
            f"/api/admin/farmers/{farmer['id']}",
            json={"pm_kisan_status": "Automatically linked"},
            headers=self.admin_headers,
        )
        self.assertEqual(invalid_scheme_status.status_code, 400)

        cleared = self.client.patch(
            f"/api/admin/farmers/{farmer['id']}",
            json={"kisan_farmer_id": None},
            headers=self.admin_headers,
        )
        self.assertIsNone(cleared.json["farmer"]["kisan_farmer_id"])

    def test_farmer_metadata_requires_admin_authorization(self):
        farmer = self.register_farmer().json["farmer"]
        response = self.client.patch(
            f"/api/admin/farmers/{farmer['id']}",
            json={"kisan_farmer_id": "TN-FARM-1234"},
        )
        self.assertEqual(response.status_code, 401)

    def create_official(self):
        with self.app.app_context():
            from database import get_db

            get_db().execute(
                """
                INSERT INTO officials (username, password_hash, created_at)
                VALUES (?, ?, '2026-01-01T00:00:00+00:00')
                """,
                ("procurement-official", generate_password_hash("OfficialPassword123!")),
            )
            get_db().commit()

    def test_official_login_and_web_procurement_workflow(self):
        farmer = self.register_farmer().json["farmer"]
        booking = self.create_booking(farmer["id"]).json["booking"]
        self.create_official()
        login = self.client.post(
            "/api/auth/login",
            json={
                "identifier": "procurement-official",
                "password": "OfficialPassword123!",
            },
            headers=self.csrf_headers,
        )
        self.assertEqual(login.status_code, 200)
        self.csrf_token = login.json["csrf_token"]
        self.assertEqual(login.json["role"], "official")
        self.assertEqual(self.client.get("/api/official/analytics").status_code, 200)
        self.assertEqual(
            self.client.get("/api/official/bookings").json["bookings"][0]["token"],
            booking["token"],
        )

        advance = self.client.patch(
            f"/api/official/bookings/{booking['id']}",
            json={"status": "In Progress"},
            headers=self.csrf_headers,
        )
        self.assertEqual(advance.status_code, 200)
        incomplete = self.client.patch(
            f"/api/official/bookings/{booking['id']}",
            json={"status": "Completed"},
            headers=self.csrf_headers,
        )
        self.assertEqual(incomplete.status_code, 400)
        complete = self.client.patch(
            f"/api/official/bookings/{booking['id']}",
            json={"status": "Completed", "actual_quantity_kg": 475},
            headers=self.csrf_headers,
        )
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json["booking"]["actual_quantity_kg"], 475)
        paid = self.client.patch(
            f"/api/official/bookings/{booking['id']}",
            json={"payment": "Paid"},
            headers=self.csrf_headers,
        )
        self.assertEqual(paid.json["booking"]["payment"], "Paid")

    def test_migrates_existing_farmer_table(self):
        database_path = Path(self.temporary_directory.name) / "legacy.sqlite3"
        connection = sqlite3.connect(database_path)
        connection.execute(
            """
            CREATE TABLE farmers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                mobile TEXT NOT NULL UNIQUE,
                village TEXT NOT NULL,
                crop TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO farmers (name, mobile, village, crop, created_at)
            VALUES ('Existing Farmer', '9876543210', 'Old Village', 'Paddy', '2025-01-01')
            """
        )
        connection.commit()
        connection.close()

        migrated_app = create_app(
            {
                "TESTING": True,
                "DATABASE": str(database_path),
                "ADMIN_API_TOKEN": "test-admin-token",
                "ENVIRONMENT": "test",
            }
        )
        response = migrated_app.test_client().get(
            "/api/admin/farmers", headers=self.admin_headers
        )
        self.assertEqual(response.status_code, 200)
        farmer = response.json["farmers"][0]
        self.assertIsNone(farmer["kisan_farmer_id"])
        self.assertEqual(farmer["pm_kisan_status"], "Not recorded")
        self.assertEqual(farmer["pmfby_status"], "Not recorded")

    def test_production_requires_admin_token(self):
        with self.assertRaisesRegex(RuntimeError, "ADMIN_API_TOKEN"):
            create_app(
                {
                    "TESTING": True,
                    "DATABASE": str(Path(self.temporary_directory.name) / "prod.sqlite3"),
                    "ADMIN_API_TOKEN": "",
                    "ENVIRONMENT": "production",
                }
            )
        with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
            create_app(
                {
                    "TESTING": True,
                    "DATABASE": str(Path(self.temporary_directory.name) / "short-token.sqlite3"),
                    "ADMIN_API_TOKEN": "too-short",
                    "ENVIRONMENT": "production",
                }
            )


if __name__ == "__main__":
    unittest.main()
