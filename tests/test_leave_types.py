import tempfile
import unittest
from pathlib import Path

import app


class LeaveTypeTests(unittest.TestCase):
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

    def tearDown(self):
        app.DATA_DIR = self.original_data_dir
        app.UPLOAD_DIR = self.original_upload_dir
        app.DB_PATH = self.original_db_path
        self.tmp.cleanup()

    def add_employee(self):
        with app.db() as conn:
            return conn.execute(
                """
                INSERT INTO employees (
                    name, email, join_date, department, annual_entitlement,
                    probation_end_date, status
                ) VALUES ('Employee', 'employee@example.test', '2026-01-01', 'Ops', 14, '2026-04-01', 'active')
                """
            ).lastrowid

    def add_request(self, employee_id, leave_type, status="approved", days=1):
        with app.db() as conn:
            return conn.execute(
                """
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date, half_day, days, reason, status
                ) VALUES (?, ?, '2026-04-06', '2026-04-06', 0, ?, 'Test', ?)
                """,
                (employee_id, leave_type, days, status),
            ).lastrowid

    def test_annual_leave_deducts_annual_balance(self):
        employee_id = self.add_employee()
        self.add_request(employee_id, "Annual Leave", "approved", 1)

        with app.db() as conn:
            balance = app.employee_balance(conn, employee_id, 2026)

        self.assertEqual(balance["approved"], 1)
        self.assertEqual(balance["balance"], 13)

    def test_medical_leave_has_separate_balance(self):
        employee_id = self.add_employee()
        self.add_request(employee_id, "Medical Leave", "approved", 1)

        with app.db() as conn:
            annual = app.employee_balance(conn, employee_id, 2026)
            medical = app.managed_leave_type_balance(conn, employee_id, "Medical Leave", 2026)

        self.assertEqual(annual["balance"], 14)
        self.assertEqual(medical["balance"], 13)

    def test_hospitalisation_leave_has_separate_balance(self):
        employee_id = self.add_employee()
        self.add_request(employee_id, "Hospitalisation Leave", "approved", 2)

        with app.db() as conn:
            balance = app.managed_leave_type_balance(conn, employee_id, "Hospitalisation Leave", 2026)

        self.assertEqual(balance["entitlement"], 60)
        self.assertEqual(balance["balance"], 58)

    def test_childcare_leave_has_separate_balance(self):
        employee_id = self.add_employee()
        self.add_request(employee_id, "Childcare Leave", "approved", 1)

        with app.db() as conn:
            balance = app.managed_leave_type_balance(conn, employee_id, "Childcare Leave", 2026)

        self.assertEqual(balance["entitlement"], 6)
        self.assertEqual(balance["balance"], 5)

    def test_unpaid_leave_default_does_not_deduct_balance(self):
        employee_id = self.add_employee()
        self.add_request(employee_id, "Unpaid Leave", "approved", 1)

        with app.db() as conn:
            annual = app.employee_balance(conn, employee_id, 2026)
            unpaid = app.managed_leave_type_balance(conn, employee_id, "Unpaid Leave", 2026)

        self.assertEqual(annual["balance"], 14)
        self.assertEqual(unpaid["available"], float("inf"))

    def test_off_in_lieu_deducts_only_off_in_lieu_balance(self):
        employee_id = self.add_employee()
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
            annual_after = app.employee_balance(conn, employee_id, 2026)["balance"]
            oil_after = app.off_in_lieu_balance(conn, employee_id)

        self.assertEqual(oil_after, 0)
        self.assertEqual(annual_after, annual_before)

    def test_disabled_leave_type_is_not_available(self):
        with app.db() as conn:
            conn.execute("UPDATE leave_types SET enabled = 0 WHERE name = 'Medical Leave'")
            config = app.leave_type_config(conn, "Medical Leave")
            enabled_names = [row["name"] for row in app.enabled_leave_types(conn)]

        self.assertEqual(config["enabled"], 0)
        self.assertNotIn("Medical Leave", enabled_names)

    def test_rejected_and_cancelled_leave_do_not_deduct_balance(self):
        employee_id = self.add_employee()
        self.add_request(employee_id, "Annual Leave", "rejected", 1)
        self.add_request(employee_id, "Annual Leave", "cancelled", 1)

        with app.db() as conn:
            balance = app.employee_balance(conn, employee_id, 2026)

        self.assertEqual(balance["approved"], 0)
        self.assertEqual(balance["balance"], 14)


if __name__ == "__main__":
    unittest.main()
