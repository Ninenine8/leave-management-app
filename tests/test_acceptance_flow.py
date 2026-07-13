import csv
import http.client
import io
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import app


class SmallCompanyAcceptanceFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.original_data_dir = app.DATA_DIR
        self.original_upload_dir = app.UPLOAD_DIR
        self.original_db_path = app.DB_PATH
        base = Path(self.tmp.name)
        app.DATA_DIR = base / "data"
        app.UPLOAD_DIR = base / "uploads"
        app.DB_PATH = app.DATA_DIR / "test.sqlite3"
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.App)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.1)

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
        raw = response.read()
        headers = dict(response.getheaders())
        status = response.status
        client.close()
        return status, headers, raw

    def post(self, path, data, cookie=None):
        return self.request("POST", path, data, cookie)

    def get(self, path, cookie=None):
        return self.request("GET", path, None, cookie)

    def login(self, email, password="Password123"):
        status, headers, _ = self.post("/login", {"email": email, "password": password})
        self.assertEqual(status, 303)
        return headers["Set-Cookie"]

    def csv_rows(self, data):
        return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))

    def test_real_internal_use_flow_for_singapore_company(self):
        status, _, body = self.get("/")
        self.assertEqual(status, 303)

        status, headers, _ = self.post(
            "/setup",
            {
                "name": "First Admin",
                "email": "admin@example.test",
                "department": "HR",
                "join_date": "2026-01-01",
                "password": "Password123",
                "confirm_password": "Password123",
            },
        )
        self.assertEqual(status, 303)

        admin_cookie = self.login("admin@example.test")
        manager_payload = {
            "name": "Boss Manager",
            "email": "boss@example.test",
            "department": "Operations",
            "job_title": "Operations Manager",
            "join_date": "2026-01-01",
            "annual_entitlement": "14",
            "probation_end_date": "2026-04-01",
            "approver_user_id": "",
            "work_pattern": "five_day",
            "custom_work_days": "",
            "status": "active",
            "role": "manager",
            "active": "1",
            "password": "Password123",
        }
        status, _, _ = self.post("/admin/employees/new", manager_payload, admin_cookie)
        self.assertEqual(status, 303)
        with app.db() as conn:
            manager_user_id = conn.execute("SELECT id FROM users WHERE email = 'boss@example.test'").fetchone()["id"]

        employee_payload = {
            "name": "April Joiner",
            "email": "april.joiner@example.test",
            "department": "Operations",
            "job_title": "Executive",
            "join_date": "2026-04-06",
            "annual_entitlement": "14",
            "probation_end_date": "2026-07-06",
            "approver_user_id": str(manager_user_id),
            "work_pattern": "five_day",
            "custom_work_days": "",
            "status": "active",
            "role": "employee",
            "active": "1",
            "password": "Password123",
        }
        status, _, _ = self.post("/admin/employees/new", employee_payload, admin_cookie)
        self.assertEqual(status, 303)

        with app.db() as conn:
            employee = conn.execute("SELECT * FROM employees WHERE email = 'april.joiner@example.test'").fetchone()
            balance = app.employee_balance(conn, employee["id"], 2026)
            self.assertEqual(balance["calculation"].completed_months, 8)
            self.assertEqual(balance["earned"], 9)

        status, _, _ = self.post(
            "/admin/settings",
            {
                "enforce_mom_three_month_rule": "yes",
                "allow_company_override": "yes",
                "saturday_ph_compensation_method": "off_in_lieu",
                "off_in_lieu_expiry_months": "12",
            },
            admin_cookie,
        )
        self.assertEqual(status, 303)

        status, _, _ = self.post(
            "/admin/holidays",
            {
                "holiday_date": "2026-03-21",
                "name": "Hari Raya Puasa",
            },
            admin_cookie,
        )
        self.assertEqual(status, 303)

        with app.db() as conn:
            oil_balance = app.off_in_lieu_balance(conn, employee["id"])
        self.assertEqual(oil_balance, 1)

        employee_cookie = self.login("april.joiner@example.test")
        status, _, _ = self.post(
            "/leave/new",
            {
                "leave_type": "Annual Leave",
                "start_date": "2026-04-06",
                "end_date": "2026-04-06",
                "reason": "Family appointment",
            },
            employee_cookie,
        )
        self.assertEqual(status, 303)

        status, _, _ = self.post(
            "/leave/new",
            {
                "leave_type": "Off-in-lieu",
                "start_date": "2026-04-07",
                "end_date": "2026-04-07",
                "reason": "Use off-in-lieu credit",
            },
            employee_cookie,
        )
        self.assertEqual(status, 303)

        manager_cookie = self.login("boss@example.test")
        status, _, manager_page = self.get("/manager", manager_cookie)
        self.assertEqual(status, 200)
        self.assertIn(b"April Joiner", manager_page)

        with app.db() as conn:
            requests = conn.execute("SELECT id, leave_type, approver_user_id FROM leave_requests ORDER BY id").fetchall()
            self.assertTrue(all(row["approver_user_id"] == manager_user_id for row in requests))
        for request in requests:
            status, _, _ = self.post(f"/manager/requests/{request['id']}/approve", {}, manager_cookie)
            self.assertEqual(status, 303)

        with app.db() as conn:
            annual = app.employee_balance(conn, employee["id"], 2026)
            oil = app.off_in_lieu_balance(conn, employee["id"])
            approved = conn.execute("SELECT COUNT(*) FROM leave_requests WHERE status = 'approved'").fetchone()[0]
        self.assertEqual(approved, 2)
        self.assertEqual(annual["earned"], 9)
        self.assertEqual(annual["approved"], 1)
        self.assertEqual(annual["balance"], 8)
        self.assertEqual(oil, 0)

        status, _, _ = self.post(
            "/leave/new",
            {
                "leave_type": "Unpaid Leave",
                "start_date": "2026-04-08",
                "end_date": "2026-04-08",
                "reason": "Admin override check",
            },
            employee_cookie,
        )
        self.assertEqual(status, 303)
        with app.db() as conn:
            override_request = conn.execute("SELECT id FROM leave_requests WHERE status = 'pending' ORDER BY id DESC LIMIT 1").fetchone()["id"]
        status, _, _ = self.post(f"/admin/requests/{override_request}/approve", {}, admin_cookie)
        self.assertEqual(status, 303)

        for path in (
            "/admin/export/employees.csv",
            "/admin/export/leaves.csv",
            "/admin/export/balances.csv",
            "/admin/export/off-in-lieu.csv",
            "/admin/export/audit-log.csv",
        ):
            status, headers, data = self.get(path, admin_cookie)
            self.assertEqual(status, 200, path)
            self.assertIn("text/csv", headers["Content-Type"])
            self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
            self.assertGreaterEqual(len(self.csv_rows(data)), 1)

        with app.db() as conn:
            actions = {
                row["action"]
                for row in conn.execute("SELECT action FROM audit_logs").fetchall()
            }
        self.assertIn("employee_created", actions)
        self.assertIn("public_holiday_saved", actions)
        self.assertIn("off_in_lieu_credit_added", actions)
        self.assertIn("leave_request_submitted", actions)
        self.assertIn("leave_approved", actions)
        self.assertIn("admin_override_approval", actions)
        self.assertIn("approver_assigned", actions)


if __name__ == "__main__":
    unittest.main()
