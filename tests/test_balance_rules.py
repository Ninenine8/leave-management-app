import tempfile
import unittest
from pathlib import Path

import app


class BalanceRuleTests(unittest.TestCase):
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

    def test_only_annual_leave_reduces_annual_balance(self):
        employee_id = self.add_employee()
        with app.db() as conn:
            conn.execute(
                """
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date, half_day, days, reason, status
                ) VALUES (?, 'Medical leave', '2026-04-06', '2026-04-06', 0, 1, 'Medical', 'approved')
                """,
                (employee_id,),
            )
            balance = app.employee_balance(conn, employee_id, 2026)

        self.assertEqual(balance["approved"], 0)
        self.assertEqual(balance["balance"], 14)

    def test_pending_annual_leave_is_separate_from_other_pending_leave(self):
        employee_id = self.add_employee()
        with app.db() as conn:
            conn.execute(
                """
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date, half_day, days, reason, status
                ) VALUES (?, 'Unpaid leave', '2026-04-06', '2026-04-06', 0, 1, 'Unpaid', 'pending')
                """,
                (employee_id,),
            )
            conn.execute(
                """
                INSERT INTO leave_requests (
                    employee_id, leave_type, start_date, end_date, half_day, days, reason, status
                ) VALUES (?, 'Annual leave', '2026-04-07', '2026-04-07', 0, 1, 'Annual', 'pending')
                """,
                (employee_id,),
            )
            balance = app.employee_balance(conn, employee_id, 2026)

        self.assertEqual(balance["pending"], 1)
        self.assertEqual(balance["balance"], 14)


if __name__ == "__main__":
    unittest.main()
