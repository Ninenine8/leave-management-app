export const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;
export const ALLOWED_ATTACHMENT_TYPES = new Map([
  ["application/pdf", ".pdf"],
  ["image/jpeg", ".jpg"],
  ["image/png", ".png"],
]);

export function parseDate(value) {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day));
}

export function formatDate(date) {
  return date.toISOString().slice(0, 10);
}

export function addMonths(date, months) {
  const year = date.getUTCFullYear();
  const month = date.getUTCMonth();
  const day = date.getUTCDate();
  const target = new Date(Date.UTC(year, month + months, 1));
  const lastDay = new Date(Date.UTC(target.getUTCFullYear(), target.getUTCMonth() + 1, 0)).getUTCDate();
  target.setUTCDate(Math.min(day, lastDay));
  return target;
}

export function completedServiceMonths(joinDateInput, calculationYear, serviceEndDateInput = null) {
  const joinDate = typeof joinDateInput === "string" ? parseDate(joinDateInput) : joinDateInput;
  const yearStart = new Date(Date.UTC(calculationYear, 0, 1));
  const yearEnd = new Date(Date.UTC(calculationYear, 11, 31));
  const serviceEnd = serviceEndDateInput
    ? (typeof serviceEndDateInput === "string" ? parseDate(serviceEndDateInput) : serviceEndDateInput)
    : yearEnd;
  const endDate = serviceEnd < yearEnd ? serviceEnd : yearEnd;
  if (joinDate > endDate) return 0;
  const serviceAnchor = joinDate < yearStart ? yearStart : joinDate;
  let completed = 0;
  for (let month = 1; month <= 12; month += 1) {
    const completedMonthEnd = addMonths(serviceAnchor, month);
    completedMonthEnd.setUTCDate(completedMonthEnd.getUTCDate() - 1);
    if (completedMonthEnd <= endDate) completed = month;
    else break;
  }
  return completed;
}

export function momRound(rawDays) {
  const whole = Math.floor(rawDays);
  return whole + (rawDays - whole >= 0.5 ? 1 : 0);
}

export function calculateAnnualLeaveEntitlement({
  joinDate,
  calculationYear,
  annualEntitlementDays = 14,
  serviceEndDate = `${calculationYear}-12-31`,
  enforceThreeMonthRule = true,
  companyOverride = false,
}) {
  const completedMonths = completedServiceMonths(joinDate, calculationYear, serviceEndDate);
  const rawEntitlement = Math.round(((completedMonths / 12) * Number(annualEntitlementDays)) * 100) / 100;
  const roundedEntitlement = momRound(rawEntitlement);
  const eligible = completedMonths >= 3 || !enforceThreeMonthRule || companyOverride;
  const roundedDirection = roundedEntitlement > Math.floor(rawEntitlement) ? "up" : "down";
  const rawText = rawEntitlement.toFixed(2).replace(/\.?0+$/, "");
  const start = humanDate(joinDate);
  const end = humanDate(serviceEndDate);
  const explanation = `${calculationYear} annual leave entitlement: ${roundedEntitlement} days. Based on ${completedMonths} completed months of service from ${start} to ${end}. Calculation: ${completedMonths} / 12 x ${Number(annualEntitlementDays)} = ${rawText}, rounded ${roundedDirection} to ${roundedEntitlement} days under MOM rounding rules.`;
  return {
    completedMonths,
    rawEntitlement,
    roundedEntitlement,
    payableEntitlement: eligible ? roundedEntitlement : 0,
    eligible,
    explanation: eligible ? explanation : `${explanation} Employee has not completed 3 months of service, so payable paid annual leave is 0 days while the MOM eligibility rule is enforced.`,
  };
}

export function humanDate(value) {
  const date = typeof value === "string" ? parseDate(value) : value;
  return `${date.getUTCDate()} ${date.toLocaleString("en-SG", { month: "short", timeZone: "UTC" })} ${date.getUTCFullYear()}`;
}

export function workDaysForPattern(pattern = "five_day", customWorkDays = "") {
  if (pattern === "six_day") return new Set([1, 2, 3, 4, 5, 6]);
  if (pattern === "five_half_day") return new Set([1, 2, 3, 4, 5, 6]);
  if (pattern === "custom") {
    const days = String(customWorkDays || "").split(",").map((x) => Number(x.trim())).filter((x) => x >= 1 && x <= 7);
    return new Set(days.length ? days : [1, 2, 3, 4, 5]);
  }
  return new Set([1, 2, 3, 4, 5]);
}

export function chargeableLeaveDays({ startDate, endDate, halfDay = false, publicHolidays = [], workPattern = "five_day", customWorkDays = "" }) {
  const start = parseDate(startDate);
  const end = parseDate(endDate);
  if (end < start) throw new Error("End date cannot be before start date");
  const holidays = new Set(publicHolidays);
  const workDays = workDaysForPattern(workPattern, customWorkDays);
  let days = 0;
  for (let cursor = new Date(start); cursor <= end; cursor.setUTCDate(cursor.getUTCDate() + 1)) {
    const isoDay = cursor.getUTCDay() === 0 ? 7 : cursor.getUTCDay();
    if (workDays.has(isoDay) && !holidays.has(formatDate(cursor))) days += 1;
  }
  if (halfDay) {
    if (formatDate(start) !== formatDate(end)) throw new Error("Half-day leave must start and end on the same date");
    return days > 0 ? 0.5 : 0;
  }
  return days;
}

export function qualifiesForSaturdayOffInLieu({ publicHolidayDate, workPattern = "five_day", customWorkDays = "", compensationMethod = "off_in_lieu" }) {
  if (compensationMethod !== "off_in_lieu") return false;
  const date = parseDate(publicHolidayDate);
  const isoDay = date.getUTCDay() === 0 ? 7 : date.getUTCDay();
  if (isoDay !== 6) return false;
  return !workDaysForPattern(workPattern, customWorkDays).has(6);
}

export function validateAttachment(file, leaveTypeName) {
  const required = leaveTypeName === "Medical Leave" || leaveTypeName === "Hospitalisation Leave";
  if (!file || !file.name) {
    if (required) throw new Error("Attachment is required for this leave type.");
    return null;
  }
  if (!ALLOWED_ATTACHMENT_TYPES.has(file.type)) {
    throw new Error("Attachment must be a PDF, JPG, or PNG file.");
  }
  if (file.size > MAX_ATTACHMENT_BYTES) {
    throw new Error("Attachment must be 5MB or smaller.");
  }
  return true;
}
