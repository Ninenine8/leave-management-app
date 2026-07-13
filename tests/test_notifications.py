import http.client
import tempfile
import threading
import time
import unittest
from datetime import date, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import app


class NotificationTests(unittest.TestCase):
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
        self.server = None
        self.thread = None

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.UPLOAD_DIR = self.original_upload_dir
        app.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def start_server(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.App)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.1)
        return self.server.server_address[1]

    def post(self, port, path, data, cookie=None):
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        client.request("POST", path, body=urlencode(data), headers=headers)
        response = client.getresponse()
        body = response.read().decode("utf-8")
        headers = dict(response.getheaders())
        status = response.status
        client.close()
        return status, headers, body

    def add_users(self):
        with app.db() as conn:
            admin_employee_id = conn.execute(
                """
                INSERT INTO employees (
                    name, email, join_date, department, annual_entitlement,
                    probation_end_date, status
                ) VALUES ('Admin User', 'admin@example.test', '2026-01-01', 'HR', 14, '2026-04-01', 'active')
                """
            ).lastrowid
            employee_id = conn.execute(
                """
                INSERT INTO employees (
                    name, email, join_date, department, annual_entitlement,
                    probation_end_date, status, work_pattern
                ) VALUES ('Employee User', 'employee@example.test', '2026-01-01', 'Ops', 14, '2026-04-01', 'active', 'five_day')
                """
            ).lastrowid
            admin_user_id = conn.execute(
                "INSERT INTO users (employee_id, email, password_hash, role) VALUES (?, 'admin@example.test', ?, 'admin')",
                (admin_employee_id, app.hash_password("Password123")),
            ).lastrowid
            employee_user_id = conn.execute(
                "INSERT INTO users (employee_id, email, password_hash, role) VALUES (?, 'employee@example.test', ?, 'employee')",
                (employee_id, app.hash_password("Password123")),
            ).lastrowid
        return admin_user_id, employee_user_id, employee_id

    def login(self, port, email):
        status, headers, _ = self.post(port, "/login", {"email": email, "password": "Password123"})
        self.assertEqual(status, 303)
        return headers["Set-Cookie"]

    def notification_titles(self, user_id):
        with app.db() as conn:
            return [
                row["title"]
                for row in conn.execute(
                    "SELECT title FROM notifications WHERE user_id = ? ORDER BY id",
                    (user_id,),
                )
            ]

    def test_leave_submission_approval_and_mark_read_notifications(self):
        admin_user_id, employee_user_id, _ = self.add_users()
        port = self.start_server()
        employee_cookie = self.login(port, "employee@example.test")
        status, _, _ = self.post(
            port,
            "/leave/new",
            {
                "leave_type": "Annual Leave",
                "start_date": "2026-04-06",
                "end_date": "2026-04-06",
                "reason": "Family",
            },
            employee_cookie,
        )
        self.assertEqual(status, 303)

        self.assertIn("Leave submitted", self.notification_titles(employee_user_id))
        self.assertIn("New leave request submitted", self.notification_titles(admin_user_id))

        with app.db() as conn:
            request_id = conn.execute("SELECT id FROM leave_requests").fetchone()["id"]

        admin_cookie = self.login(port, "admin@example.test")
        status, _, _ = self.post(port, f"/admin/requests/{request_id}/approve", {}, admin_cookie)
        self.assertEqual(status, 303)
        self.assertIn("Leave approved", self.notification_titles(employee_user_id))

        status, _, _ = self.post(port, "/notifications/read", {}, employee_cookie)
        self.assertEqual(status, 303)
        with app.db() as conn:
            unread = app.unread_notification_count(conn, employee_user_id)
        self.assertEqual(unread, 0)

    def test_rejected_leave_notifies_employee(self):
        admin_user_id, employee_user_id, employee_id = self.add_users()
        del admin_user_id
        with app.db() as conn:
            request_id = conn.execute(
                """
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date, half_day, days, reason, status
                ) VALUES (?, 'Annual Leave', '2026-04-06', '2026-04-06', 0, 1, 'Family', 'pending')
                """,
                (employee_id,),
            ).lastrowid

        port = self.start_server()
        admin_cookie = self.login(port, "admin@example.test")
        status, _, _ = self.post(port, f"/admin/requests/{request_id}/reject", {}, admin_cookie)

        self.assertEqual(status, 303)
        self.assertIn("Leave rejected", self.notification_titles(employee_user_id))

    def test_off_in_lieu_credit_and_expiry_notifications(self):
        admin_user_id, employee_user_id, employee_id = self.add_users()
        with app.db() as conn:
            conn.execute("DELETE FROM public_holidays")
            conn.execute(
                "INSERT INTO public_holidays (holiday_date, name) VALUES ('2026-03-21', 'Saturday Holiday')"
            )
            created = app.generate_off_in_lieu_credits(conn, employee_id=employee_id, year=2026)
            self.assertEqual(created, 1)

            expiry = (date.today() + timedelta(days=10)).isoformat()
            conn.execute(
                """
                UPDATE off_in_lieu_credits
                SET expiry_date = ?
                WHERE employee_id = ?
                """,
                (expiry, employee_id),
            )
            app.create_expiring_off_in_lieu_notifications(conn, days=30)
            app.create_expiring_off_in_lieu_notifications(conn, days=30)

        employee_titles = self.notification_titles(employee_user_id)
        admin_titles = self.notification_titles(admin_user_id)
        self.assertEqual(employee_titles.count("Off-in-lieu credited"), 1)
        self.assertEqual(employee_titles.count("Off-in-lieu expiring soon"), 1)
        self.assertEqual(admin_titles.count("Off-in-lieu credit expiring soon"), 1)

    def test_insufficient_balance_attempt_notifies_admin(self):
        admin_user_id, _, _ = self.add_users()
        port = self.start_server()
        employee_cookie = self.login(port, "employee@example.test")
        status, _, body = self.post(
            port,
            "/leave/new",
            {
                "leave_type": "Off-in-lieu",
                "start_date": "2026-04-06",
                "end_date": "2026-04-06",
                "reason": "No balance",
            },
            employee_cookie,
        )

        self.assertEqual(status, 400)
        self.assertIn("Not enough Off-in-lieu balance", body)
        self.assertIn("Insufficient balance attempt", self.notification_titles(admin_user_id))


if __name__ == "__main__":
    unittest.main()
