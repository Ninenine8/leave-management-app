import unittest
from datetime import date

from leave_rules import calculate_annual_leave_entitlement, completed_service_months, leave_days


class LeaveRuleTests(unittest.TestCase):
    def test_case_a_join_6_apr_2026(self):
        result = calculate_annual_leave_entitlement(date(2026, 4, 6), 2026, 14)

        self.assertEqual(result.completed_months, 8)
        self.assertEqual(result.raw_entitlement, 9.33)
        self.assertEqual(result.rounded_entitlement, 9)
        self.assertEqual(result.payable_entitlement, 9)

    def test_case_b_join_1_jan_2026(self):
        result = calculate_annual_leave_entitlement(date(2026, 1, 1), 2026, 14)

        self.assertEqual(result.completed_months, 12)
        self.assertEqual(result.rounded_entitlement, 14)
        self.assertEqual(result.payable_entitlement, 14)

    def test_case_c_join_15_mar_end_31_jul_2026(self):
        result = calculate_annual_leave_entitlement(
            date(2026, 3, 15),
            2026,
            10,
            service_end_date=date(2026, 7, 31),
        )

        self.assertEqual(result.completed_months, 4)
        self.assertEqual(result.raw_entitlement, 3.33)
        self.assertEqual(result.rounded_entitlement, 3)
        self.assertEqual(result.payable_entitlement, 3)

    def test_case_d_join_1_jul_2026(self):
        result = calculate_annual_leave_entitlement(date(2026, 7, 1), 2026, 14)

        self.assertEqual(result.completed_months, 6)
        self.assertEqual(result.raw_entitlement, 7)
        self.assertEqual(result.rounded_entitlement, 7)
        self.assertEqual(result.payable_entitlement, 7)

    def test_case_e_join_20_oct_2026_not_eligible(self):
        result = calculate_annual_leave_entitlement(date(2026, 10, 20), 2026, 14)

        self.assertEqual(result.completed_months, 2)
        self.assertFalse(result.eligible)
        self.assertEqual(result.raw_entitlement, 2.33)
        self.assertEqual(result.rounded_entitlement, 2)
        self.assertEqual(result.payable_entitlement, 0)

    def test_case_e_company_override_can_make_employee_eligible(self):
        result = calculate_annual_leave_entitlement(
            date(2026, 10, 20),
            2026,
            14,
            company_override=True,
        )

        self.assertEqual(result.completed_months, 2)
        self.assertTrue(result.eligible)
        self.assertEqual(result.rounded_entitlement, 2)
        self.assertEqual(result.payable_entitlement, 2)

    def test_completed_months_do_not_count_calendar_month_names(self):
        self.assertEqual(completed_service_months(date(2026, 4, 6), 2026), 8)

    def test_leave_days_skip_weekends(self):
        self.assertEqual(leave_days(date(2026, 7, 10), date(2026, 7, 13)), 2)

    def test_leave_days_skip_public_holidays(self):
        holidays = {date(2026, 8, 10)}

        self.assertEqual(leave_days(date(2026, 8, 10), date(2026, 8, 11), public_holidays=holidays), 1)

    def test_leave_days_skip_weekends_and_public_holidays(self):
        holidays = {date(2026, 4, 3)}

        self.assertEqual(leave_days(date(2026, 4, 3), date(2026, 4, 6), public_holidays=holidays), 1)

    def test_half_day_on_public_holiday_charges_zero(self):
        holidays = {date(2026, 12, 25)}

        self.assertEqual(leave_days(date(2026, 12, 25), date(2026, 12, 25), True, holidays), 0)

    def test_half_day_only_single_day(self):
        self.assertEqual(leave_days(date(2026, 7, 10), date(2026, 7, 10), True), 0.5)
        with self.assertRaises(ValueError):
            leave_days(date(2026, 7, 10), date(2026, 7, 13), True)


if __name__ == "__main__":
    unittest.main()
