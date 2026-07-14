ALTER TABLE employees ADD COLUMN invite_token TEXT;
ALTER TABLE employees ADD COLUMN invitation_status TEXT NOT NULL DEFAULT 'active';
ALTER TABLE employees ADD COLUMN invitation_created_at TEXT;
ALTER TABLE employees ADD COLUMN invitation_used_at TEXT;
ALTER TABLE employees ADD COLUMN pending_role TEXT NOT NULL DEFAULT 'employee';

CREATE UNIQUE INDEX IF NOT EXISTS idx_employees_invite_token ON employees(invite_token);
