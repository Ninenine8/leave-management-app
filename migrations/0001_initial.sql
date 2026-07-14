PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS employees (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  department TEXT NOT NULL,
  job_title TEXT,
  join_date TEXT NOT NULL,
  probation_start_date TEXT,
  probation_period_months INTEGER NOT NULL DEFAULT 3,
  probation_end_date TEXT NOT NULL,
  probation_status_override TEXT NOT NULL DEFAULT 'auto',
  annual_entitlement REAL NOT NULL DEFAULT 14,
  mc_entitlement REAL NOT NULL DEFAULT 14,
  hospitalisation_entitlement REAL NOT NULL DEFAULT 60,
  childcare_eligible INTEGER NOT NULL DEFAULT 0,
  childcare_entitlement REAL NOT NULL DEFAULT 0,
  extended_childcare_eligible INTEGER NOT NULL DEFAULT 0,
  mom_eligibility_override INTEGER NOT NULL DEFAULT 0,
  work_pattern TEXT NOT NULL DEFAULT 'five_day',
  custom_work_days TEXT,
  approver_user_id INTEGER,
  status TEXT NOT NULL DEFAULT 'active',
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('admin', 'manager', 'employee')),
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leave_types (
  name TEXT PRIMARY KEY,
  enabled INTEGER NOT NULL DEFAULT 1,
  deducts_balance INTEGER NOT NULL DEFAULT 1,
  balance_category TEXT NOT NULL,
  default_entitlement REAL NOT NULL DEFAULT 0,
  attachment_required INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS leave_balances (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  balance_category TEXT NOT NULL,
  year INTEGER NOT NULL,
  adjustment_days REAL NOT NULL DEFAULT 0,
  notes TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS leave_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  leave_type TEXT NOT NULL REFERENCES leave_types(name),
  balance_category TEXT NOT NULL,
  start_date TEXT NOT NULL,
  end_date TEXT NOT NULL,
  half_day INTEGER NOT NULL DEFAULT 0,
  days REAL NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'cancelled')),
  approver_user_id INTEGER REFERENCES users(id),
  decided_by INTEGER REFERENCES users(id),
  decided_at TEXT,
  decision_note TEXT,
  attachment_key TEXT,
  attachment_filename TEXT,
  attachment_type TEXT,
  attachment_size INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS public_holidays (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  holiday_date TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS off_in_lieu_credits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
  public_holiday_name TEXT,
  public_holiday_date TEXT,
  credit_date TEXT NOT NULL,
  credit_amount_days REAL NOT NULL DEFAULT 1,
  used_amount_days REAL NOT NULL DEFAULT 0,
  expiry_date TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active', 'used', 'expired', 'cancelled')),
  notes TEXT,
  created_by INTEGER REFERENCES users(id),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id),
  employee_id INTEGER REFERENCES employees(id),
  action_type TEXT NOT NULL,
  before_value TEXT,
  after_value TEXT,
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  link TEXT,
  read_at TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO leave_types (name, enabled, deducts_balance, balance_category, default_entitlement, attachment_required) VALUES
  ('Annual Leave', 1, 1, 'annual', 14, 0),
  ('Medical Leave', 1, 1, 'medical', 14, 1),
  ('Hospitalisation Leave', 1, 1, 'hospitalisation', 60, 1),
  ('Childcare Leave', 1, 1, 'childcare', 6, 0),
  ('Unpaid Leave', 1, 0, 'unpaid', 0, 0),
  ('Off-in-lieu', 1, 1, 'off_in_lieu', 0, 0);

INSERT OR IGNORE INTO settings (key, value) VALUES
  ('company_name', 'LeaveDesk'),
  ('leave_year_start', '01-01'),
  ('leave_year_end', '12-31'),
  ('default_work_pattern', 'five_day'),
  ('default_annual_leave_entitlement', '14'),
  ('enforce_mom_three_month_rule', 'yes'),
  ('allow_admin_annual_leave_override', 'yes'),
  ('default_mc_entitlement', '14'),
  ('enforce_mom_mc_three_month_rule', 'yes'),
  ('require_mc_attachment', 'yes'),
  ('default_hospitalisation_entitlement', '60'),
  ('enforce_mom_hospitalisation_three_month_rule', 'yes'),
  ('require_hospitalisation_attachment', 'yes'),
  ('enable_childcare_leave', 'yes'),
  ('default_childcare_entitlement', '6'),
  ('default_probation_period_months', '3'),
  ('allow_probation_manual_override', 'yes'),
  ('saturday_ph_compensation_method', 'off_in_lieu'),
  ('off_in_lieu_default_expiry_months', '12');

INSERT OR IGNORE INTO public_holidays (holiday_date, name) VALUES
  ('2026-01-01', 'New Year''s Day'),
  ('2026-02-17', 'Chinese New Year'),
  ('2026-02-18', 'Chinese New Year Holiday'),
  ('2026-03-21', 'Hari Raya Puasa'),
  ('2026-04-03', 'Good Friday'),
  ('2026-05-01', 'Labour Day'),
  ('2026-05-27', 'Hari Raya Haji'),
  ('2026-05-31', 'Vesak Day'),
  ('2026-06-01', 'Vesak Day Holiday'),
  ('2026-08-09', 'National Day'),
  ('2026-08-10', 'National Day Holiday'),
  ('2026-11-08', 'Deepavali'),
  ('2026-11-09', 'Deepavali Holiday'),
  ('2026-12-25', 'Christmas Day');

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_leave_requests_employee ON leave_requests(employee_id);
CREATE INDEX IF NOT EXISTS idx_leave_requests_approver ON leave_requests(approver_user_id);
CREATE INDEX IF NOT EXISTS idx_oil_employee ON off_in_lieu_credits(employee_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);
