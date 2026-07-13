import http.client
import json
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import app


class AuditLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_upload_dir = app.UPLOAD_DIR
        self.original_db_path = app.DB_PATH
        base = Path(self.tmp.name)
        app.DATA_DIR = base / "data"
        app.UPLOAD_DIR = base / "uploads"
        app.DB_PATH = app.DATA_DIR / "test.sqlite3"
        app.init_db()
        with app.db() as conn:
            admin_employee_id = conn.execute(
                """
                INSERT INTO employees (
                    name, email, join_date, department, annual_entitlement,
                    probation_end_date, status
                ) VALUES ('Admin User', 'admin@example.test', '2026-01-01', 'HR', 14, '2026-04-01', 'active')
                """
            ).lastrowid
            conn.execute(
                "INSERT INTO users (employee_id, email, password_hash, role) VALUES (?, 'admin@example.test', ?, 'admin')",
                (admin_employee_id, app.hash_password("Password123")),
            )
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.App)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.1)
        self.admin_cookie = self.login("admin@example.test")

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.UPLOAD_DIR = self.original_upload_dir
        app.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def request(self, method, path, data=None, cookie=None):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {}
        body = None
        if data is not None:
            body = urlencode(data)
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if cookie:
            headers["Cookie"] = cookie
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse()
        response_body = response.read().decode("utf-8", errors="replace")
        response_headers = dict(response.getheaders())
        status = response.status
        client.close()
        return status, response_headers, response_body

    def login(self, email):
        status, headers, _ = self.request("POST", "/login", {"email": email, "password": "Password123"})
        self.assertEqual(status, 303)
        return headers["Set-Cookie"]

    def audit_row(self, action):
        with app.db() as conn:
            return conn.execute(
                "SELECT * FROM audit_logs WHERE action = ? ORDER BY id DESC LIMIT 1",
                (action,),
            ).fetchone()

    def employee_payload(self, **overrides):
        payload = {
            "name": "Employee User",
            "email": "employee@example.test",
            "department": "Ops",
            "join_date": "2026-04-06",
            "annual_entitlement": "14",
            "probation_end_date": "2026-07-06",
            "work_pattern": "five_day",
            "custom_work_days": "",
            "status": "active",
            "role": "employee",
            "password": "Password123",
        }
        payload.update(overrides)
        return payload

    def test_employee_create_and_edit_audit_before_after_values(self):
        status, _, _ = self.request("POST", "/admin/employees/new", self.employee_payload(), self.admin_cookie)
        self.assertEqual(status, 303)
        created = self.audit_row("employee_created")
        self.assertIsNotNone(created)
        self.assertEqual(json.loads(created["after_value"])["join_date"], "2026-04-06")

        with app.db() as conn:
            employee_id = conn.execute("SELECT id FROM employees WHERE email = 'employee@example.test'").fetchone()["id"]

        edit_payload = self.employee_payload(
            join_date="2026-05-01",
            annual_entitlement="16",
            password="",
        )
        status, _, _ = self.request("POST", f"/admin/employees/{employee_id}/edit", edit_payload, self.admin_cookie)
        self.assertEqual(status, 303)

        edited = self.audit_row("employee_edited")
        self.assertIn("join_date", json.loads(edited["before_value"]))
        self.assertEqual(json.loads(edited["after_value"])["annual_entitlement"], 16.0)

        join_changed = self.audit_row("join_date_changed")
        self.assertEqual(json.loads(join_changed["before_value"])["join_date"], "2026-04-06")
        self.assertEqual(json.loads(join_changed["after_value"])["join_date"], "2026-05-01")

        entitlement_changed = self.audit_row("entitlement_changed")
        self.assertEqual(json.loads(entitlement_changed["after_value"])["annual_entitlement"], 16.0)

    def test_settings_and_off_in_lieu_audit_before_after_values(self):
        status, _, _ = self.request(
            "POST",
            "/admin/settings",
            {
                "enforce_mom_three_month_rule": "no",
                "allow_company_override": "yes",
                "saturday_ph_compensation_method": "salary_in_lieu",
                "off_in_lieu_expiry_months": "18",
            },
            self.admin_cookie,
        )
        self.assertEqual(status, 303)
        settings = self.audit_row("admin_settings_changed")
        self.assertEqual(json.loads(settings["before_value"])["enforce_mom_three_month_rule"], "yes")
        self.assertEqual(json.loads(settings["after_value"])["off_in_lieu_expiry_months"], "18")

        status, _, _ = self.request("POST", "/admin/employees/new", self.employee_payload(email="oil@example.test"), self.admin_cookie)
        self.assertEqual(status, 303)
        with app.db() as conn:
            employee_id = conn.execute("SELECT id FROM employees WHERE email = 'oil@example.test'").fetchone()["id"]

        status, _, _ = self.request(
            "POST",
            "/admin/off-in-lieu",
            {
                "employee_id": str(employee_id),
                "public_holiday_name": "Saturday Holiday",
                "public_holiday_date": "2026-03-21",
                "credit_date": "2026-03-21",
                "credit_amount_days": "1",
                "expiry_date": "2027-03-21",
                "notes": "Manual credit",
            },
            self.admin_cookie,
        )
        self.assertEqual(status, 303)
        added = self.audit_row("off_in_lieu_credit_added")
        self.assertEqual(json.loads(added["after_value"])["remaining_amount_days"], 1.0)
        self.assertEqual(added["notes"], "Manual credit")


if __name__ == "__main__":
    unittest.main()
