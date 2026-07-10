from dataclasses import dataclass
from datetime import date, datetime, timedelta
import math


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def month_add(start_date: date, months: int) -> date:
    month = start_date.month - 1 + months
    year = start_date.year + month // 12
    month = month % 12 + 1
    days_in_month = [
        31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31,
        30,
        31,
        30,
        31,
        31,
        30,
        31,
        30,
        31,
    ]
    return date(year, month, min(start_date.day, days_in_month[month - 1]))


def mom_round_leave_days(raw_days: float) -> int:
    whole_days = math.floor(raw_days)
    fraction = raw_days - whole_days
    return whole_days + (1 if fraction >= 0.5 else 0)


def completed_service_months(join_date: date, calculation_year: int, service_end_date: date | None = None) -> int:
    """Count only fully completed service months for the calculation year."""
    year_start = date(calculation_year, 1, 1)
    year_end = date(calculation_year, 12, 31)
    end_date = min(service_end_date or year_end, year_end)

    if join_date > end_date:
        return 0

    service_anchor = year_start if join_date < year_start else join_date
    if service_anchor > end_date:
        return 0

    completed = 0
    for month_number in range(1, 13):
        completed_month_end = month_add(service_anchor, month_number) - timedelta(days=1)
        if completed_month_end <= end_date:
            completed = month_number
        else:
            break
    return completed


@dataclass(frozen=True)
class AnnualLeaveCalculation:
    join_date: date
    calculation_year: int
    annual_entitlement_days: float
    service_end_date: date
    completed_months: int
    raw_entitlement: float
    rounded_entitlement: int
    eligible: bool
    enforce_three_month_rule: bool
    company_override_applied: bool

    @property
    def payable_entitlement(self) -> int:
        return self.rounded_entitlement if self.eligible else 0

    @property
    def rounded_direction(self) -> str:
        if self.rounded_entitlement > math.floor(self.raw_entitlement):
            return "up"
        return "down"

    @property
    def explanation(self) -> str:
        start_text = f"{self.join_date.day} {self.join_date.strftime('%b %Y')}"
        end_text = f"{self.service_end_date.day} {self.service_end_date.strftime('%b %Y')}"
        raw_text = f"{self.raw_entitlement:.2f}".rstrip("0").rstrip(".")
        base = (
            f"{self.calculation_year} annual leave entitlement: {self.rounded_entitlement} days. "
            f"Based on {self.completed_months} completed months of service from {start_text} to {end_text}. "
            f"Calculation: {self.completed_months} / 12 × {self.annual_entitlement_days:g} = {raw_text}, "
            f"rounded {self.rounded_direction} to {self.rounded_entitlement} days under MOM rounding rules."
        )
        if not self.eligible:
            return base + " Employee has not completed 3 months of service, so payable paid annual leave is 0 days while the MOM eligibility rule is enforced."
        if self.company_override_applied and self.completed_months < 3:
            return base + " Company override is applied even though the employee has not completed 3 months of service."
        return base


def calculate_annual_leave_entitlement(
    join_date: date,
    calculation_year: int,
    annual_entitlement_days: float = 14,
    service_end_date: date | None = None,
    enforce_three_month_rule: bool = True,
    company_override: bool = False,
) -> AnnualLeaveCalculation:
    service_end = service_end_date or date(calculation_year, 12, 31)
    completed_months = completed_service_months(join_date, calculation_year, service_end)
    raw_entitlement = round((completed_months / 12) * float(annual_entitlement_days), 2)
    rounded_entitlement = mom_round_leave_days(raw_entitlement)
    eligible = completed_months >= 3 or not enforce_three_month_rule or company_override

    return AnnualLeaveCalculation(
        join_date=join_date,
        calculation_year=calculation_year,
        annual_entitlement_days=float(annual_entitlement_days),
        service_end_date=min(service_end, date(calculation_year, 12, 31)),
        completed_months=completed_months,
        raw_entitlement=raw_entitlement,
        rounded_entitlement=rounded_entitlement,
        eligible=eligible,
        enforce_three_month_rule=enforce_three_month_rule,
        company_override_applied=company_override,
    )


def leave_days(start_date: date, end_date: date, half_day: bool = False, public_holidays: set[date] | None = None) -> float:
    """Count chargeable leave days, excluding weekends and public holidays."""
    if end_date < start_date:
        raise ValueError("End date cannot be before start date")

    holidays = public_holidays or set()
    days = 0
    cursor = start_date
    while cursor <= end_date:
        if cursor.weekday() < 5 and cursor not in holidays:
            days += 1
        cursor += timedelta(days=1)

    if half_day:
        if start_date != end_date:
            raise ValueError("Half-day leave must start and end on the same date")
        return 0.5 if days else 0.0
    return float(days)
