import http.client
import io
import tempfile
import threading
import time
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import app


class ExportTests(unittest.TestCase):
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
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.App)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.1)
        self.seed_data()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        app.DATA_DIR = self.original_data_dir
        app.UPLOAD_DIR = self.original_upload_dir
        app.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def seed_data(self):
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
                    probation_end_date, status
                ) VALUES ('Employee User', 'employee@example.test', '2026-04-06', 'Ops', 14, '2026-07-06', 'active')
                """
            ).lastrowid
            admin_user_id = conn.execute(
                "INSERT INTO users (employee_id, email, password_hash, role) VALUES (?, 'admin@example.test', ?, 'admin')",
                (admin_employee_id, app.hash_password("Password123")),
            ).lastrowid
            conn.execute(
                """
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date, half_day, days, reason, status, decided_by
                ) VALUES (?, 'Annual Leave', '2026-08-03', '2026-08-03', 0, 1, 'Family', 'approved', ?)
                """,
                (employee_id, admin_user_id),
            )
            conn.execute(
                """
                INSERT INTO off_in_lieu_credits (
                    employee_id, public_holiday_name, public_holiday_date, credit_date,
                    credit_amount_days, used_amount_days, remaining_amount_days, expiry_date, status
                ) VALUES (?, 'Saturday Holiday', '2026-03-21', '2026-03-21', 1, 0, 1, '2027-03-21', 'active')
                """,
                (employee_id,),
            )
            app.add_audit_log(conn, admin_user_id, "test export", "Created test export data.", employee_id)
        app.UPLOAD_DIR.mkdir(exist_ok=True)
        (app.UPLOAD_DIR / "test.pdf").write_bytes(b"%PDF- test")

    def login_admin(self):
        status, headers, _ = self.post("/login", {"email": "admin@example.test", "password": "Password123"})
        self.assertEqual(status, 303)
        return headers["Set-Cookie"]

    def post(self, path, data, cookie=None):
        body = urlencode(data)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if cookie:
            headers["Cookie"] = cookie
        return self.request("POST", path, body.encode("utf-8"), headers)

    def get(self, path, cookie):
        return self.request("GET", path, None, {"Cookie": cookie})

    def request(self, method, path, body, headers):
        client = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        client.request(method, path, body=body, headers=headers)
        response = client.getresponse()
        data = response.read()
        response_headers = dict(response.getheaders())
        status = response.status
        client.close()
        return status, response_headers, data

    def assert_excel_csv(self, path, expected_header):
        cookie = self.login_admin()
        status, headers, data = self.get(path, cookie)

        self.assertEqual(status, 200)
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertIn(expected_header, data.decode("utf-8-sig").splitlines()[0])

    def test_admin_can_export_all_csv_files_for_excel(self):
        self.assert_excel_csv("/admin/export/employees.csv", "employee_id")
        self.assert_excel_csv("/admin/export/users.csv", "user_id")
        self.assert_excel_csv("/admin/export/leaves.csv", "leave_request_id")
        self.assert_excel_csv("/admin/export/balances.csv", "annual_leave_balance")
        self.assert_excel_csv("/admin/export/off-in-lieu.csv", "credit_id")
        self.assert_excel_csv("/admin/export/holidays.csv", "holiday_id")
        self.assert_excel_csv("/admin/export/audit-log.csv", "audit_log_id")

    def test_admin_can_download_backup_zip_with_app_data(self):
        cookie = self.login_admin()
        status, headers, data = self.get("/admin/export/backup.zip", cookie)

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/zip")
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = set(archive.namelist())

        self.assertIn("csv/employees.csv", names)
        self.assertIn("csv/leave_requests.csv", names)
        self.assertIn("csv/leave_balances_2026.csv", names)
        self.assertIn("csv/off_in_lieu_credits.csv", names)
        self.assertIn("csv/audit_logs.csv", names)
        self.assertIn("database/leave_app.sqlite3", names)
        self.assertIn("database/leave_app.sql", names)
        self.assertIn("uploads/test.pdf", names)


if __name__ == "__main__":
    unittest.main()
