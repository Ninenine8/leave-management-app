import test from "node:test";
import assert from "node:assert/strict";
import {
  calculateAnnualLeaveEntitlement,
  chargeableLeaveDays,
  qualifiesForSaturdayOffInLieu,
  validateAttachment,
} from "../src/rules.js";

test("MOM annual leave: 6 Apr 2026 gives 8 completed months and 9 days", () => {
  const result = calculateAnnualLeaveEntitlement({
    joinDate: "2026-04-06",
    calculationYear: 2026,
    annualEntitlementDays: 14,
  });
  assert.equal(result.completedMonths, 8);
  assert.equal(result.rawEntitlement, 9.33);
  assert.equal(result.roundedEntitlement, 9);
  assert.match(result.explanation, /8 \/ 12 x 14 = 9\.33/);
});

test("MOM annual leave full year and mid-year examples", () => {
  assert.equal(calculateAnnualLeaveEntitlement({ joinDate: "2026-01-01", calculationYear: 2026, annualEntitlementDays: 14 }).roundedEntitlement, 14);
  const mar = calculateAnnualLeaveEntitlement({ joinDate: "2026-03-15", calculationYear: 2026, annualEntitlementDays: 10, serviceEndDate: "2026-07-31" });
  assert.equal(mar.completedMonths, 4);
  assert.equal(mar.rawEntitlement, 3.33);
  assert.equal(mar.roundedEntitlement, 3);
  assert.equal(calculateAnnualLeaveEntitlement({ joinDate: "2026-07-01", calculationYear: 2026, annualEntitlementDays: 14 }).roundedEntitlement, 7);
});

test("3-month MOM eligibility can withhold paid annual leave", () => {
  const result = calculateAnnualLeaveEntitlement({ joinDate: "2026-10-20", calculationYear: 2026, annualEntitlementDays: 14 });
  assert.equal(result.completedMonths, 2);
  assert.equal(result.eligible, false);
  assert.equal(result.payableEntitlement, 0);
});

test("weekends and Singapore public holidays are excluded", () => {
  const days = chargeableLeaveDays({
    startDate: "2026-04-03",
    endDate: "2026-04-06",
    publicHolidays: ["2026-04-03"],
    workPattern: "five_day",
  });
  assert.equal(days, 1);
});

test("Saturday public holiday off-in-lieu qualification", () => {
  assert.equal(qualifiesForSaturdayOffInLieu({ publicHolidayDate: "2026-03-21", workPattern: "five_day", compensationMethod: "off_in_lieu" }), true);
  assert.equal(qualifiesForSaturdayOffInLieu({ publicHolidayDate: "2026-03-21", workPattern: "six_day", compensationMethod: "off_in_lieu" }), false);
  assert.equal(qualifiesForSaturdayOffInLieu({ publicHolidayDate: "2026-05-31", workPattern: "five_day", compensationMethod: "off_in_lieu" }), false);
  assert.equal(qualifiesForSaturdayOffInLieu({ publicHolidayDate: "2026-03-21", workPattern: "five_day", compensationMethod: "salary_in_lieu" }), false);
});

test("attachment validation", () => {
  assert.doesNotThrow(() => validateAttachment({ name: "mc.pdf", type: "application/pdf", size: 1024 }, "Medical Leave"));
  assert.throws(() => validateAttachment(null, "Medical Leave"), /required/);
  assert.throws(() => validateAttachment({ name: "bad.exe", type: "application/x-msdownload", size: 10 }, "Annual Leave"), /PDF, JPG, or PNG/);
  assert.throws(() => validateAttachment({ name: "huge.pdf", type: "application/pdf", size: 6 * 1024 * 1024 }, "Annual Leave"), /5MB/);
});
