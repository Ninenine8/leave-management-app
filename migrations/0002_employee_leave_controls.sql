ALTER TABLE employees ADD COLUMN probation_start_date TEXT;
ALTER TABLE employees ADD COLUMN probation_period_months INTEGER NOT NULL DEFAULT 3;
ALTER TABLE employees ADD COLUMN probation_status_override TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE employees ADD COLUMN mc_entitlement REAL NOT NULL DEFAULT 14;
ALTER TABLE employees ADD COLUMN hospitalisation_entitlement REAL NOT NULL DEFAULT 60;
ALTER TABLE employees ADD COLUMN childcare_eligible INTEGER NOT NULL DEFAULT 0;
ALTER TABLE employees ADD COLUMN childcare_entitlement REAL NOT NULL DEFAULT 0;
ALTER TABLE employees ADD COLUMN extended_childcare_eligible INTEGER NOT NULL DEFAULT 0;
ALTER TABLE employees ADD COLUMN notes TEXT;

UPDATE employees
SET probation_start_date = COALESCE(probation_start_date, join_date)
WHERE probation_start_date IS NULL;

INSERT OR IGNORE INTO settings (key, value) VALUES
  ('company_name', 'LeaveDesk'),
  ('leave_year_start', '01-01'),
  ('leave_year_end', '12-31'),
  ('default_work_pattern', 'five_day'),
  ('allow_admin_annual_leave_override', 'yes'),
  ('default_annual_leave_entitlement', '14'),
  ('default_mc_entitlement', '14'),
  ('enforce_mom_mc_three_month_rule', 'yes'),
  ('require_mc_attachment', 'yes'),
  ('default_hospitalisation_entitlement', '60'),
  ('enforce_mom_hospitalisation_three_month_rule', 'yes'),
  ('require_hospitalisation_attachment', 'yes'),
  ('enable_childcare_leave', 'yes'),
  ('default_childcare_entitlement', '6'),
  ('default_probation_period_months', '3'),
  ('allow_probation_manual_override', 'yes');
