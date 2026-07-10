import tempfile
import http.client
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlencode

import app


class OffInLieuTests(unittest.TestCase):
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
        conn = app.db()
        conn.execute("DELETE FROM public_holidays")
        conn.commit()
        conn.close()

    def tearDown(self):
        app.DATA_DIR = self.original_data_dir
        app.UPLOAD_DIR = self.original_upload_dir
        app.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def add_employee(self, email="employee@example.test", work_pattern="five_day"):
        with app.db() as conn:
            employee_id = conn.execute(
                """
                INSERT INTO employees (
                    name, email, join_date, department, annual_entitlement,
                    probation_end_date, status, work_pattern
                ) VALUES (?, ?, '2026-01-01', 'Ops', 14, '2026-04-01', 'active', ?)
                """,
                ("Employee", email, work_pattern),
            ).lastrowid
        return employee_id

    def add_public_holiday(self, holiday_date, name="Test Holiday"):
        with app.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO public_holidays (holiday_date, name) VALUES (?, ?)",
                (holiday_date, name),
            )

    def test_case_a_five_day_saturday_public_holiday_gets_credit(self):
        employee_id = self.add_employee(work_pattern="five_day")
        self.add_public_holiday("2026-03-21", "Saturday Holiday")

        with app.db() as conn:
            created = app.generate_off_in_lieu_credits(conn, year=2026)
            balance = app.off_in_lieu_balance(conn, employee_id)

        self.assertEqual(created, 1)
        self.assertEqual(balance, 1)

    def test_case_b_six_day_saturday_public_holiday_gets_no_credit(self):
        employee_id = self.add_employee(work_pattern="six_day")
        self.add_public_holiday("2026-03-21", "Saturday Holiday")

        with app.db() as conn:
            created = app.generate_off_in_lieu_credits(conn, year=2026)
            balance = app.off_in_lieu_balance(conn, employee_id)

        self.assertEqual(created, 0)
        self.assertEqual(balance, 0)

    def test_case_c_sunday_public_holiday_gets_no_auto_credit(self):
        employee_id = self.add_employee(work_pattern="five_day")
        self.add_public_holiday("2026-08-09", "Sunday Holiday")

        with app.db() as conn:
            created = app.generate_off_in_lieu_credits(conn, year=2026)
            balance = app.off_in_lieu_balance(conn, employee_id)

        self.assertEqual(created, 0)
        self.assertEqual(balance, 0)

    def test_salary_in_lieu_setting_does_not_generate_credit(self):
        employee_id = self.add_employee(work_pattern="five_day")
        self.add_public_holiday("2026-03-21", "Saturday Holiday")

        with app.db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES ('saturday_ph_compensation_method', 'salary_in_lieu')"
            )
            created = app.generate_off_in_lieu_credits(conn, year=2026)
            balance = app.off_in_lieu_balance(conn, employee_id)

        self.assertEqual(created, 0)
        self.assertEqual(balance, 0)

    def test_case_d_approved_off_in_lieu_deducts_oil_only(self):
        employee_id = self.add_employee(work_pattern="five_day")
        with app.db() as conn:
            conn.execute(
                """
                INSERT INTO off_in_lieu_credits (
                    employee_id, public_holiday_name, public_holiday_date, credit_date,
                    credit_amount_days, used_amount_days, remaining_amount_days, expiry_date, status
                ) VALUES (?, 'Saturday Holiday', '2026-03-21', '2026-03-21', 1, 0, 1, '2027-03-21', 'active')
                """,
                (employee_id,),
            )
            request_id = conn.execute(
                """
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date, half_day, days, reason, status
                ) VALUES (?, 'Off-in-lieu', '2026-04-06', '2026-04-06', 0, 1, 'Use OIL', 'pending')
                """,
                (employee_id,),
            ).lastrowid
            annual_before = app.employee_balance(conn, employee_id, 2026)["balance"]
            app.consume_off_in_lieu(conn, employee_id, request_id, 1)
            conn.execute("UPDATE leave_requests SET status = 'approved' WHERE id = ?", (request_id,))
            oil_after = app.off_in_lieu_balance(conn, employee_id)
            annual_after = app.employee_balance(conn, employee_id, 2026)["balance"]

        self.assertEqual(oil_after, 0)
        self.assertEqual(annual_after, annual_before)

    def test_case_e_off_in_lieu_request_blocked_when_balance_zero(self):
        employee_id = self.add_employee(work_pattern="five_day")
        with app.db() as conn:
            conn.execute(
                "INSERT INTO users (employee_id, email, password_hash, role) VALUES (?, ?, ?, 'employee')",
                (employee_id, "employee@example.test", app.hash_password("Password123")),
            )

        with app.db() as conn:
            available = app.usable_off_in_lieu_balance(conn, employee_id)

        self.assertEqual(available, 0)

        server = ThreadingHTTPServer(("127.0.0.1", 0), app.App)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.1)
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        client.request(
            "POST",
            "/login",
            body=urlencode({"email": "employee@example.test", "password": "Password123"}),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        response = client.getresponse()
        cookie = response.getheader("Set-Cookie")
        response.read()
        self.assertEqual(response.status, 303)
        client.request(
            "POST",
            "/leave/new",
            body=urlencode(
                {
                    "leave_type": "Off-in-lieu",
                    "start_date": "2026-04-06",
                    "end_date": "2026-04-06",
                    "reason": "No balance",
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded", "Cookie": cookie},
        )
        response = client.getresponse()
        body = response.read().decode("utf-8")
        client.close()
        server.shutdown()
        server.server_close()

        self.assertEqual(response.status, 400)
        self.assertIn("Not enough Off-in-lieu balance", body)


if __name__ == "__main__":
    unittest.main()
