import http.client
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import app


class AuthRoleTests(unittest.TestCase):
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
        response_headers = dict(response.getheaders())
        status = response.status
        client.close()
        return status, response_headers, raw

    def post(self, path, data, cookie=None):
        return self.request("POST", path, data, cookie)

    def get(self, path, cookie=None):
        return self.request("GET", path, None, cookie)

    def setup_admin(self):
        status, _, _ = self.post(
            "/setup",
            {
                "name": "Admin",
                "email": "admin@example.test",
                "password": "Password123",
                "confirm_password": "Password123",
                "department": "HR",
                "join_date": "2026-01-01",
            },
        )
        self.assertEqual(status, 303)
        return self.login("admin@example.test")

    def login(self, email):
        status, headers, _ = self.post("/login", {"email": email, "password": "Password123"})
        self.assertEqual(status, 303)
        return headers["Set-Cookie"]

    def create_user(self, admin_cookie, email, role):
        status, _, _ = self.post(
            "/admin/employees/new",
            {
                "name": email.split("@")[0],
                "email": email,
                "department": "Ops",
                "job_title": "",
                "join_date": "2026-01-01",
                "annual_entitlement": "14",
                "probation_end_date": "2026-04-01",
                "approver_user_id": "",
                "work_pattern": "five_day",
                "custom_work_days": "",
                "status": "active",
                "role": role,
                "active": "1",
                "password": "Password123",
            },
            admin_cookie,
        )
        self.assertEqual(status, 303)

    def test_first_admin_setup_authentication_and_role_boundaries(self):
        status, headers, body = self.get("/")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/setup")

        admin_cookie = self.setup_admin()
        status, _, _ = self.get("/setup", admin_cookie)
        self.assertEqual(status, 303)
        with app.db() as conn:
            stored = conn.execute("SELECT password_hash FROM users WHERE email = 'admin@example.test'").fetchone()["password_hash"]
        self.assertNotEqual(stored, "Password123")
        self.assertTrue(stored.startswith("pbkdf2_sha256$"))

        self.create_user(admin_cookie, "manager@example.test", "manager")
        self.create_user(admin_cookie, "employee@example.test", "employee")
        manager_cookie = self.login("manager@example.test")
        employee_cookie = self.login("employee@example.test")

        status, _, _ = self.get("/admin/settings", manager_cookie)
        self.assertEqual(status, 404)
        status, _, _ = self.get("/admin", employee_cookie)
        self.assertEqual(status, 404)
        status, _, _ = self.get("/manager", manager_cookie)
        self.assertEqual(status, 200)

        status, headers, _ = self.get("/admin")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/login")


if __name__ == "__main__":
    unittest.main()
