import cgi
import csv
import html
import io
import json
import os
import re
import secrets
import shutil
import sqlite3
import zipfile
from datetime import date, datetime, timedelta
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse
import hashlib

from leave_rules import calculate_annual_leave_entitlement, leave_days, parse_date


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", DATA_DIR / "uploads"))
DB_PATH = Path(os.environ.get("DB_PATH", DATA_DIR / "leave_app.sqlite3"))
SESSION_DAYS = 7
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
ALLOWED_ATTACHMENT_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
ATTACHMENT_REQUIRED_LEAVE_TYPES = {"Medical Leave", "Hospitalisation Leave"}
DEFAULT_SG_PUBLIC_HOLIDAYS_2026 = [
    ("2026-01-01", "New Year's Day"),
    ("2026-02-17", "Chinese New Year"),
    ("2026-02-18", "Chinese New Year Holiday"),
    ("2026-03-21", "Hari Raya Puasa"),
    ("2026-04-03", "Good Friday"),
    ("2026-05-01", "Labour Day"),
    ("2026-05-27", "Hari Raya Haji"),
    ("2026-05-31", "Vesak Day"),
    ("2026-06-01", "Vesak Day Holiday"),
    ("2026-08-09", "National Day"),
    ("2026-08-10", "National Day Holiday"),
    ("2026-11-08", "Deepavali"),
    ("2026-11-09", "Deepavali Holiday"),
    ("2026-12-25", "Christmas Day"),
]
DEFAULT_LEAVE_TYPES = [
    ("Annual Leave", 1, 1, "annual", 0),
    ("Medical Leave", 1, 1, "custom", 14),
    ("Hospitalisation Leave", 1, 1, "custom", 60),
    ("Childcare Leave", 1, 1, "custom", 6),
    ("Unpaid Leave", 1, 0, "custom", 0),
    ("Off-in-lieu", 1, 1, "off_in_lieu", 0),
]


def db():
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                join_date TEXT NOT NULL,
                department TEXT NOT NULL,
                annual_entitlement REAL NOT NULL DEFAULT 14,
                probation_end_date TEXT NOT NULL,
                mom_eligibility_override INTEGER NOT NULL DEFAULT 0,
                work_pattern TEXT NOT NULL DEFAULT 'five_day',
                custom_work_days TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('employee', 'admin', 'manager')),
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS leave_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                leave_type TEXT NOT NULL,
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                half_day INTEGER NOT NULL DEFAULT 0,
                days REAL NOT NULL,
                reason TEXT NOT NULL,
                attachment_path TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                admin_note TEXT,
                approver_user_id INTEGER REFERENCES users(id),
                decided_by INTEGER REFERENCES users(id),
                decided_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS leave_types (
                name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                deducts_balance INTEGER NOT NULL DEFAULT 1,
                balance_kind TEXT NOT NULL DEFAULT 'custom',
                default_entitlement_days REAL NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS balance_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                year INTEGER NOT NULL,
                days REAL NOT NULL,
                reason TEXT NOT NULL,
                created_by INTEGER REFERENCES users(id),
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS public_holidays (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                holiday_date TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                employee_id INTEGER REFERENCES employees(id) ON DELETE SET NULL,
                leave_request_id INTEGER REFERENCES leave_requests(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                details TEXT NOT NULL,
                before_value TEXT,
                after_value TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS off_in_lieu_credits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                public_holiday_name TEXT NOT NULL,
                public_holiday_date TEXT NOT NULL,
                credit_date TEXT NOT NULL,
                credit_amount_days REAL NOT NULL DEFAULT 1,
                used_amount_days REAL NOT NULL DEFAULT 0,
                remaining_amount_days REAL NOT NULL DEFAULT 1,
                expiry_date TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'active',
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS off_in_lieu_usages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                credit_id INTEGER NOT NULL REFERENCES off_in_lieu_credits(id) ON DELETE CASCADE,
                leave_request_id INTEGER NOT NULL REFERENCES leave_requests(id) ON DELETE CASCADE,
                amount_days REAL NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                role TEXT,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                link TEXT,
                dedupe_key TEXT UNIQUE,
                read_at TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(employees)").fetchall()]
        if "mom_eligibility_override" not in columns:
            conn.execute("ALTER TABLE employees ADD COLUMN mom_eligibility_override INTEGER NOT NULL DEFAULT 0")
        if "work_pattern" not in columns:
            conn.execute("ALTER TABLE employees ADD COLUMN work_pattern TEXT NOT NULL DEFAULT 'five_day'")
        if "custom_work_days" not in columns:
            conn.execute("ALTER TABLE employees ADD COLUMN custom_work_days TEXT")
        if "job_title" not in columns:
            conn.execute("ALTER TABLE employees ADD COLUMN job_title TEXT")
        if "approver_user_id" not in columns:
            conn.execute("ALTER TABLE employees ADD COLUMN approver_user_id INTEGER REFERENCES users(id)")
        user_sql = conn.execute("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'users'").fetchone()["sql"]
        if "'manager'" not in user_sql:
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(
                """
                CREATE TABLE users_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('employee', 'admin', 'manager')),
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO users_new (id, employee_id, email, password_hash, role, created_at)
                SELECT id, employee_id, email, password_hash, role, created_at FROM users;
                DROP TABLE users;
                ALTER TABLE users_new RENAME TO users;
                """
            )
            conn.execute("PRAGMA foreign_keys = ON")
        user_columns = [row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "active" not in user_columns:
            conn.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        leave_columns = [row["name"] for row in conn.execute("PRAGMA table_info(leave_requests)").fetchall()]
        if "approver_user_id" not in leave_columns:
            conn.execute("ALTER TABLE leave_requests ADD COLUMN approver_user_id INTEGER REFERENCES users(id)")
        audit_columns = [row["name"] for row in conn.execute("PRAGMA table_info(audit_logs)").fetchall()]
        if "before_value" not in audit_columns:
            conn.execute("ALTER TABLE audit_logs ADD COLUMN before_value TEXT")
        if "after_value" not in audit_columns:
            conn.execute("ALTER TABLE audit_logs ADD COLUMN after_value TEXT")
        if "notes" not in audit_columns:
            conn.execute("ALTER TABLE audit_logs ADD COLUMN notes TEXT")
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('enforce_mom_three_month_rule', 'yes')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('allow_company_override', 'yes')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('saturday_ph_compensation_method', 'off_in_lieu')"
        )
        conn.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES ('off_in_lieu_expiry_months', '12')"
        )
        for holiday_date, name in DEFAULT_SG_PUBLIC_HOLIDAYS_2026:
            conn.execute(
                "INSERT OR IGNORE INTO public_holidays (holiday_date, name) VALUES (?, ?)",
                (holiday_date, name),
            )
        for name, enabled, deducts_balance, balance_kind, default_entitlement in DEFAULT_LEAVE_TYPES:
            conn.execute(
                """
                INSERT OR IGNORE INTO leave_types (
                    name, enabled, deducts_balance, balance_kind, default_entitlement_days
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (name, enabled, deducts_balance, balance_kind, default_entitlement),
            )


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
        expected = hash_password(password, bytes.fromhex(salt_hex)).split("$")[2]
        return secrets.compare_digest(expected, digest_hex)
    except ValueError:
        return False


def month_add(join_date: date, months: int) -> date:
    month = join_date.month - 1 + months
    year = join_date.year + month // 12
    month = month % 12 + 1
    day = min(join_date.day, [31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)


def first_admin_needed() -> bool:
    with db() as conn:
        return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def is_admin(user) -> bool:
    return bool(user and user["role"] == "admin")


def is_manager(user) -> bool:
    return bool(user and user["role"] in ("manager", "admin"))


def app_setting(conn, key: str, default: str = "yes") -> str:
    row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def public_holiday_dates(conn, start_date: date, end_date: date) -> set[date]:
    rows = conn.execute(
        "SELECT holiday_date FROM public_holidays WHERE holiday_date BETWEEN ? AND ?",
        (start_date.isoformat(), end_date.isoformat()),
    ).fetchall()
    return {parse_date(row["holiday_date"]) for row in rows}


def public_holidays_for_year(conn, year: int):
    return conn.execute(
        "SELECT * FROM public_holidays WHERE substr(holiday_date, 1, 4) = ? ORDER BY holiday_date",
        (str(year),),
    ).fetchall()


def canonical_leave_type(conn, leave_type: str) -> str | None:
    row = conn.execute("SELECT name FROM leave_types WHERE lower(name) = lower(?)", (leave_type,)).fetchone()
    return row["name"] if row else None


def leave_type_config(conn, leave_type: str):
    canonical = canonical_leave_type(conn, leave_type)
    if not canonical:
        return None
    return conn.execute("SELECT * FROM leave_types WHERE name = ?", (canonical,)).fetchone()


def enabled_leave_types(conn):
    return conn.execute("SELECT * FROM leave_types WHERE enabled = 1 ORDER BY name").fetchall()


def leave_type_sum(conn, employee_id: int, leave_type: str, status: str, year: int | None = None) -> float:
    canonical = canonical_leave_type(conn, leave_type) or leave_type
    aliases = {canonical}
    if canonical == "Annual Leave":
        aliases.add("Annual leave")
    placeholders = ",".join("?" for _ in aliases)
    params = [employee_id, status, *aliases]
    year_filter = ""
    if year is not None:
        year_filter = " AND substr(start_date, 1, 4) = ?"
        params.append(str(year))
    return float(
        conn.execute(
            f"""
            SELECT COALESCE(SUM(days), 0) FROM leave_requests
            WHERE employee_id = ? AND status = ? AND leave_type IN ({placeholders}){year_filter}
            """,
            params,
        ).fetchone()[0]
    )


def custom_leave_type_balance(conn, employee_id: int, leave_type: str, year: int):
    config = leave_type_config(conn, leave_type)
    entitlement = float(config["default_entitlement_days"] if config else 0)
    approved = leave_type_sum(conn, employee_id, leave_type, "approved", year)
    pending = leave_type_sum(conn, employee_id, leave_type, "pending", year)
    return {
        "entitlement": entitlement,
        "approved": approved,
        "pending": pending,
        "balance": round(entitlement - approved, 2),
        "available": max(0.0, round(entitlement - approved - pending, 2)),
    }


def managed_leave_type_balance(conn, employee_id: int, leave_type: str, year: int):
    config = leave_type_config(conn, leave_type)
    if not config or not config["deducts_balance"]:
        return {"entitlement": 0.0, "approved": 0.0, "pending": 0.0, "balance": 0.0, "available": float("inf")}
    if config["balance_kind"] == "annual":
        balance = employee_balance(conn, employee_id, year)
        return {
            "entitlement": balance["earned"],
            "approved": balance["approved"],
            "pending": balance["pending"],
            "balance": balance["balance"],
            "available": max(0.0, round(balance["balance"] - balance["pending"], 2)),
        }
    if config["balance_kind"] == "off_in_lieu":
        balance = off_in_lieu_balance(conn, employee_id)
        pending = pending_off_in_lieu_days(conn, employee_id)
        return {
            "entitlement": balance,
            "approved": 0.0,
            "pending": pending,
            "balance": balance,
            "available": max(0.0, round(balance - pending, 2)),
        }
    return custom_leave_type_balance(conn, employee_id, leave_type, year)


def safe_upload_name(original_name: str) -> str:
    suffix = Path(original_name).suffix.lower()
    stem = Path(original_name).stem
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip(".-")[:80] or "attachment"
    return f"{secrets.token_hex(12)}-{cleaned}{suffix}"


def detect_allowed_attachment_type(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_ATTACHMENT_TYPES:
        raise ValueError("Attachment must be a PDF, JPG, or PNG file.")
    if suffix == ".pdf" and not content.startswith(b"%PDF-"):
        raise ValueError("Attachment file does not look like a valid PDF.")
    if suffix in {".jpg", ".jpeg"} and not content.startswith(b"\xff\xd8\xff"):
        raise ValueError("Attachment file does not look like a valid JPG.")
    if suffix == ".png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Attachment file does not look like a valid PNG.")
    return ALLOWED_ATTACHMENT_TYPES[suffix]


def validate_and_store_attachment(attachment, leave_type: str) -> str | None:
    required = leave_type in ATTACHMENT_REQUIRED_LEAVE_TYPES
    if attachment is None:
        if required:
            raise ValueError(f"Supporting document is required for {leave_type}.")
        return None

    original_name = Path(getattr(attachment, "filename", "")).name
    if not original_name:
        if required:
            raise ValueError(f"Supporting document is required for {leave_type}.")
        return None

    content = attachment.file.read(MAX_ATTACHMENT_BYTES + 1)
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise ValueError("Attachment must be 5MB or smaller.")
    if not content:
        if required:
            raise ValueError(f"Supporting document is required for {leave_type}.")
        return None
    detect_allowed_attachment_type(original_name, content)

    safe_name = safe_upload_name(original_name)
    target = UPLOAD_DIR / safe_name
    target.parent.mkdir(exist_ok=True)
    with target.open("wb") as f:
        f.write(content)
    return safe_name


def audit_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, sqlite3.Row):
        value = dict(value)
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def row_snapshot(row, fields: list[str]) -> dict:
    if not row:
        return {}
    return {field: row[field] for field in fields if field in row.keys()}


def changed_values(before: dict, after: dict) -> tuple[dict, dict]:
    keys = sorted(set(before) | set(after))
    before_changed = {}
    after_changed = {}
    for key in keys:
        if str(before.get(key, "")) != str(after.get(key, "")):
            before_changed[key] = before.get(key, "")
            after_changed[key] = after.get(key, "")
    return before_changed, after_changed


def add_audit_log(
    conn,
    actor_user_id: int | None,
    action: str,
    details: str,
    employee_id: int | None = None,
    leave_request_id: int | None = None,
    before_value=None,
    after_value=None,
    notes: str | None = None,
):
    conn.execute(
        """
        INSERT INTO audit_logs (
            actor_user_id, employee_id, leave_request_id, action, details,
            before_value, after_value, notes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            actor_user_id,
            employee_id,
            leave_request_id,
            action,
            details,
            audit_value(before_value),
            audit_value(after_value),
            notes if notes is not None else details,
        ),
    )


def add_notification(conn, title: str, message: str, user_id: int | None = None, role: str | None = None, link: str | None = None, dedupe_key: str | None = None):
    if dedupe_key:
        conn.execute(
            """
            INSERT OR IGNORE INTO notifications (user_id, role, title, message, link, dedupe_key)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, role, title, message, link, dedupe_key),
        )
    else:
        conn.execute(
            """
            INSERT INTO notifications (user_id, role, title, message, link)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, role, title, message, link),
        )


def notify_employee(conn, employee_id: int, title: str, message: str, link: str = "/", dedupe_key: str | None = None):
    user = conn.execute("SELECT id FROM users WHERE employee_id = ? ORDER BY id LIMIT 1", (employee_id,)).fetchone()
    if user:
        add_notification(conn, title, message, user_id=user["id"], link=link, dedupe_key=dedupe_key)


def notify_admins(conn, title: str, message: str, link: str = "/admin", dedupe_key: str | None = None):
    admins = conn.execute("SELECT id FROM users WHERE role = 'admin' AND active = 1").fetchall()
    for admin in admins:
        key = f"{dedupe_key}:admin:{admin['id']}" if dedupe_key else None
        add_notification(conn, title, message, user_id=admin["id"], role="admin", link=link, dedupe_key=key)


def notify_user(conn, user_id: int | None, title: str, message: str, link: str = "/", dedupe_key: str | None = None):
    if user_id:
        add_notification(conn, title, message, user_id=user_id, link=link, dedupe_key=dedupe_key)


def employee_approver(conn, employee_id: int):
    return conn.execute(
        """
        SELECT users.id, employees.name, users.email, users.role
        FROM employees AS subject
        JOIN users ON users.id = subject.approver_user_id
        JOIN employees ON employees.id = users.employee_id
        WHERE subject.id = ? AND users.active = 1 AND users.employee_id != subject.id
        """,
        (employee_id,),
    ).fetchone()


def can_decide_request(conn, user, request, admin_override: bool = False) -> bool:
    if not request or request["employee_id"] == user["employee_id"]:
        return False
    if is_admin(user):
        return True
    return bool(request["approver_user_id"] == user["id"])


def user_notifications(conn, user_id: int, limit: int = 8):
    return conn.execute(
        """
        SELECT * FROM notifications
        WHERE user_id = ?
        ORDER BY read_at IS NOT NULL, created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()


def unread_notification_count(conn, user_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,)).fetchone()[0])


def notification_panel(conn, user, title="Notifications") -> str:
    notes = user_notifications(conn, user["id"])
    rows = "".join(
        f"""<li class="{'unread' if n['read_at'] is None else ''}"><a href="{q(n,'link','#')}">{q(n,'title')}</a><span>{q(n,'message')}</span><small>{q(n,'created_at')}</small></li>"""
        for n in notes
    )
    return f"""
    <section class="panel notifications">
      <div class="section-head"><h2>{html.escape(title)}</h2><form method="post" action="/notifications/read"><button class="ghost">Mark all read</button></form></div>
      <ul>{rows or '<li><span>No notifications yet.</span></li>'}</ul>
    </section>
    """


def create_expiring_off_in_lieu_notifications(conn, days: int = 30):
    today = date.today()
    until = today + timedelta(days=days)
    credits = conn.execute(
        """
        SELECT off_in_lieu_credits.*, employees.name AS employee_name
        FROM off_in_lieu_credits
        JOIN employees ON employees.id = off_in_lieu_credits.employee_id
        WHERE off_in_lieu_credits.status = 'active'
          AND off_in_lieu_credits.remaining_amount_days > 0
          AND off_in_lieu_credits.expiry_date BETWEEN ? AND ?
        """,
        (today.isoformat(), until.isoformat()),
    ).fetchall()
    for credit in credits:
        message = f"{money_days(credit['remaining_amount_days'])} day(s) from {credit['public_holiday_name']} expire on {credit['expiry_date']}."
        notify_employee(
            conn,
            credit["employee_id"],
            "Off-in-lieu expiring soon",
            message,
            "/",
            f"oil-expiring:{credit['id']}:employee",
        )
        notify_admins(
            conn,
            "Off-in-lieu credit expiring soon",
            f"{credit['employee_name']}: {message}",
            "/admin/off-in-lieu",
            f"oil-expiring:{credit['id']}",
        )


def employee_work_days(employee) -> set[int]:
    pattern = employee["work_pattern"] if "work_pattern" in employee.keys() and employee["work_pattern"] else "five_day"
    if pattern == "five_day":
        return {0, 1, 2, 3, 4}
    if pattern in ("five_half_day", "six_day"):
        return {0, 1, 2, 3, 4, 5}
    if pattern == "custom" and employee["custom_work_days"]:
        return {int(day) for day in employee["custom_work_days"].split(",") if day != ""}
    return {0, 1, 2, 3, 4}


def work_pattern_label(value: str) -> str:
    return {
        "five_day": "5-day work week, Monday to Friday",
        "five_half_day": "5.5-day work week",
        "six_day": "6-day work week",
        "custom": "Custom work days",
    }.get(value, "5-day work week, Monday to Friday")


def leave_days_for_employee(employee, start_date: date, end_date: date, half_day: bool, holidays: set[date]) -> float:
    if end_date < start_date:
        raise ValueError("End date cannot be before start date.")
    work_days = employee_work_days(employee)
    total = 0.0
    cursor = start_date
    while cursor <= end_date:
        if cursor.weekday() in work_days and cursor not in holidays:
            total += 1
        cursor += timedelta(days=1)
    if half_day:
        if start_date != end_date:
            raise ValueError("Half-day requests must start and end on the same date.")
        return 0.5 if total > 0 else 0.0
    return total


def off_in_lieu_balance(conn, employee_id: int) -> float:
    today = date.today().isoformat()
    expired_rows = conn.execute(
        """
        SELECT id FROM off_in_lieu_credits
        WHERE employee_id = ? AND status = 'active' AND expiry_date < ? AND remaining_amount_days > 0
        """,
        (employee_id, today),
    ).fetchall()
    for row in expired_rows:
        conn.execute("UPDATE off_in_lieu_credits SET status = 'expired' WHERE id = ?", (row["id"],))
    return float(
        conn.execute(
            """
            SELECT COALESCE(SUM(remaining_amount_days), 0) FROM off_in_lieu_credits
            WHERE employee_id = ? AND status = 'active' AND expiry_date >= ?
            """,
            (employee_id, today),
        ).fetchone()[0]
    )


def pending_off_in_lieu_days(conn, employee_id: int) -> float:
    return float(
        conn.execute(
            """
            SELECT COALESCE(SUM(days), 0) FROM leave_requests
            WHERE employee_id = ? AND status = 'pending' AND leave_type = 'Off-in-lieu'
            """,
            (employee_id,),
        ).fetchone()[0]
    )


def usable_off_in_lieu_balance(conn, employee_id: int) -> float:
    return max(0.0, off_in_lieu_balance(conn, employee_id) - pending_off_in_lieu_days(conn, employee_id))


def generate_off_in_lieu_credits(conn, actor_user_id: int | None = None, employee_id: int | None = None, year: int | None = None) -> int:
    if app_setting(conn, "saturday_ph_compensation_method", "off_in_lieu") != "off_in_lieu":
        return 0
    expiry_months = int(app_setting(conn, "off_in_lieu_expiry_months", "12"))
    employees_query = "SELECT * FROM employees WHERE status = 'active'"
    params: list = []
    if employee_id:
        employees_query += " AND id = ?"
        params.append(employee_id)
    employees = conn.execute(employees_query, params).fetchall()
    holiday_query = "SELECT * FROM public_holidays"
    holiday_params: list = []
    if year:
        holiday_query += " WHERE substr(holiday_date, 1, 4) = ?"
        holiday_params.append(str(year))
    holidays = conn.execute(holiday_query, holiday_params).fetchall()
    created = 0
    for employee in employees:
        work_days = employee_work_days(employee)
        if 5 in work_days:
            continue
        for holiday in holidays:
            holiday_date = parse_date(holiday["holiday_date"])
            if holiday_date.weekday() != 5:
                continue
            existing = conn.execute(
                """
                SELECT id FROM off_in_lieu_credits
                WHERE employee_id = ? AND public_holiday_date = ? AND status != 'cancelled'
                """,
                (employee["id"], holiday["holiday_date"]),
            ).fetchone()
            if existing:
                continue
            expiry = month_add(holiday_date, expiry_months)
            conn.execute(
                """
                INSERT INTO off_in_lieu_credits (
                    employee_id, public_holiday_name, public_holiday_date, credit_date,
                    credit_amount_days, used_amount_days, remaining_amount_days, expiry_date, status, notes
                ) VALUES (?, ?, ?, ?, 1, 0, 1, ?, 'active', ?)
                """,
                (
                    employee["id"],
                    holiday["name"],
                    holiday["holiday_date"],
                    holiday["holiday_date"],
                    expiry.isoformat(),
                    "Auto-generated for Saturday public holiday under company policy.",
                ),
            )
            add_audit_log(
                conn,
                actor_user_id,
                "off_in_lieu_credit_added",
                f"Credited 1 day for {employee['name']} for {holiday['name']} on {holiday['holiday_date']}.",
                employee["id"],
                None,
                before_value={},
                after_value={
                    "public_holiday_name": holiday["name"],
                    "public_holiday_date": holiday["holiday_date"],
                    "credit_amount_days": 1,
                    "remaining_amount_days": 1,
                    "expiry_date": expiry.isoformat(),
                    "status": "active",
                },
                notes="Auto-generated for Saturday public holiday under company policy.",
            )
            notify_employee(
                conn,
                employee["id"],
                "Off-in-lieu credited",
                f"1 day credited for {holiday['name']} on {holiday['holiday_date']}.",
                "/",
                f"oil-credit:{employee['id']}:{holiday['holiday_date']}",
            )
            created += 1
    return created


def consume_off_in_lieu(conn, employee_id: int, leave_request_id: int, amount_days: float):
    if off_in_lieu_balance(conn, employee_id) < amount_days:
        raise ValueError("Not enough off-in-lieu balance.")
    remaining = float(amount_days)
    credits = conn.execute(
        """
        SELECT * FROM off_in_lieu_credits
        WHERE employee_id = ? AND status = 'active' AND remaining_amount_days > 0 AND expiry_date >= ?
        ORDER BY expiry_date, public_holiday_date, id
        """,
        (employee_id, date.today().isoformat()),
    ).fetchall()
    for credit in credits:
        if remaining <= 0:
            break
        use = min(float(credit["remaining_amount_days"]), remaining)
        new_used = float(credit["used_amount_days"]) + use
        new_remaining = float(credit["remaining_amount_days"]) - use
        new_status = "used" if new_remaining <= 0 else "active"
        conn.execute(
            """
            UPDATE off_in_lieu_credits
            SET used_amount_days = ?, remaining_amount_days = ?, status = ?
            WHERE id = ?
            """,
            (new_used, round(new_remaining, 2), new_status, credit["id"]),
        )
        conn.execute(
            "INSERT INTO off_in_lieu_usages (credit_id, leave_request_id, amount_days) VALUES (?, ?, ?)",
            (credit["id"], leave_request_id, use),
        )
        remaining = round(remaining - use, 2)
    if remaining > 0:
        raise ValueError("Not enough off-in-lieu balance.")


def q(row, key, default=""):
    return html.escape(str(row[key] if row and row[key] is not None else default))


def money_days(value) -> str:
    amount = round(float(value), 2)
    return str(int(amount)) if amount.is_integer() else f"{amount:.2f}"


def employee_balance(conn, employee_id: int, year: int):
    employee = conn.execute("SELECT * FROM employees WHERE id = ?", (employee_id,)).fetchone()
    allow_override = app_setting(conn, "allow_company_override") == "yes"
    company_override = bool(employee["mom_eligibility_override"]) and allow_override
    calculation = calculate_annual_leave_entitlement(
        parse_date(employee["join_date"]),
        year,
        employee["annual_entitlement"],
        enforce_three_month_rule=app_setting(conn, "enforce_mom_three_month_rule") == "yes",
        company_override=company_override,
    )
    earned = calculation.payable_entitlement
    approved = leave_type_sum(conn, employee_id, "Annual Leave", "approved", year)
    pending = leave_type_sum(conn, employee_id, "Annual Leave", "pending", year)
    adjustments = conn.execute(
        "SELECT COALESCE(SUM(days), 0) FROM balance_adjustments WHERE employee_id = ? AND year = ?",
        (employee_id, year),
    ).fetchone()[0]
    return {
        "earned": float(earned),
        "calculation": calculation,
        "approved": float(approved),
        "pending": float(pending),
        "adjustments": float(adjustments),
        "balance": round(float(earned) + float(adjustments) - float(approved), 2),
    }


def layout(title: str, body: str, user=None) -> bytes:
    nav = ""
    if user:
        admin = '<a href="/admin">Admin</a>' if is_admin(user) else ""
        manager = '<a href="/manager">Team</a>' if is_manager(user) else ""
        with db() as conn:
            unread = unread_notification_count(conn, user["id"])
        note_label = f"Notifications ({unread})" if unread else "Notifications"
        nav = f"""
        <nav class="topbar">
          <a class="brand" href="/">LeaveDesk</a>
          <div>{admin}{manager}<a href="/">My leave</a><a href="/#notifications">{note_label}</a><a href="/logout">Logout</a></div>
        </nav>
        """
    return f"""<!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{html.escape(title)} - LeaveDesk</title>
      <link rel="stylesheet" href="/static/styles.css">
    </head>
    <body>
      {nav}
      <main class="shell">{body}</main>
    </body>
    </html>""".encode("utf-8")


def field(label, name, value="", input_type="text", required=True, extra=""):
    req = "required" if required else ""
    return f"""<label>{label}<input type="{input_type}" name="{name}" value="{html.escape(str(value))}" {req} {extra}></label>"""


def select(label, name, options, selected=""):
    items = "".join(f'<option value="{html.escape(v)}" {"selected" if v == selected else ""}>{html.escape(t)}</option>' for v, t in options)
    return f"<label>{label}<select name=\"{name}\">{items}</select></label>"


class App(BaseHTTPRequestHandler):
    def do_GET(self):
        init_db()
        path = urlparse(self.path).path
        if path == "/static/styles.css":
            self.send_file(BASE_DIR / "static" / "styles.css", "text/css")
            return
        if path == "/healthz":
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        if path.startswith("/uploads/"):
            user = self.current_user()
            if not user:
                self.redirect("/login")
                return
            self.send_upload(unquote(path.removeprefix("/uploads/")), user)
            return

        user = self.current_user()
        if first_admin_needed() and path != "/setup":
            self.redirect("/setup")
            return
        if path == "/setup":
            self.setup_page()
        elif path == "/login":
            self.login_page()
        elif path == "/logout":
            self.logout()
        elif not user:
            self.redirect("/login")
        elif path == "/":
            self.employee_dashboard(user)
        elif path == "/leave/new":
            self.leave_form(user)
        elif path.startswith("/leave/") and path.endswith("/cancel"):
            self.cancel_leave(user, int(path.split("/")[2]))
        elif path == "/manager" and is_manager(user):
            self.manager_dashboard(user)
        elif path == "/admin" and is_admin(user):
            self.admin_dashboard(user)
        elif path == "/admin/leaves" and is_admin(user):
            self.admin_leave_records(user)
        elif path == "/admin/export/employees.csv" and is_admin(user):
            self.export_employees_csv(user)
        elif path == "/admin/export/users.csv" and is_admin(user):
            self.export_users_csv(user)
        elif path == "/admin/export/leaves.csv" and is_admin(user):
            self.export_leave_records_csv(user)
        elif path == "/admin/export/balances.csv" and is_admin(user):
            self.export_leave_balances_csv(user)
        elif path == "/admin/export/off-in-lieu.csv" and is_admin(user):
            self.export_off_in_lieu_csv(user)
        elif path == "/admin/export/holidays.csv" and is_admin(user):
            self.export_public_holidays_csv(user)
        elif path == "/admin/export/audit-log.csv" and is_admin(user):
            self.export_audit_log_csv(user)
        elif path == "/admin/export/backup.zip" and is_admin(user):
            self.export_backup_zip(user)
        elif path == "/admin/settings" and is_admin(user):
            self.admin_settings(user)
        elif path == "/admin/holidays" and is_admin(user):
            self.admin_holidays(user)
        elif path == "/admin/off-in-lieu" and is_admin(user):
            self.admin_off_in_lieu(user)
        elif path == "/admin/leave-types" and is_admin(user):
            self.admin_leave_types(user)
        elif path == "/admin/employees/new" and is_admin(user):
            self.employee_form(user)
        elif path.startswith("/admin/employees/") and path.endswith("/edit") and is_admin(user):
            self.employee_form(user, int(path.split("/")[3]))
        elif path == "/admin/seed" and is_admin(user):
            self.seed_page(user)
        else:
            self.not_found(user)

    def do_POST(self):
        init_db()
        path = urlparse(self.path).path
        if path == "/setup":
            self.create_admin()
            return
        if path == "/login":
            self.login()
            return

        user = self.current_user()
        if not user:
            self.redirect("/login")
        elif path == "/leave/new":
            self.create_leave(user)
        elif path == "/notifications/read":
            self.mark_notifications_read(user)
        elif path.startswith("/leave/") and path.endswith("/cancel"):
            self.cancel_leave(user, int(path.split("/")[2]))
        elif path == "/admin/employees/new" and is_admin(user):
            self.save_employee(user)
        elif path.startswith("/admin/employees/") and path.endswith("/edit") and is_admin(user):
            self.save_employee(user, int(path.split("/")[3]))
        elif path.startswith("/admin/requests/") and is_admin(user):
            parts = path.split("/")
            self.decide_leave(user, int(parts[3]), parts[4], admin_override=True)
        elif path.startswith("/manager/requests/") and is_manager(user):
            parts = path.split("/")
            self.decide_leave(user, int(parts[3]), parts[4], admin_override=False)
        elif path == "/admin/adjustments" and is_admin(user):
            self.add_adjustment(user)
        elif path == "/admin/settings" and is_admin(user):
            self.save_admin_settings(user)
        elif path == "/admin/holidays" and is_admin(user):
            self.save_public_holiday(user)
        elif path.startswith("/admin/holidays/") and path.endswith("/delete") and is_admin(user):
            self.delete_public_holiday(user, int(path.split("/")[3]))
        elif path == "/admin/off-in-lieu/generate" and is_admin(user):
            self.generate_off_in_lieu(user)
        elif path == "/admin/off-in-lieu" and is_admin(user):
            self.add_off_in_lieu_credit(user)
        elif path.startswith("/admin/off-in-lieu/") and path.endswith("/edit") and is_admin(user):
            self.edit_off_in_lieu_credit(user, int(path.split("/")[3]))
        elif path.startswith("/admin/off-in-lieu/") and path.endswith("/expire") and is_admin(user):
            self.set_off_in_lieu_status(user, int(path.split("/")[3]), "expired")
        elif path.startswith("/admin/off-in-lieu/") and path.endswith("/delete") and is_admin(user):
            self.set_off_in_lieu_status(user, int(path.split("/")[3]), "cancelled")
        elif path == "/admin/leave-types" and is_admin(user):
            self.save_leave_types(user)
        elif path == "/admin/seed" and is_admin(user):
            self.seed_data(user)
        elif path == "/admin/seed/delete" and is_admin(user):
            self.delete_seed_data(user)
        else:
            self.not_found(user)

    def current_user(self):
        raw = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie(raw)
        if "session" not in jar:
            return None
        token = jar["session"].value
        with db() as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at < ?", (datetime.now().isoformat(),))
            return conn.execute(
                """
                SELECT users.*, employees.name, employees.status FROM sessions
                JOIN users ON users.id = sessions.user_id
                JOIN employees ON employees.id = users.employee_id
                WHERE token = ? AND users.active = 1 AND employees.status != 'inactive'
                """,
                (token,),
            ).fetchone()

    def read_form(self):
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("multipart/form-data"):
            form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST"})
            data = {key: form.getvalue(key) for key in form.keys() if key != "attachment"}
            attachment = form["attachment"] if "attachment" in form and getattr(form["attachment"], "filename", "") else None
            return data, attachment
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode("utf-8")
        data = {k: v[0] for k, v in parse_qs(raw).items()}
        return data, None

    def send_html(self, title, body, user=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(layout(title, body, user))

    def send_file(self, path: Path, content_type: str):
        if not path.exists() or not path.resolve().is_relative_to(BASE_DIR):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def send_upload(self, filename: str, user):
        path = (UPLOAD_DIR / Path(filename).name).resolve()
        if not path.exists() or not path.is_relative_to(UPLOAD_DIR.resolve()):
            self.send_error(404)
            return
        with db() as conn:
            request = conn.execute("SELECT * FROM leave_requests WHERE attachment_path = ?", (path.name,)).fetchone()
        if not request or not (is_admin(user) or request["employee_id"] == user["employee_id"] or request["approver_user_id"] == user["id"]):
            self.send_error(403)
            return
        content_type = ALLOWED_ATTACHMENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'inline; filename="{path.name}"')
        self.end_headers()
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def send_csv(self, filename: str, headers: list[str], rows):
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        data = output.getvalue().encode("utf-8-sig")
        self.send_download(filename, "text/csv; charset=utf-8", data)

    def csv_bytes(self, headers: list[str], rows) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers)
        writer.writerows(rows)
        return output.getvalue().encode("utf-8-sig")

    def send_download(self, filename: str, content_type: str, data: bytes):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, location):
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def mark_notifications_read(self, user):
        with db() as conn:
            conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (datetime.now().isoformat(), user["id"]))
        referer = self.headers.get("Referer", "/")
        parsed = urlparse(referer)
        target = parsed.path if parsed.netloc in ("", self.headers.get("Host", "")) else "/"
        self.redirect(target or "/")

    def setup_page(self):
        if not first_admin_needed():
            self.redirect("/")
            return
        body = f"""
        <section class="auth">
          <h1>Create First Admin Account</h1>
          <p>Set up the first HR/admin user for this company.</p>
          <form method="post" class="panel">
            {field("Name", "name")}
            {field("Email", "email", input_type="email")}
            <input type="hidden" name="department" value="HR">
            <input type="hidden" name="join_date" value="{date.today().isoformat()}">
            {field("Password", "password", input_type="password", extra="minlength='8'")}
            {field("Confirm password", "confirm_password", input_type="password", extra="minlength='8'")}
            <button>Create admin</button>
          </form>
        </section>"""
        self.send_html("Setup", body)

    def create_admin(self):
        if not first_admin_needed():
            self.redirect("/")
            return
        data, _ = self.read_form()
        if data["password"] != data.get("confirm_password"):
            self.send_html("Setup", '<section class="auth"><h1>Create First Admin Account</h1><p class="error">Passwords do not match.</p><p><a href="/setup">Back to setup</a></p></section>', None, 400)
            return
        join = parse_date(data["join_date"])
        with db() as conn:
            emp_id = conn.execute(
                """
                INSERT INTO employees (name, email, join_date, department, annual_entitlement, probation_end_date, status)
                VALUES (?, ?, ?, ?, 14, ?, 'active')
                """,
                (data["name"], data["email"].lower(), join.isoformat(), data["department"], month_add(join, 3).isoformat()),
            ).lastrowid
            user_id = conn.execute(
                "INSERT INTO users (employee_id, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
                (emp_id, data["email"].lower(), hash_password(data["password"])),
            ).lastrowid
            created = conn.execute(
                "SELECT employees.*, users.role FROM employees JOIN users ON users.employee_id = employees.id WHERE employees.id = ?",
                (emp_id,),
            ).fetchone()
            add_audit_log(
                conn,
                user_id,
                "employee_created",
                f"{data['email'].lower()} created the first admin account.",
                emp_id,
                before_value={},
                after_value=row_snapshot(
                    created,
                    [
                        "name",
                        "email",
                        "department",
                        "join_date",
                        "annual_entitlement",
                        "probation_end_date",
                        "status",
                        "role",
                    ],
                ),
                notes="First admin setup.",
            )
        self.redirect("/login")

    def login_page(self, message=""):
        note = f'<p class="error">{html.escape(message)}</p>' if message else ""
        body = f"""
        <section class="auth">
          <h1>Sign in</h1>
          {note}
          <form method="post" class="panel">
            {field("Email", "email", input_type="email")}
            {field("Password", "password", input_type="password")}
            <button>Sign in</button>
          </form>
        </section>"""
        self.send_html("Login", body)

    def login(self):
        data, _ = self.read_form()
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE email = ?", (data["email"].lower(),)).fetchone()
        if not user or not verify_password(data["password"], user["password_hash"]):
            self.login_page("Email or password is incorrect.")
            return
        self.make_session(user["id"])

    def make_session(self, user_id):
        token = secrets.token_urlsafe(32)
        expires = (datetime.now() + timedelta(days=SESSION_DAYS)).isoformat()
        with db() as conn:
            conn.execute("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)", (token, user_id, expires))
        self.send_response(303)
        self.send_header("Set-Cookie", f"session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={SESSION_DAYS * 86400}")
        self.send_header("Location", "/")
        self.end_headers()

    def logout(self):
        raw = self.headers.get("Cookie", "")
        jar = cookies.SimpleCookie(raw)
        if "session" in jar:
            with db() as conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (jar["session"].value,))
        self.send_response(303)
        self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
        self.send_header("Location", "/login")
        self.end_headers()

    def employee_dashboard(self, user):
        year = date.today().year
        with db() as conn:
            create_expiring_off_in_lieu_notifications(conn)
            employee = conn.execute("SELECT * FROM employees WHERE id = ?", (user["employee_id"],)).fetchone()
            approver = employee_approver(conn, employee["id"])
            balance = employee_balance(conn, employee["id"], year)
            oil_balance = off_in_lieu_balance(conn, employee["id"])
            requests = conn.execute(
                "SELECT * FROM leave_requests WHERE employee_id = ? ORDER BY created_at DESC",
                (employee["id"],),
            ).fetchall()
            oil_credits = conn.execute(
                """
                SELECT * FROM off_in_lieu_credits
                WHERE employee_id = ? AND status = 'active' AND remaining_amount_days > 0
                ORDER BY expiry_date, public_holiday_date
                """,
                (employee["id"],),
            ).fetchall()
            leave_type_rows = enabled_leave_types(conn)
            leave_type_balances = [
                (row["name"], managed_leave_type_balance(conn, employee["id"], row["name"], year), row["deducts_balance"])
                for row in leave_type_rows
            ]
            notifications_html = notification_panel(conn, user)
        rows = "".join(
            f"""<tr><td>{q(r,'leave_type')}</td><td>{q(r,'start_date')} to {q(r,'end_date')}</td><td>{money_days(r['days'])}</td><td><span class="status {q(r,'status')}">{q(r,'status')}</span></td><td>{q(r,'reason')}</td><td>{'<form method="post" action="/leave/' + str(r['id']) + '/cancel"><button class="ghost">Cancel</button></form>' if r['status'] == 'pending' else ''}</td></tr>"""
            for r in requests
        )
        oil_rows = "".join(
            f"""<tr><td>{q(c,'public_holiday_name')}</td><td>{q(c,'public_holiday_date')}</td><td>{money_days(c['remaining_amount_days'])}</td><td>{q(c,'expiry_date')}</td></tr>"""
            for c in oil_credits
        )
        leave_balance_rows = "".join(
            f"""<tr><td>{html.escape(name)}</td><td>{'Yes' if deducts else 'No'}</td><td>{money_days(data['balance']) if deducts else 'Not deducted'}</td><td>{money_days(data['pending']) if deducts else 'Shown in history'}</td></tr>"""
            for name, data, deducts in leave_type_balances
        )
        body = f"""
        <header class="page-head"><div><h1>My leave</h1><p>{q(employee,'department')} - Joined {q(employee,'join_date')} - Approver: {html.escape(approver['name'] if approver else 'Admin / HR')}</p></div><a class="button" href="/leave/new">Request leave</a></header>
        <div id="notifications">{notifications_html}</div>
        <section class="stats">
          <div><b>{money_days(balance['earned'])}</b><span>Earned {year}</span></div>
          <div><b>{money_days(balance['approved'])}</b><span>Approved taken</span></div>
          <div><b>{money_days(balance['pending'])}</b><span>Pending</span></div>
          <div><b>{money_days(balance['balance'])}</b><span>Balance</span></div>
          <div><b>{money_days(oil_balance)}</b><span>Off-in-lieu balance</span></div>
        </section>
        <section class="panel">
          <h2>Off-in-lieu credits</h2>
          <div class="table-wrap"><table><thead><tr><th>Public holiday</th><th>Date</th><th>Remaining</th><th>Expires</th></tr></thead><tbody>{oil_rows or '<tr><td colspan="4">No active off-in-lieu credits.</td></tr>'}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Leave type balances</h2>
          <div class="table-wrap"><table><thead><tr><th>Leave type</th><th>Deducts balance</th><th>Balance</th><th>Pending</th></tr></thead><tbody>{leave_balance_rows}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Leave history</h2>
          <div class="table-wrap"><table><thead><tr><th>Type</th><th>Dates</th><th>Days</th><th>Status</th><th>Reason</th><th></th></tr></thead><tbody>{rows or '<tr><td colspan="6">No leave requests yet.</td></tr>'}</tbody></table></div>
        </section>"""
        self.send_html("My leave", body, user)

    def leave_form(self, user):
        current_year = date.today().year
        with db() as conn:
            employee = conn.execute("SELECT * FROM employees WHERE id = ?", (user["employee_id"],)).fetchone()
            oil_available = usable_off_in_lieu_balance(conn, user["employee_id"])
            leave_types = enabled_leave_types(conn)
            holiday_rows = conn.execute(
                "SELECT holiday_date, name FROM public_holidays WHERE substr(holiday_date, 1, 4) IN (?, ?) ORDER BY holiday_date",
                (str(current_year), str(current_year + 1)),
            ).fetchall()
            type_balances = {
                row["name"]: managed_leave_type_balance(conn, user["employee_id"], row["name"], current_year)
                for row in leave_types
            }
        holiday_js = json.dumps({row["holiday_date"]: row["name"] for row in holiday_rows})
        leave_type_options = [(row["name"], row["name"]) for row in leave_types]
        type_balance_js = json.dumps({name: data["available"] for name, data in type_balances.items() if data["available"] != float("inf")})
        work_days_js = json.dumps(sorted(employee_work_days(employee)))
        body = f"""
        <header class="page-head"><h1>Request leave</h1></header>
        <form method="post" enctype="multipart/form-data" class="panel form-grid">
          {select("Leave type", "leave_type", leave_type_options)}
          {field("Start date", "start_date", input_type="date")}
          {field("End date", "end_date", input_type="date")}
          <label class="check"><input type="checkbox" name="half_day" value="1"> Half day</label>
          <div class="wide leave-preview">
            <b id="chargeable-days">0 days</b>
            <span>Chargeable leave days, excluding non-working days and Singapore public holidays.</span>
            <span id="oil-balance-note">Available off-in-lieu balance: {money_days(oil_available)} days.</span>
            <small id="holiday-note"></small>
          </div>
          <label class="wide">Reason<textarea name="reason" required></textarea></label>
          <label class="wide">Attachment<input type="file" name="attachment" accept=".pdf,.jpg,.jpeg,.png"><small>PDF, JPG, or PNG only. Maximum 5MB. Required for Medical Leave and Hospitalisation Leave.</small></label>
          <button>Submit request</button>
        </form>
        <script>
          const publicHolidays = {holiday_js};
          const startInput = document.querySelector('input[name="start_date"]');
          const endInput = document.querySelector('input[name="end_date"]');
          const halfInput = document.querySelector('input[name="half_day"]');
          const leaveTypeInput = document.querySelector('select[name="leave_type"]');
          const output = document.getElementById('chargeable-days');
          const note = document.getElementById('holiday-note');
          const oilBalance = {float(oil_available)};
          const typeBalances = {type_balance_js};
          const workDays = new Set({work_days_js});

          function dateParts(value) {{
            const parts = value.split('-').map(Number);
            return new Date(parts[0], parts[1] - 1, parts[2]);
          }}

          function dateKey(value) {{
            const year = value.getFullYear();
            const month = String(value.getMonth() + 1).padStart(2, '0');
            const day = String(value.getDate()).padStart(2, '0');
            return `${{year}}-${{month}}-${{day}}`;
          }}

          function updateChargeableDays() {{
            if (!startInput.value || !endInput.value) {{
              output.textContent = '0 days';
              note.textContent = '';
              return;
            }}
            const start = dateParts(startInput.value);
            const end = dateParts(endInput.value);
            if (end < start) {{
              output.textContent = 'Invalid date range';
              note.textContent = '';
              return;
            }}
            let days = 0;
            const holidays = [];
            for (let cursor = new Date(start); cursor <= end; cursor.setDate(cursor.getDate() + 1)) {{
              const day = cursor.getDay();
              const key = dateKey(cursor);
              if (publicHolidays[key]) {{
                holidays.push(`${{key}} ${{publicHolidays[key]}}`);
              }}
              const appDay = day === 0 ? 6 : day - 1;
              if (workDays.has(appDay) && !publicHolidays[key]) {{
                days += 1;
              }}
            }}
            if (halfInput.checked) {{
              days = startInput.value === endInput.value && days > 0 ? 0.5 : days;
            }}
            output.textContent = `${{days}} ${{days === 1 ? 'day' : 'days'}}`;
            if (Object.prototype.hasOwnProperty.call(typeBalances, leaveTypeInput.value) && days > typeBalances[leaveTypeInput.value]) {{
              note.textContent = `Not enough ${{leaveTypeInput.value}} balance. Available: ${{typeBalances[leaveTypeInput.value]}} days.`;
            }} else {{
              note.textContent = holidays.length ? `Public holidays in range: ${{holidays.join(', ')}}` : '';
            }}
          }}

          [startInput, endInput, halfInput, leaveTypeInput].forEach((input) => input.addEventListener('input', updateChargeableDays));
          [startInput, endInput, halfInput, leaveTypeInput].forEach((input) => input.addEventListener('change', updateChargeableDays));
          updateChargeableDays();
        </script>"""
        self.send_html("Request leave", body, user)

    def create_leave(self, user):
        data, attachment = self.read_form()
        start = parse_date(data["start_date"])
        end = parse_date(data["end_date"])
        half_day = data.get("half_day") == "1"
        try:
            with db() as conn:
                config = leave_type_config(conn, data["leave_type"])
                if not config or not config["enabled"]:
                    raise ValueError("This leave type is not enabled.")
                holidays = public_holiday_dates(conn, start, end)
                employee = conn.execute("SELECT * FROM employees WHERE id = ?", (user["employee_id"],)).fetchone()
            days = leave_days_for_employee(employee, start, end, half_day, holidays)
            if config["deducts_balance"]:
                with db() as conn:
                    available = managed_leave_type_balance(conn, user["employee_id"], config["name"], start.year)["available"]
                if days <= 0:
                    raise ValueError(f"{config['name']} request must include at least one chargeable working day.")
                if days > available:
                    with db() as notify_conn:
                        notify_admins(
                            notify_conn,
                            "Insufficient balance attempt",
                            f"{user['name']} tried to request {money_days(days)} days of {config['name']} with {money_days(available)} days available.",
                            "/admin",
                        )
                    raise ValueError(f"Not enough {config['name']} balance. Available balance is {money_days(available)} days.")
        except ValueError as exc:
            self.send_html("Request leave", f'<p class="error">{html.escape(str(exc))}</p><p><a href="/leave/new">Back</a></p>', user, 400)
            return
        try:
            attachment_path = validate_and_store_attachment(attachment, config["name"])
        except ValueError as exc:
            self.send_html("Request leave", f'<p class="error">{html.escape(str(exc))}</p><p><a href="/leave/new">Back</a></p>', user, 400)
            return
        with db() as conn:
            approver = employee_approver(conn, user["employee_id"])
            approver_user_id = approver["id"] if approver else None
            request_id = conn.execute(
                """
                INSERT INTO leave_requests (employee_id, leave_type, start_date, end_date, half_day, days, reason, attachment_path, approver_user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (user["employee_id"], config["name"], start.isoformat(), end.isoformat(), int(half_day), days, data["reason"], attachment_path, approver_user_id),
            ).lastrowid
            add_audit_log(
                conn,
                user["id"],
                "leave_request_submitted",
                f"{user['name']} submitted {config['name']} from {start.isoformat()} to {end.isoformat()} ({money_days(days)} days).",
                user["employee_id"],
                request_id,
                before_value={},
                after_value={
                    "leave_type": config["name"],
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "half_day": half_day,
                    "days": days,
                    "status": "pending",
                    "approver_user_id": approver_user_id or "",
                    "reason": data["reason"],
                    "attachment_path": attachment_path or "",
                },
                notes=data["reason"],
            )
            notify_employee(
                conn,
                user["employee_id"],
                "Leave submitted",
                f"Your {config['name']} request for {money_days(days)} day(s) is pending approval.",
                "/",
            )
            if approver_user_id:
                notify_user(conn, approver_user_id, "New leave request submitted", f"{user['name']} submitted {config['name']} from {start.isoformat()} to {end.isoformat()} ({money_days(days)} days).", "/manager")
            else:
                notify_admins(
                    conn,
                    "New leave request submitted",
                    f"{user['name']} submitted {config['name']} from {start.isoformat()} to {end.isoformat()} ({money_days(days)} days).",
                    "/admin",
                )
        self.redirect("/")

    def cancel_leave(self, user, request_id):
        with db() as conn:
            request = conn.execute(
                "SELECT * FROM leave_requests WHERE id = ? AND employee_id = ? AND status = 'pending'",
                (request_id, user["employee_id"]),
            ).fetchone()
            conn.execute(
                "UPDATE leave_requests SET status = 'cancelled' WHERE id = ? AND employee_id = ? AND status = 'pending'",
                (request_id, user["employee_id"]),
            )
            if request:
                add_audit_log(
                    conn,
                    user["id"],
                    "leave_cancelled",
                    f"{user['name']} cancelled {request['leave_type']} from {request['start_date']} to {request['end_date']}.",
                    user["employee_id"],
                    request_id,
                    before_value={"status": request["status"]},
                    after_value={"status": "cancelled"},
                    notes=request["reason"],
                )
        self.redirect("/")

    def manager_dashboard(self, user):
        year = date.today().year
        with db() as conn:
            team = conn.execute(
                """
                SELECT * FROM employees
                WHERE approver_user_id = ? AND id != ?
                ORDER BY department, name
                """,
                (user["id"], user["employee_id"]),
            ).fetchall()
            pending = conn.execute(
                """
                SELECT leave_requests.*, employees.name, employees.department
                FROM leave_requests
                JOIN employees ON employees.id = leave_requests.employee_id
                WHERE leave_requests.approver_user_id = ?
                  AND leave_requests.status = 'pending'
                  AND leave_requests.employee_id != ?
                ORDER BY leave_requests.created_at
                """,
                (user["id"], user["employee_id"]),
            ).fetchall()
            upcoming = conn.execute(
                """
                SELECT leave_requests.*, employees.name
                FROM leave_requests
                JOIN employees ON employees.id = leave_requests.employee_id
                WHERE leave_requests.approver_user_id = ?
                  AND leave_requests.status = 'approved'
                  AND leave_requests.end_date >= ?
                ORDER BY leave_requests.start_date
                LIMIT 20
                """,
                (user["id"], date.today().isoformat()),
            ).fetchall()
            history = conn.execute(
                """
                SELECT leave_requests.*, employees.name
                FROM leave_requests
                JOIN employees ON employees.id = leave_requests.employee_id
                WHERE leave_requests.approver_user_id = ?
                ORDER BY leave_requests.created_at DESC
                LIMIT 30
                """,
                (user["id"],),
            ).fetchall()
        pending_rows = "".join(
            f"""<tr><td>{q(r,'name')}<small>{q(r,'department')}</small></td><td>{q(r,'leave_type')}</td><td>{q(r,'start_date')} to {q(r,'end_date')}</td><td>{money_days(r['days'])}</td><td>{q(r,'reason')}</td><td>{'<a href="/uploads/' + quote(r['attachment_path']) + '">File</a>' if r['attachment_path'] else ''}</td><td class="actions"><form method="post" action="/manager/requests/{r['id']}/approve"><button>Approve</button></form><form method="post" action="/manager/requests/{r['id']}/reject"><button class="danger">Reject</button></form></td></tr>"""
            for r in pending
        )
        upcoming_rows = "".join(
            f"""<tr><td>{q(r,'name')}</td><td>{q(r,'leave_type')}</td><td>{q(r,'start_date')} to {q(r,'end_date')}</td><td>{money_days(r['days'])}</td></tr>"""
            for r in upcoming
        )
        history_rows = "".join(
            f"""<tr><td>{q(r,'name')}</td><td>{q(r,'leave_type')}</td><td>{q(r,'start_date')} to {q(r,'end_date')}</td><td>{money_days(r['days'])}</td><td><span class="status {q(r,'status')}">{q(r,'status')}</span></td></tr>"""
            for r in history
        )
        body = f"""
        <header class="page-head"><div><h1>Team approvals</h1><p>{len(team)} assigned employee(s) for {year}.</p></div></header>
        <section class="stats">
          <div><b>{len(team)}</b><span>Assigned employees</span></div>
          <div><b>{len(pending)}</b><span>Pending approvals</span></div>
          <div><b>{len(upcoming)}</b><span>Upcoming approved leave</span></div>
        </section>
        <section class="panel">
          <h2>Pending requests</h2>
          <div class="table-wrap"><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Reason</th><th>Attachment</th><th></th></tr></thead><tbody>{pending_rows or '<tr><td colspan="7">No pending requests for your team.</td></tr>'}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Team leave calendar</h2>
          <div class="table-wrap"><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th></tr></thead><tbody>{upcoming_rows or '<tr><td colspan="4">No upcoming approved leave.</td></tr>'}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Team leave history</h2>
          <div class="table-wrap"><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Status</th></tr></thead><tbody>{history_rows or '<tr><td colspan="5">No team leave history yet.</td></tr>'}</tbody></table></div>
        </section>"""
        self.send_html("Team approvals", body, user)

    def admin_dashboard(self, user):
        year = date.today().year
        with db() as conn:
            create_expiring_off_in_lieu_notifications(conn)
            employees = conn.execute("SELECT * FROM employees ORDER BY status, name").fetchall()
            settings = {
                "enforce_mom_three_month_rule": app_setting(conn, "enforce_mom_three_month_rule"),
                "allow_company_override": app_setting(conn, "allow_company_override"),
            }
            pending = conn.execute(
                """
                SELECT leave_requests.*, employees.name, employees.department, approver_employee.name AS approver_name FROM leave_requests
                JOIN employees ON employees.id = leave_requests.employee_id
                LEFT JOIN users AS approver_user ON approver_user.id = leave_requests.approver_user_id
                LEFT JOIN employees AS approver_employee ON approver_employee.id = approver_user.employee_id
                WHERE leave_requests.status = 'pending'
                ORDER BY leave_requests.created_at
                """
            ).fetchall()
            balances = {e["id"]: employee_balance(conn, e["id"], year) for e in employees}
            oil_balances = {e["id"]: off_in_lieu_balance(conn, e["id"]) for e in employees}
            pending_oil = {e["id"]: pending_off_in_lieu_days(conn, e["id"]) for e in employees}
            expiring_oil = conn.execute(
                """
                SELECT off_in_lieu_credits.*, employees.name AS employee_name
                FROM off_in_lieu_credits
                JOIN employees ON employees.id = off_in_lieu_credits.employee_id
                WHERE off_in_lieu_credits.status = 'active'
                  AND off_in_lieu_credits.remaining_amount_days > 0
                  AND off_in_lieu_credits.expiry_date BETWEEN ? AND ?
                ORDER BY off_in_lieu_credits.expiry_date
                LIMIT 10
                """,
                (date.today().isoformat(), (date.today() + timedelta(days=90)).isoformat()),
            ).fetchall()
            audit_logs = conn.execute(
                """
                SELECT audit_logs.*, users.email AS actor_email, employees.name AS employee_name
                FROM audit_logs
                LEFT JOIN users ON users.id = audit_logs.actor_user_id
                LEFT JOIN employees ON employees.id = audit_logs.employee_id
                ORDER BY audit_logs.created_at DESC
                LIMIT 12
                """
            ).fetchall()
            notifications_html = notification_panel(conn, user, "Admin notifications")
        pending_by_approver = {}
        for request in pending:
            key = request["approver_name"] or "Admin / HR"
            pending_by_approver[key] = pending_by_approver.get(key, 0) + 1
        pending_by_approver_text = ", ".join(f"{name}: {count}" for name, count in pending_by_approver.items()) or "None"
        emp_rows = "".join(
            f"""<tr><td>{q(e,'name')}<small>{q(e,'email')}</small></td><td>{q(e,'department')}</td><td>{q(e,'join_date')}</td><td>{money_days(balances[e['id']]['balance'])}</td><td>{money_days(balances[e['id']]['pending'])}</td><td>{money_days(oil_balances[e['id']])}</td><td>{money_days(pending_oil[e['id']])}</td><td><span class="status {q(e,'status')}">{q(e,'status')}</span></td><td><a href="/admin/employees/{e['id']}/edit">Edit</a></td></tr>"""
            for e in employees
        )
        pending_rows = "".join(
            f"""<tr><td>{q(r,'name')}<small>{q(r,'department')}</small></td><td>{q(r,'leave_type')}</td><td>{q(r,'start_date')} to {q(r,'end_date')}</td><td>{money_days(r['days'])}</td><td>{q(r,'approver_name','Admin / HR')}</td><td>{q(r,'reason')}</td><td>{'<a href="/uploads/' + quote(r['attachment_path']) + '">File</a>' if r['attachment_path'] else ''}</td><td class="actions"><form method="post" action="/admin/requests/{r['id']}/approve"><button>Approve</button></form><form method="post" action="/admin/requests/{r['id']}/reject"><button class="danger">Reject</button></form></td></tr>"""
            for r in pending
        )
        audit_rows = "".join(
            f"""<tr><td>{q(a,'created_at')}</td><td>{q(a,'action')}</td><td>{q(a,'actor_email','System')}</td><td>{q(a,'employee_name')}</td><td>{q(a,'before_value')}</td><td>{q(a,'after_value')}</td><td>{q(a,'notes') or q(a,'details')}</td></tr>"""
            for a in audit_logs
        )
        expiring_rows = "".join(
            f"""<tr><td>{q(c,'employee_name')}</td><td>{q(c,'public_holiday_name')}</td><td>{q(c,'public_holiday_date')}</td><td>{money_days(c['remaining_amount_days'])}</td><td>{q(c,'expiry_date')}</td></tr>"""
            for c in expiring_oil
        )
        body = f"""
        <header class="page-head"><div><h1>Admin dashboard</h1><p>Pending approvals and employee balances for {year}.</p></div><div class="actions"><a class="button" href="/admin/employees/new">Add employee or user</a><a class="button ghost" href="/admin/leaves">Leave records</a><a class="button ghost" href="/admin/leave-types">Leave types</a><a class="button ghost" href="/admin/off-in-lieu">Off-in-lieu</a><a class="button ghost" href="/admin/export/employees.csv">Export employees</a><a class="button ghost" href="/admin/export/users.csv">Export users</a><a class="button ghost" href="/admin/export/leaves.csv">Export leave records</a><a class="button ghost" href="/admin/export/balances.csv">Export balances</a><a class="button ghost" href="/admin/export/off-in-lieu.csv">Export off-in-lieu</a><a class="button ghost" href="/admin/export/holidays.csv">Export holidays</a><a class="button ghost" href="/admin/export/audit-log.csv">Export audit log</a><a class="button ghost" href="/admin/export/backup.zip">Backup all data</a><a class="button ghost" href="/admin/holidays?year={year}">Public holidays</a><a class="button ghost" href="/admin/seed">Sample data</a></div></header>
        <div id="notifications">{notifications_html}</div>
        <section class="stats">
          <div><b>{len(employees)}</b><span>Total employees</span></div>
          <div><b>{len(pending)}</b><span>Pending requests</span></div>
          <div><b>{sum(1 for e in employees if e['status'] == 'active')}</b><span>Active employees</span></div>
          <div><b>{len(expiring_oil)}</b><span>Expiring off-in-lieu</span></div>
        </section>
        <section class="panel">
          <h2>Pending by approver</h2>
          <p>{html.escape(pending_by_approver_text)}</p>
        </section>
        <section class="panel">
          <h2>MOM annual leave settings</h2>
          <p>3-month eligibility rule: {settings['enforce_mom_three_month_rule'].capitalize()} · Company override: {settings['allow_company_override'].capitalize()}</p>
          <a class="button ghost" href="/admin/settings">Edit settings</a>
        </section>
        <section class="panel">
          <h2>Pending leave requests</h2>
          <div class="table-wrap"><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Approver</th><th>Reason</th><th>Attachment</th><th></th></tr></thead><tbody>{pending_rows or '<tr><td colspan="8">No pending requests.</td></tr>'}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Employees</h2>
          <div class="table-wrap"><table><thead><tr><th>Name</th><th>Department</th><th>Join date</th><th>Annual balance</th><th>Pending annual</th><th>Off-in-lieu</th><th>Pending OIL</th><th>Status</th><th></th></tr></thead><tbody>{emp_rows}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Expiring off-in-lieu credits</h2>
          <div class="table-wrap"><table><thead><tr><th>Employee</th><th>Public holiday</th><th>Date</th><th>Remaining</th><th>Expires</th></tr></thead><tbody>{expiring_rows or '<tr><td colspan="5">No off-in-lieu credits expiring in the next 90 days.</td></tr>'}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Manual balance adjustment</h2>
          <form method="post" action="/admin/adjustments" class="inline-form">
            <select name="employee_id">{''.join(f'<option value="{e["id"]}">{q(e,"name")}</option>' for e in employees)}</select>
            <input type="number" step="0.5" name="days" placeholder="Days (+/-)" required>
            <input type="number" name="year" value="{year}" required>
            <input name="reason" placeholder="Reason" required>
            <button>Add adjustment</button>
          </form>
        </section>
        <section class="panel">
          <h2>Audit log</h2>
          <div class="table-wrap"><table><thead><tr><th>Time</th><th>Action type</th><th>User</th><th>Employee</th><th>Before</th><th>After</th><th>Notes</th></tr></thead><tbody>{audit_rows or '<tr><td colspan="7">No audit activity yet.</td></tr>'}</tbody></table></div>
        </section>"""
        self.send_html("Admin", body, user)

    def admin_leave_records(self, user):
        query = parse_qs(urlparse(self.path).query)
        filters = {
            "employee": query.get("employee", [""])[0].strip(),
            "department": query.get("department", [""])[0].strip(),
            "leave_type": query.get("leave_type", [""])[0].strip(),
            "status": query.get("status", [""])[0].strip(),
            "approver": query.get("approver", [""])[0].strip(),
            "date_from": query.get("date_from", [""])[0].strip(),
            "date_to": query.get("date_to", [""])[0].strip(),
        }
        where = []
        params = []
        if filters["employee"]:
            where.append("(employees.name LIKE ? OR employees.email LIKE ?)")
            params.extend([f"%{filters['employee']}%", f"%{filters['employee']}%"])
        if filters["department"]:
            where.append("employees.department LIKE ?")
            params.append(f"%{filters['department']}%")
        if filters["leave_type"]:
            where.append("leave_requests.leave_type = ?")
            params.append(filters["leave_type"])
        if filters["status"]:
            where.append("leave_requests.status = ?")
            params.append(filters["status"])
        if filters["approver"]:
            where.append("(approver_employee.name LIKE ? OR approver.email LIKE ?)")
            params.extend([f"%{filters['approver']}%", f"%{filters['approver']}%"])
        if filters["date_from"]:
            where.append("leave_requests.end_date >= ?")
            params.append(filters["date_from"])
        if filters["date_to"]:
            where.append("leave_requests.start_date <= ?")
            params.append(filters["date_to"])
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        with db() as conn:
            leave_types = enabled_leave_types(conn)
            rows = conn.execute(
                f"""
                SELECT leave_requests.*, employees.name, employees.department,
                       approver_employee.name AS approver_name
                FROM leave_requests
                JOIN employees ON employees.id = leave_requests.employee_id
                LEFT JOIN users AS approver ON approver.id = leave_requests.approver_user_id
                LEFT JOIN employees AS approver_employee ON approver_employee.id = approver.employee_id
                {where_sql}
                ORDER BY leave_requests.created_at DESC
                LIMIT 200
                """,
                params,
            ).fetchall()
        leave_type_options = [("", "All leave types")] + [(row["name"], row["name"]) for row in leave_types]
        status_options = [("", "All statuses"), ("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("cancelled", "Cancelled")]
        result_rows = "".join(
            f"""<tr><td>{q(r,'name')}<small>{q(r,'department')}</small></td><td>{q(r,'leave_type')}</td><td>{q(r,'start_date')} to {q(r,'end_date')}</td><td>{money_days(r['days'])}</td><td>{q(r,'approver_name','Admin / HR')}</td><td><span class="status {q(r,'status')}">{q(r,'status')}</span></td><td>{q(r,'reason')}</td></tr>"""
            for r in rows
        )
        body = f"""
        <header class="page-head"><div><h1>Leave records</h1><p>Filter leave requests by employee, department, type, status, approver, and date range.</p></div><a class="button ghost" href="/admin">Back to admin</a></header>
        <form method="get" class="panel form-grid">
          {field("Employee name or email", "employee", filters["employee"], required=False)}
          {field("Department", "department", filters["department"], required=False)}
          {select("Leave type", "leave_type", leave_type_options, filters["leave_type"])}
          {select("Status", "status", status_options, filters["status"])}
          {field("Approver", "approver", filters["approver"], required=False)}
          {field("From date", "date_from", filters["date_from"], "date", required=False)}
          {field("To date", "date_to", filters["date_to"], "date", required=False)}
          <div class="actions wide"><button>Apply filters</button><a class="button ghost" href="/admin/leaves">Clear</a><a class="button ghost" href="/admin/export/leaves.csv">Export CSV</a></div>
        </form>
        <section class="panel">
          <h2>Results</h2>
          <div class="table-wrap"><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Approver</th><th>Status</th><th>Reason</th></tr></thead><tbody>{result_rows or '<tr><td colspan="7">No leave records found.</td></tr>'}</tbody></table></div>
        </section>"""
        self.send_html("Leave records", body, user)

    def export_employees_csv(self, user):
        year = date.today().year
        with db() as conn:
            employees = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
            rows = []
            for employee in employees:
                balance = employee_balance(conn, employee["id"], year)
                rows.append(
                    [
                        employee["id"],
                        employee["name"],
                        employee["email"],
                        employee["department"],
                        employee["job_title"] or "",
                        employee["join_date"],
                        employee["probation_end_date"],
                        employee["annual_entitlement"],
                        employee["status"],
                        employee["approver_user_id"] or "",
                        balance["calculation"].completed_months,
                        balance["earned"],
                        balance["approved"],
                        balance["pending"],
                        balance["adjustments"],
                        balance["balance"],
                        off_in_lieu_balance(conn, employee["id"]),
                        pending_off_in_lieu_days(conn, employee["id"]),
                    ]
                )
        self.send_csv(
            f"employees-{year}.csv",
            [
                "employee_id",
                "name",
                "email",
                "department",
                "job_title",
                "join_date",
                "probation_end_date",
                "annual_entitlement",
                "status",
                "approver_user_id",
                "completed_months",
                "earned_entitlement",
                "approved_taken",
                "pending_leave",
                "manual_adjustments",
                "balance",
                "off_in_lieu_balance",
                "pending_off_in_lieu",
            ],
            rows,
        )

    def export_users_csv(self, user):
        with db() as conn:
            users = conn.execute(
                """
                SELECT users.id, users.email, users.role, users.active, users.created_at,
                       employees.name, employees.department, employees.status
                FROM users JOIN employees ON employees.id = users.employee_id
                ORDER BY users.role, employees.name
                """
            ).fetchall()
        rows = [
            [row["id"], row["name"], row["email"], row["role"], "yes" if row["active"] else "no", row["department"], row["status"], row["created_at"]]
            for row in users
        ]
        self.send_csv("users.csv", ["user_id", "name", "email", "role", "login_active", "department", "employee_status", "created_at"], rows)

    def export_leave_records_csv(self, user):
        with db() as conn:
            records = conn.execute(
                """
                SELECT leave_requests.*, employees.name AS employee_name, employees.email AS employee_email,
                       approver.email AS decided_by_email, assigned.email AS assigned_approver_email
                FROM leave_requests
                JOIN employees ON employees.id = leave_requests.employee_id
                LEFT JOIN users AS approver ON approver.id = leave_requests.decided_by
                LEFT JOIN users AS assigned ON assigned.id = leave_requests.approver_user_id
                ORDER BY leave_requests.created_at DESC
                """
            ).fetchall()
        rows = [
            [
                record["id"],
                record["employee_name"],
                record["employee_email"],
                record["leave_type"],
                record["start_date"],
                record["end_date"],
                "yes" if record["half_day"] else "no",
                record["days"],
                record["status"],
                record["reason"],
                record["created_at"],
                record["assigned_approver_email"] or "Admin / HR",
                record["decided_by_email"] or "",
                record["decided_at"] or "",
            ]
            for record in records
        ]
        self.send_csv(
            "leave-records.csv",
            [
                "leave_request_id",
                "employee_name",
                "employee_email",
                "leave_type",
                "start_date",
                "end_date",
                "half_day",
                "chargeable_days",
                "status",
                "reason",
                "submitted_at",
                "assigned_approver",
                "decided_by",
                "decided_at",
            ],
            rows,
        )

    def leave_balance_export_rows(self, conn, year: int):
        employees = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
        rows = []
        for employee in employees:
            annual = employee_balance(conn, employee["id"], year)
            rows.append(
                [
                    employee["id"],
                    employee["name"],
                    employee["email"],
                    employee["department"],
                    employee["status"],
                    year,
                    annual["calculation"].completed_months,
                    annual["earned"],
                    annual["approved"],
                    annual["pending"],
                    annual["adjustments"],
                    annual["balance"],
                    max(0.0, round(annual["balance"] - annual["pending"], 2)),
                    off_in_lieu_balance(conn, employee["id"]),
                    pending_off_in_lieu_days(conn, employee["id"]),
                ]
            )
        return rows

    def leave_balance_export_headers(self):
        return [
            "employee_id",
            "name",
            "email",
            "department",
            "status",
            "year",
            "completed_months",
            "annual_leave_earned",
            "approved_annual_leave_taken",
            "pending_annual_leave",
            "manual_adjustments",
            "annual_leave_balance",
            "annual_leave_available",
            "off_in_lieu_balance",
            "pending_off_in_lieu",
        ]

    def export_leave_balances_csv(self, user):
        year = date.today().year
        with db() as conn:
            rows = self.leave_balance_export_rows(conn, year)
        self.send_csv(f"leave-balances-{year}.csv", self.leave_balance_export_headers(), rows)

    def export_off_in_lieu_csv(self, user):
        with db() as conn:
            credits = conn.execute(
                """
                SELECT off_in_lieu_credits.*, employees.name AS employee_name, employees.email AS employee_email
                FROM off_in_lieu_credits
                JOIN employees ON employees.id = off_in_lieu_credits.employee_id
                ORDER BY off_in_lieu_credits.expiry_date, employees.name
                """
            ).fetchall()
        rows = [
            [
                credit["id"],
                credit["employee_id"],
                credit["employee_name"],
                credit["employee_email"],
                credit["public_holiday_name"],
                credit["public_holiday_date"],
                credit["credit_date"],
                credit["credit_amount_days"],
                credit["used_amount_days"],
                credit["remaining_amount_days"],
                credit["expiry_date"],
                credit["status"],
                credit["notes"] or "",
                credit["created_at"],
            ]
            for credit in credits
        ]
        self.send_csv(
            "off-in-lieu-credits.csv",
            [
                "credit_id",
                "employee_id",
                "employee_name",
                "employee_email",
                "public_holiday_name",
                "public_holiday_date",
                "credit_date",
                "credit_amount_days",
                "used_amount_days",
                "remaining_amount_days",
                "expiry_date",
                "status",
                "notes",
                "created_at",
            ],
            rows,
        )

    def export_public_holidays_csv(self, user):
        with db() as conn:
            holidays = conn.execute("SELECT * FROM public_holidays ORDER BY holiday_date").fetchall()
        rows = [[row["id"], row["holiday_date"], row["name"], row["created_at"]] for row in holidays]
        self.send_csv("public-holidays.csv", ["holiday_id", "holiday_date", "name", "created_at"], rows)

    def export_audit_log_csv(self, user):
        with db() as conn:
            logs = conn.execute(
                """
                SELECT audit_logs.*, users.email AS actor_email, employees.name AS employee_name,
                       leave_requests.leave_type AS leave_type
                FROM audit_logs
                LEFT JOIN users ON users.id = audit_logs.actor_user_id
                LEFT JOIN employees ON employees.id = audit_logs.employee_id
                LEFT JOIN leave_requests ON leave_requests.id = audit_logs.leave_request_id
                ORDER BY audit_logs.created_at DESC
                """
            ).fetchall()
        rows = [
            [
                log["id"],
                log["created_at"],
                log["actor_user_id"] or "",
                log["actor_email"] or "System",
                log["employee_id"] or "",
                log["employee_name"] or "",
                log["leave_request_id"] or "",
                log["leave_type"] or "",
                log["action"],
                log["before_value"] or "",
                log["after_value"] or "",
                log["notes"] or log["details"],
            ]
            for log in logs
        ]
        self.send_csv(
            "audit-log.csv",
            [
                "audit_log_id",
                "created_at",
                "actor_user_id",
                "actor_email",
                "employee_id",
                "employee_name",
                "leave_request_id",
                "leave_type",
                "action_type",
                "before_value",
                "after_value",
                "notes",
            ],
            rows,
        )

    def table_export(self, conn, table_name: str):
        columns = [row["name"] for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()]
        rows = conn.execute(f"SELECT * FROM {table_name}").fetchall()
        return columns, [[row[column] for column in columns] for row in rows]

    def export_backup_zip(self, user):
        backup_tables = [
            "employees",
            "users",
            "leave_requests",
            "leave_types",
            "balance_adjustments",
            "app_settings",
            "public_holidays",
            "audit_logs",
            "off_in_lieu_credits",
            "off_in_lieu_usages",
            "notifications",
        ]
        year = date.today().year
        generated_at = datetime.now().strftime("%Y%m%d-%H%M%S")
        buffer = io.BytesIO()
        with db() as conn:
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "README.txt",
                    "LeaveDesk backup export. CSV files are UTF-8 with BOM for Excel. The SQLite database and uploaded attachments are included when available.\n",
                )
                for table_name in backup_tables:
                    headers, rows = self.table_export(conn, table_name)
                    archive.writestr(f"csv/{table_name}.csv", self.csv_bytes(headers, rows))
                archive.writestr(
                    f"csv/leave_balances_{year}.csv",
                    self.csv_bytes(self.leave_balance_export_headers(), self.leave_balance_export_rows(conn, year)),
                )
                sqlite_backup = sqlite3.connect(":memory:")
                try:
                    conn.backup(sqlite_backup)
                    db_bytes = "\n".join(sqlite_backup.iterdump()).encode("utf-8")
                    archive.writestr("database/leave_app.sql", db_bytes)
                finally:
                    sqlite_backup.close()
        if DB_PATH.exists():
            archive_path = "database/leave_app.sqlite3"
            with zipfile.ZipFile(buffer, "a", zipfile.ZIP_DEFLATED) as archive:
                archive.write(DB_PATH, archive_path)
                if UPLOAD_DIR.exists():
                    for upload in UPLOAD_DIR.rglob("*"):
                        if upload.is_file():
                            archive.write(upload, f"uploads/{upload.relative_to(UPLOAD_DIR).as_posix()}")
        self.send_download(f"leavedesk-backup-{generated_at}.zip", "application/zip", buffer.getvalue())

    def admin_leave_types(self, user):
        with db() as conn:
            leave_types = conn.execute("SELECT * FROM leave_types ORDER BY name").fetchall()
        rows = "".join(
            f"""
            <tr>
              <td>{q(t,'name')}<small>{q(t,'balance_kind')}</small></td>
              <td>
                <label class="check"><input type="checkbox" name="enabled__{html.escape(t['name'])}" value="1" {'checked' if t['enabled'] else ''}> Enabled</label>
              </td>
              <td>
                <label class="check"><input type="checkbox" name="deducts__{html.escape(t['name'])}" value="1" {'checked' if t['deducts_balance'] else ''}> Deducts balance</label>
              </td>
              <td><input type="number" step="0.5" name="entitlement__{html.escape(t['name'])}" value="{money_days(t['default_entitlement_days'])}"></td>
            </tr>
            """
            for t in leave_types
        )
        body = f"""
        <header class="page-head"><div><h1>Leave types</h1><p>Enable leave types and configure whether each one deducts from its own balance.</p></div><a class="button ghost" href="/admin">Back to admin</a></header>
        <form method="post" class="panel">
          <div class="table-wrap"><table><thead><tr><th>Leave type</th><th>Enabled</th><th>Deducts balance</th><th>Default entitlement</th></tr></thead><tbody>{rows}</tbody></table></div>
          <button>Save leave types</button>
        </form>"""
        self.send_html("Leave types", body, user)

    def save_leave_types(self, user):
        data, _ = self.read_form()
        with db() as conn:
            leave_types = conn.execute("SELECT * FROM leave_types").fetchall()
            before = {
                leave_type["name"]: {
                    "enabled": leave_type["enabled"],
                    "deducts_balance": leave_type["deducts_balance"],
                    "default_entitlement_days": leave_type["default_entitlement_days"],
                }
                for leave_type in leave_types
            }
            for leave_type in leave_types:
                name = leave_type["name"]
                enabled = 1 if data.get(f"enabled__{name}") == "1" else 0
                deducts = 1 if data.get(f"deducts__{name}") == "1" else 0
                entitlement = float(data.get(f"entitlement__{name}", leave_type["default_entitlement_days"]) or 0)
                conn.execute(
                    """
                    UPDATE leave_types
                    SET enabled = ?, deducts_balance = ?, default_entitlement_days = ?
                    WHERE name = ?
                    """,
                    (enabled, deducts, entitlement, name),
                )
            updated = conn.execute("SELECT * FROM leave_types").fetchall()
            after = {
                leave_type["name"]: {
                    "enabled": leave_type["enabled"],
                    "deducts_balance": leave_type["deducts_balance"],
                    "default_entitlement_days": leave_type["default_entitlement_days"],
                }
                for leave_type in updated
            }
            before_changed, after_changed = changed_values(before, after)
            if before_changed:
                add_audit_log(
                    conn,
                    user["id"],
                    "admin_settings_changed",
                    f"{user['email']} updated leave type settings.",
                    before_value=before_changed,
                    after_value=after_changed,
                    notes="Leave type settings updated.",
                )
        self.redirect("/admin/leave-types")

    def admin_holidays(self, user):
        query = parse_qs(urlparse(self.path).query)
        year = int(query.get("year", [date.today().year])[0])
        with db() as conn:
            holidays = public_holidays_for_year(conn, year)
        rows = "".join(
            f"""<tr><td>{q(h,'holiday_date')}</td><td>{q(h,'name')}</td><td><form method="post" action="/admin/holidays/{h['id']}/delete"><button class="danger">Delete</button></form></td></tr>"""
            for h in holidays
        )
        body = f"""
        <header class="page-head"><div><h1>Public holidays</h1><p>Manage Singapore public holidays used when calculating chargeable leave days.</p></div><a class="button ghost" href="/admin">Back to admin</a></header>
        <section class="panel">
          <form method="get" action="/admin/holidays" class="inline-form">
            <input type="number" name="year" value="{year}" min="2000" max="2100" required>
            <button>View year</button>
          </form>
        </section>
        <section class="panel">
          <h2>{year} holidays</h2>
          <div class="table-wrap"><table><thead><tr><th>Date</th><th>Name</th><th></th></tr></thead><tbody>{rows or '<tr><td colspan="3">No public holidays for this year yet.</td></tr>'}</tbody></table></div>
        </section>
        <section class="panel">
          <h2>Add public holiday</h2>
          <form method="post" action="/admin/holidays" class="form-grid">
            {field("Date", "holiday_date", f"{year}-01-01", "date")}
            {field("Name", "name")}
            <button>Add holiday</button>
          </form>
        </section>"""
        self.send_html("Public holidays", body, user)

    def save_public_holiday(self, user):
        data, _ = self.read_form()
        holiday_date = parse_date(data["holiday_date"])
        with db() as conn:
            before = conn.execute("SELECT * FROM public_holidays WHERE holiday_date = ?", (holiday_date.isoformat(),)).fetchone()
            conn.execute(
                "INSERT OR REPLACE INTO public_holidays (holiday_date, name) VALUES (?, ?)",
                (holiday_date.isoformat(), data["name"]),
            )
            after = conn.execute("SELECT * FROM public_holidays WHERE holiday_date = ?", (holiday_date.isoformat(),)).fetchone()
            add_audit_log(
                conn,
                user["id"],
                "public_holiday_saved",
                f"{user['email']} saved public holiday {data['name']} on {holiday_date.isoformat()}.",
                before_value=row_snapshot(before, ["holiday_date", "name"]) if before else {},
                after_value=row_snapshot(after, ["holiday_date", "name"]),
                notes="Public holiday saved.",
            )
            generate_off_in_lieu_credits(conn, user["id"], year=holiday_date.year)
        self.redirect(f"/admin/holidays?year={holiday_date.year}")

    def delete_public_holiday(self, user, holiday_id):
        with db() as conn:
            row = conn.execute("SELECT * FROM public_holidays WHERE id = ?", (holiday_id,)).fetchone()
            year = parse_date(row["holiday_date"]).year if row else date.today().year
            conn.execute("DELETE FROM public_holidays WHERE id = ?", (holiday_id,))
            if row:
                add_audit_log(
                    conn,
                    user["id"],
                    "public_holiday_deleted",
                    f"{user['email']} deleted public holiday {row['name']} on {row['holiday_date']}.",
                    before_value=row_snapshot(row, ["holiday_date", "name"]),
                    after_value={},
                    notes="Public holiday deleted.",
                )
        self.redirect(f"/admin/holidays?year={year}")

    def admin_off_in_lieu(self, user):
        with db() as conn:
            employees = conn.execute("SELECT * FROM employees ORDER BY name").fetchall()
            credits = conn.execute(
                """
                SELECT off_in_lieu_credits.*, employees.name AS employee_name
                FROM off_in_lieu_credits
                JOIN employees ON employees.id = off_in_lieu_credits.employee_id
                ORDER BY off_in_lieu_credits.expiry_date, employees.name
                """
            ).fetchall()
            comp = app_setting(conn, "saturday_ph_compensation_method", "off_in_lieu")
        rows = "".join(
            f"""
            <tr>
              <td>{q(c,'employee_name')}<small>{q(c,'public_holiday_name')} · {q(c,'public_holiday_date')}</small></td>
              <td>{money_days(c['credit_amount_days'])}</td>
              <td>{money_days(c['used_amount_days'])}</td>
              <td>{money_days(c['remaining_amount_days'])}</td>
              <td>{q(c,'expiry_date')}</td>
              <td><span class="status {q(c,'status')}">{q(c,'status')}</span></td>
              <td>
                <form method="post" action="/admin/off-in-lieu/{c['id']}/edit" class="mini-form">
                  <input type="number" step="0.5" name="credit_amount_days" value="{money_days(c['credit_amount_days'])}" required>
                  <input type="number" step="0.5" name="used_amount_days" value="{money_days(c['used_amount_days'])}" required>
                  <input type="date" name="expiry_date" value="{q(c,'expiry_date')}" required>
                  <input name="notes" value="{q(c,'notes')}" placeholder="Notes">
                  <button>Save</button>
                </form>
                <div class="actions">
                  <form method="post" action="/admin/off-in-lieu/{c['id']}/expire"><button class="ghost">Expire</button></form>
                  <form method="post" action="/admin/off-in-lieu/{c['id']}/delete"><button class="danger">Remove</button></form>
                </div>
              </td>
            </tr>
            """
            for c in credits
        )
        body = f"""
        <header class="page-head"><div><h1>Off-in-lieu credits</h1><p>Saturday public holiday compensation: {html.escape(comp)}</p></div><a class="button ghost" href="/admin">Back to admin</a></header>
        <section class="panel">
          <form method="post" action="/admin/off-in-lieu/generate" class="inline-form">
            <input type="number" name="year" value="{date.today().year}" required>
            <button>Generate Saturday PH credits</button>
          </form>
        </section>
        <section class="panel">
          <h2>Add credit</h2>
          <form method="post" action="/admin/off-in-lieu" class="form-grid">
            <label>Employee<select name="employee_id">{''.join(f'<option value="{e["id"]}">{q(e,"name")}</option>' for e in employees)}</select></label>
            {field("Public holiday name", "public_holiday_name")}
            {field("Public holiday date", "public_holiday_date", input_type="date")}
            {field("Credit date", "credit_date", date.today().isoformat(), "date")}
            {field("Credit amount days", "credit_amount_days", 1, "number", extra="step='0.5'")}
            {field("Expiry date", "expiry_date", month_add(date.today(), 12).isoformat(), "date")}
            <label class="wide">Notes<textarea name="notes"></textarea></label>
            <button>Add credit</button>
          </form>
        </section>
        <section class="panel">
          <h2>Credit ledger</h2>
          <div class="table-wrap"><table><thead><tr><th>Employee / Holiday</th><th>Credit</th><th>Used</th><th>Remaining</th><th>Expires</th><th>Status</th><th>Manage</th></tr></thead><tbody>{rows or '<tr><td colspan="7">No off-in-lieu credits yet.</td></tr>'}</tbody></table></div>
        </section>"""
        self.send_html("Off-in-lieu", body, user)

    def generate_off_in_lieu(self, user):
        data, _ = self.read_form()
        year = int(data.get("year", date.today().year))
        with db() as conn:
            created = generate_off_in_lieu_credits(conn, user["id"], year=year)
            add_audit_log(
                conn,
                user["id"],
                "off_in_lieu_credits_generated",
                f"Generated {created} off-in-lieu credits for {year}.",
                after_value={"year": year, "credits_created": created},
                notes="Generated eligible Saturday public holiday credits.",
            )
        self.redirect("/admin/off-in-lieu")

    def add_off_in_lieu_credit(self, user):
        data, _ = self.read_form()
        credit_amount = float(data["credit_amount_days"])
        with db() as conn:
            credit_id = conn.execute(
                """
                INSERT INTO off_in_lieu_credits (
                    employee_id, public_holiday_name, public_holiday_date, credit_date,
                    credit_amount_days, used_amount_days, remaining_amount_days, expiry_date, status, notes
                ) VALUES (?, ?, ?, ?, ?, 0, ?, ?, 'active', ?)
                """,
                (
                    data["employee_id"],
                    data["public_holiday_name"],
                    data["public_holiday_date"],
                    data["credit_date"],
                    credit_amount,
                    credit_amount,
                    data["expiry_date"],
                    data.get("notes", ""),
                ),
            ).lastrowid
            credit = conn.execute("SELECT * FROM off_in_lieu_credits WHERE id = ?", (credit_id,)).fetchone()
            add_audit_log(
                conn,
                user["id"],
                "off_in_lieu_credit_added",
                f"Added {money_days(credit_amount)} off-in-lieu days.",
                int(data["employee_id"]),
                before_value={},
                after_value=row_snapshot(
                    credit,
                    [
                        "id",
                        "public_holiday_name",
                        "public_holiday_date",
                        "credit_date",
                        "credit_amount_days",
                        "used_amount_days",
                        "remaining_amount_days",
                        "expiry_date",
                        "status",
                        "notes",
                    ],
                ),
                notes=data.get("notes", ""),
            )
        self.redirect("/admin/off-in-lieu")

    def edit_off_in_lieu_credit(self, user, credit_id):
        data, _ = self.read_form()
        credit_amount = float(data["credit_amount_days"])
        used_amount = min(float(data["used_amount_days"]), credit_amount)
        remaining = max(0.0, round(credit_amount - used_amount, 2))
        status = "used" if remaining == 0 else "active"
        with db() as conn:
            credit = conn.execute("SELECT * FROM off_in_lieu_credits WHERE id = ?", (credit_id,)).fetchone()
            before = row_snapshot(
                credit,
                [
                    "credit_amount_days",
                    "used_amount_days",
                    "remaining_amount_days",
                    "expiry_date",
                    "status",
                    "notes",
                ],
            )
            conn.execute(
                """
                UPDATE off_in_lieu_credits
                SET credit_amount_days = ?, used_amount_days = ?, remaining_amount_days = ?,
                    expiry_date = ?, status = ?, notes = ?
                WHERE id = ?
                """,
                (credit_amount, used_amount, remaining, data["expiry_date"], status, data.get("notes", ""), credit_id),
            )
            if credit:
                updated = conn.execute("SELECT * FROM off_in_lieu_credits WHERE id = ?", (credit_id,)).fetchone()
                after = row_snapshot(
                    updated,
                    [
                        "credit_amount_days",
                        "used_amount_days",
                        "remaining_amount_days",
                        "expiry_date",
                        "status",
                        "notes",
                    ],
                )
                before_changed, after_changed = changed_values(before, after)
                add_audit_log(
                    conn,
                    user["id"],
                    "off_in_lieu_credit_edited",
                    f"Edited off-in-lieu credit #{credit_id}.",
                    credit["employee_id"],
                    before_value=before_changed,
                    after_value=after_changed,
                    notes=data.get("notes", ""),
                )
        self.redirect("/admin/off-in-lieu")

    def set_off_in_lieu_status(self, user, credit_id, status):
        with db() as conn:
            credit = conn.execute("SELECT * FROM off_in_lieu_credits WHERE id = ?", (credit_id,)).fetchone()
            if credit:
                conn.execute("UPDATE off_in_lieu_credits SET status = ?, remaining_amount_days = 0 WHERE id = ?", (status, credit_id))
                add_audit_log(
                    conn,
                    user["id"],
                    "off_in_lieu_credit_removed" if status == "cancelled" else "off_in_lieu_credit_expired",
                    f"Set off-in-lieu credit #{credit_id} to {status}.",
                    credit["employee_id"],
                    before_value={"status": credit["status"], "remaining_amount_days": credit["remaining_amount_days"]},
                    after_value={"status": status, "remaining_amount_days": 0},
                    notes=credit["notes"] or "",
                )
        self.redirect("/admin/off-in-lieu")

    def admin_settings(self, user):
        with db() as conn:
            enforce = app_setting(conn, "enforce_mom_three_month_rule")
            allow_override = app_setting(conn, "allow_company_override")
            saturday_comp = app_setting(conn, "saturday_ph_compensation_method", "off_in_lieu")
            oil_expiry = app_setting(conn, "off_in_lieu_expiry_months", "12")
        body = f"""
        <header class="page-head"><h1>Leave settings</h1></header>
        <form method="post" class="panel form-grid">
          {select("Enforce MOM 3-month eligibility rule", "enforce_mom_three_month_rule", [("yes", "Yes"), ("no", "No")], enforce)}
          {select("Allow company override", "allow_company_override", [("yes", "Yes"), ("no", "No")], allow_override)}
          {select("Saturday public holiday compensation", "saturday_ph_compensation_method", [("off_in_lieu", "Off-in-lieu"), ("salary_in_lieu", "Salary in lieu"), ("none", "None")], saturday_comp)}
          {field("Default off-in-lieu expiry months", "off_in_lieu_expiry_months", oil_expiry, "number", extra="min='1' max='60'")}
          <p class="wide">When enforcement is on, employees with fewer than 3 completed months receive 0 paid annual leave unless company override is allowed and enabled on that employee profile.</p>
          <button>Save settings</button>
        </form>"""
        self.send_html("Settings", body, user)

    def save_admin_settings(self, user):
        data, _ = self.read_form()
        with db() as conn:
            keys = (
                "enforce_mom_three_month_rule",
                "allow_company_override",
                "saturday_ph_compensation_method",
                "off_in_lieu_expiry_months",
            )
            before = {key: app_setting(conn, key) for key in keys}
            for key in ("enforce_mom_three_month_rule", "allow_company_override"):
                value = "yes" if data.get(key) == "yes" else "no"
                conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
            comp = data.get("saturday_ph_compensation_method", "off_in_lieu")
            if comp not in {"off_in_lieu", "salary_in_lieu", "none"}:
                comp = "off_in_lieu"
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", ("saturday_ph_compensation_method", comp))
            expiry = str(max(1, int(data.get("off_in_lieu_expiry_months", "12"))))
            conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", ("off_in_lieu_expiry_months", expiry))
            after = {key: app_setting(conn, key) for key in keys}
            before_changed, after_changed = changed_values(before, after)
            if before_changed:
                add_audit_log(
                    conn,
                    user["id"],
                    "admin_settings_changed",
                    f"{user['email']} changed admin settings.",
                    before_value=before_changed,
                    after_value=after_changed,
                    notes="Admin settings updated.",
                )
        self.redirect("/admin")

    def employee_form(self, user, employee_id=None):
        employee = None
        calculation_note = ""
        if employee_id:
            with db() as conn:
                employee = conn.execute("SELECT employees.*, users.role, users.active AS user_active FROM employees JOIN users ON users.employee_id = employees.id WHERE employees.id = ?", (employee_id,)).fetchone()
                approvers = conn.execute(
                    """
                    SELECT users.id, employees.name, users.role
                    FROM users JOIN employees ON employees.id = users.employee_id
                    WHERE users.active = 1 AND users.role IN ('admin', 'manager') AND employees.id != ?
                    ORDER BY employees.name
                    """,
                    (employee_id,),
                ).fetchall()
                balance = employee_balance(conn, employee_id, date.today().year)
                calculation = balance["calculation"]
                calculation_note = f"""
                <section class="panel">
                  <h2>Annual leave calculation</h2>
                  <div class="stats compact">
                    <div><b>{calculation.completed_months}</b><span>Completed months</span></div>
                    <div><b>{money_days(calculation.raw_entitlement)}</b><span>Raw entitlement</span></div>
                    <div><b>{money_days(calculation.payable_entitlement)}</b><span>Prorated entitlement</span></div>
                    <div><b>{money_days(balance['balance'])}</b><span>Leave balance</span></div>
                  </div>
                  <p>{html.escape(balance['calculation'].explanation)}</p>
                </section>"""
        else:
            with db() as conn:
                approvers = conn.execute(
                    """
                    SELECT users.id, employees.name, users.role
                    FROM users JOIN employees ON employees.id = users.employee_id
                    WHERE users.active = 1 AND users.role IN ('admin', 'manager')
                    ORDER BY employees.name
                    """
                ).fetchall()
        today = date.today()
        join = employee["join_date"] if employee else today.isoformat()
        probation = employee["probation_end_date"] if employee else month_add(today, 3).isoformat()
        role = employee["role"] if employee else "employee"
        user_active = str(employee["user_active"]) if employee else "1"
        approver_options = [("", "Admin / HR fallback")] + [(str(row["id"]), f"{row['name']} ({'Admin' if row['role'] == 'admin' else 'Manager'})") for row in approvers]
        override_checked = "checked" if employee and employee["mom_eligibility_override"] else ""
        work_pattern = employee["work_pattern"] if employee and employee["work_pattern"] else "five_day"
        custom_work_days = employee["custom_work_days"] if employee and employee["custom_work_days"] else "0,1,2,3,4"
        body = f"""
        <header class="page-head"><h1>{'Edit' if employee else 'Add'} employee</h1></header>
        {calculation_note}
        <form method="post" class="panel form-grid">
          {field("Name", "name", employee["name"] if employee else "")}
          {field("Email", "email", employee["email"] if employee else "", "email")}
          {field("Department", "department", employee["department"] if employee else "")}
          {field("Job title", "job_title", employee["job_title"] if employee and employee["job_title"] else "", required=False)}
          {field("Join date", "join_date", join, "date")}
          {field("Annual entitlement", "annual_entitlement", employee["annual_entitlement"] if employee else 14, "number", extra="step='0.5'")}
          {field("Probation end date", "probation_end_date", probation, "date")}
          {select("Leave approver", "approver_user_id", approver_options, str(employee["approver_user_id"] or "") if employee else "")}
          {select("Work pattern", "work_pattern", [("five_day", "5-day work week, Monday to Friday"), ("five_half_day", "5.5-day work week"), ("six_day", "6-day work week"), ("custom", "Custom work days")], work_pattern)}
          {field("Custom work days", "custom_work_days", custom_work_days, "text", required=False)}
          {select("Employee status", "status", [("active", "Active"), ("inactive", "Inactive"), ("resigned", "Resigned")], employee["status"] if employee else "active")}
          {select("User role", "role", [("employee", "Employee"), ("manager", "Manager / Approver"), ("admin", "Admin / HR")], role)}
          {select("Login account", "active", [("1", "Active"), ("0", "Deactivated")], user_active)}
          <label class="check wide"><input type="checkbox" name="mom_eligibility_override" value="1" {override_checked}> Company override for MOM 3-month eligibility</label>
          {field("Password", "password", "", "password", required=employee is None, extra="minlength='8'")}
          <button>Save employee</button>
        </form>"""
        self.send_html("Employee", body, user)

    def save_employee(self, user, employee_id=None):
        data, _ = self.read_form()
        mom_override = 1 if data.get("mom_eligibility_override") == "1" else 0
        work_pattern = data.get("work_pattern", "five_day")
        if work_pattern not in {"five_day", "five_half_day", "six_day", "custom"}:
            work_pattern = "five_day"
        custom_work_days = data.get("custom_work_days", "") if work_pattern == "custom" else None
        if data.get("role") not in {"employee", "manager", "admin"}:
            data["role"] = "employee"
        approver_user_id = int(data["approver_user_id"]) if data.get("approver_user_id") else None
        account_active = 1 if data.get("active", "1") == "1" else 0
        with db() as conn:
            if approver_user_id:
                approver = conn.execute("SELECT * FROM users WHERE id = ? AND role IN ('admin', 'manager') AND active = 1", (approver_user_id,)).fetchone()
                if not approver:
                    self.send_html("Employee", '<p class="error">Selected approver is not active.</p><p><a href="/admin">Back</a></p>', user, 400)
                    return
            if employee_id:
                current_user_row = conn.execute("SELECT id FROM users WHERE employee_id = ?", (employee_id,)).fetchone()
                if current_user_row and approver_user_id == current_user_row["id"]:
                    approver_user_id = None
                before_row = conn.execute(
                    "SELECT employees.*, users.role, users.active AS user_active FROM employees JOIN users ON users.employee_id = employees.id WHERE employees.id = ?",
                    (employee_id,),
                ).fetchone()
                before = row_snapshot(
                    before_row,
                    [
                        "name",
                        "email",
                        "department",
                        "job_title",
                        "join_date",
                        "annual_entitlement",
                        "probation_end_date",
                        "status",
                        "approver_user_id",
                        "mom_eligibility_override",
                        "work_pattern",
                        "custom_work_days",
                        "role",
                        "user_active",
                    ],
                )
                conn.execute(
                    """
                    UPDATE employees SET name=?, email=?, department=?, job_title=?, join_date=?, annual_entitlement=?,
                    probation_end_date=?, status=?, approver_user_id=?, mom_eligibility_override=?, work_pattern=?, custom_work_days=? WHERE id=?
                    """,
                    (
                        data["name"],
                        data["email"].lower(),
                        data["department"],
                        data.get("job_title", ""),
                        data["join_date"],
                        data["annual_entitlement"],
                        data["probation_end_date"],
                        data["status"],
                        approver_user_id,
                        mom_override,
                        work_pattern,
                        custom_work_days,
                        employee_id,
                    ),
                )
                conn.execute("UPDATE users SET email=?, role=?, active=? WHERE employee_id=?", (data["email"].lower(), data["role"], account_active, employee_id))
                if data.get("password"):
                    conn.execute("UPDATE users SET password_hash=? WHERE employee_id=?", (hash_password(data["password"]), employee_id))
                after_row = conn.execute(
                    "SELECT employees.*, users.role, users.active AS user_active FROM employees JOIN users ON users.employee_id = employees.id WHERE employees.id = ?",
                    (employee_id,),
                ).fetchone()
                after = row_snapshot(
                    after_row,
                    [
                        "name",
                        "email",
                        "department",
                        "job_title",
                        "join_date",
                        "annual_entitlement",
                        "probation_end_date",
                        "status",
                        "approver_user_id",
                        "mom_eligibility_override",
                        "work_pattern",
                        "custom_work_days",
                        "role",
                        "user_active",
                    ],
                )
                before_changed, after_changed = changed_values(before, after)
                if data.get("password"):
                    after_changed["password"] = "changed"
                if before_changed or data.get("password"):
                    add_audit_log(
                        conn,
                        user["id"],
                        "employee_edited",
                        f"{user['email']} edited employee {after_row['name']}.",
                        employee_id,
                        before_value=before_changed,
                        after_value=after_changed,
                        notes="Employee profile updated.",
                    )
                if before.get("join_date") != after.get("join_date"):
                    add_audit_log(
                        conn,
                        user["id"],
                        "join_date_changed",
                        f"{user['email']} changed join date for {after_row['name']}.",
                        employee_id,
                        before_value={"join_date": before.get("join_date")},
                        after_value={"join_date": after.get("join_date")},
                        notes="Join date changed.",
                    )
                if str(before.get("annual_entitlement")) != str(after.get("annual_entitlement")):
                    add_audit_log(
                        conn,
                        user["id"],
                        "entitlement_changed",
                        f"{user['email']} changed annual entitlement for {after_row['name']}.",
                        employee_id,
                        before_value={"annual_entitlement": before.get("annual_entitlement")},
                        after_value={"annual_entitlement": after.get("annual_entitlement")},
                        notes="Annual leave entitlement changed.",
                    )
                if str(before.get("probation_end_date")) != str(after.get("probation_end_date")):
                    add_audit_log(conn, user["id"], "probation_changed", f"{user['email']} changed probation end date for {after_row['name']}.", employee_id, before_value={"probation_end_date": before.get("probation_end_date")}, after_value={"probation_end_date": after.get("probation_end_date")}, notes="Probation changed.")
                if str(before.get("approver_user_id", "")) != str(after.get("approver_user_id", "")):
                    add_audit_log(conn, user["id"], "approver_changed", f"{user['email']} changed approver for {after_row['name']}.", employee_id, before_value={"approver_user_id": before.get("approver_user_id", "")}, after_value={"approver_user_id": after.get("approver_user_id", "")}, notes="Approver changed.")
                if str(before.get("user_active", "1")) != str(after.get("user_active", "1")) and str(after.get("user_active")) == "0":
                    add_audit_log(conn, user["id"], "employee_deactivated", f"{user['email']} deactivated {after_row['name']}.", employee_id, before_value={"active": before.get("user_active")}, after_value={"active": after.get("user_active")}, notes="User account deactivated.")
                generate_off_in_lieu_credits(conn, user["id"], employee_id=employee_id)
            else:
                emp_id = conn.execute(
                    """
                    INSERT INTO employees (name, email, department, job_title, join_date, annual_entitlement, probation_end_date, status, approver_user_id, mom_eligibility_override, work_pattern, custom_work_days)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["name"],
                        data["email"].lower(),
                        data["department"],
                        data.get("job_title", ""),
                        data["join_date"],
                        data["annual_entitlement"],
                        data["probation_end_date"],
                        data["status"],
                        approver_user_id,
                        mom_override,
                        work_pattern,
                        custom_work_days,
                    ),
                ).lastrowid
                conn.execute(
                    "INSERT INTO users (employee_id, email, password_hash, role, active) VALUES (?, ?, ?, ?, ?)",
                    (emp_id, data["email"].lower(), hash_password(data["password"]), data["role"], account_active),
                )
                created_row = conn.execute(
                    "SELECT employees.*, users.role, users.active AS user_active FROM employees JOIN users ON users.employee_id = employees.id WHERE employees.id = ?",
                    (emp_id,),
                ).fetchone()
                add_audit_log(
                    conn,
                    user["id"],
                    "employee_created",
                    f"{user['email']} created employee {created_row['name']}.",
                    emp_id,
                    before_value={},
                    after_value=row_snapshot(
                        created_row,
                        [
                            "name",
                            "email",
                            "department",
                            "job_title",
                            "join_date",
                            "annual_entitlement",
                            "probation_end_date",
                            "status",
                            "approver_user_id",
                            "mom_eligibility_override",
                            "work_pattern",
                            "custom_work_days",
                            "role",
                            "user_active",
                        ],
                    ),
                    notes="Employee created.",
                )
                if data["role"] == "manager":
                    add_audit_log(conn, user["id"], "manager_created", f"{user['email']} created manager {created_row['name']}.", emp_id, before_value={}, after_value={"role": "manager"}, notes="Manager / approver account created.")
                if approver_user_id:
                    add_audit_log(conn, user["id"], "approver_assigned", f"{user['email']} assigned approver for {created_row['name']}.", emp_id, before_value={}, after_value={"approver_user_id": approver_user_id}, notes="Approver assigned.")
                generate_off_in_lieu_credits(conn, user["id"], employee_id=emp_id)
        self.redirect("/admin")

    def decide_leave(self, user, request_id, action, admin_override=False):
        status = "approved" if action == "approve" else "rejected"
        with db() as conn:
            request = conn.execute(
                """
                SELECT leave_requests.*, employees.name AS employee_name
                FROM leave_requests
                JOIN employees ON employees.id = leave_requests.employee_id
                WHERE leave_requests.id = ? AND leave_requests.status = 'pending'
                """,
                (request_id,),
            ).fetchone()
            if not can_decide_request(conn, user, request, admin_override):
                self.send_html("Approve leave", '<p class="error">You cannot approve this leave request.</p><p><a href="/">Back</a></p>', user, 403)
                return
            if request and status == "approved" and request["leave_type"] == "Off-in-lieu":
                try:
                    consume_off_in_lieu(conn, request["employee_id"], request_id, float(request["days"]))
                except ValueError as exc:
                    self.send_html("Approve leave", f'<p class="error">{html.escape(str(exc))}</p><p><a href="/admin">Back to admin</a></p>', user, 400)
                    return
            conn.execute(
                "UPDATE leave_requests SET status=?, decided_by=?, decided_at=? WHERE id=? AND status='pending'",
                (status, user["id"], datetime.now().isoformat(), request_id),
            )
            if request:
                add_audit_log(
                    conn,
                    user["id"],
                    ("admin_override_approval" if admin_override and is_admin(user) and request["approver_user_id"] and request["approver_user_id"] != user["id"] and status == "approved" else ("leave_approved" if status == "approved" else "leave_rejected")),
                    f"{user['email']} {status} {request['leave_type']} for {request['employee_name']} from {request['start_date']} to {request['end_date']} ({money_days(request['days'])} days).",
                    request["employee_id"],
                    request_id,
                    before_value={"status": request["status"]},
                    after_value={"status": status, "decided_by": user["email"], "decided_at": datetime.now().isoformat()},
                    notes=request["reason"],
                )
                notify_employee(
                    conn,
                    request["employee_id"],
                    "Leave approved" if status == "approved" else "Leave rejected",
                    f"Your {request['leave_type']} request from {request['start_date']} to {request['end_date']} was {status}.",
                    "/",
                )
        self.redirect("/admin" if is_admin(user) else "/manager")

    def add_adjustment(self, user):
        data, _ = self.read_form()
        with db() as conn:
            employee = conn.execute("SELECT * FROM employees WHERE id = ?", (data["employee_id"],)).fetchone()
            year = int(data["year"])
            before_balance = employee_balance(conn, int(data["employee_id"]), year)
            conn.execute(
                "INSERT INTO balance_adjustments (employee_id, year, days, reason, created_by) VALUES (?, ?, ?, ?, ?)",
                (data["employee_id"], data["year"], data["days"], data["reason"], user["id"]),
            )
            after_balance = employee_balance(conn, int(data["employee_id"]), year)
            add_audit_log(
                conn,
                user["id"],
                "manual_balance_adjustment",
                f"{user['email']} adjusted {employee['name'] if employee else 'employee'} by {data['days']} days for {data['year']}: {data['reason']}.",
                int(data["employee_id"]),
                None,
                before_value={"annual_leave_balance": before_balance["balance"], "manual_adjustments": before_balance["adjustments"]},
                after_value={"annual_leave_balance": after_balance["balance"], "manual_adjustments": after_balance["adjustments"], "adjustment_days": data["days"]},
                notes=data["reason"],
            )
        self.redirect("/admin")

    def seed_page(self, user):
        body = """
        <header class="page-head"><h1>Sample data</h1></header>
        <section class="panel">
          <p>Sample employees and leave requests are for testing only. You can remove them at any time.</p>
          <div class="actions">
            <form method="post" action="/admin/seed"><button>Add sample data</button></form>
            <form method="post" action="/admin/seed/delete"><button class="danger">Delete sample data</button></form>
          </div>
        </section>"""
        self.send_html("Sample data", body, user)

    def seed_data(self, user):
        with db() as conn:
            samples = [
                ("Sample Employee", "sample.employee@example.test", "2026-04-06", "Operations"),
                ("Sample Manager", "sample.manager@example.test", "2025-01-10", "Sales"),
            ]
            for name, email, join, dept in samples:
                if conn.execute("SELECT id FROM employees WHERE email=?", (email,)).fetchone():
                    continue
                emp_id = conn.execute(
                    "INSERT INTO employees (name,email,join_date,department,annual_entitlement,probation_end_date,status) VALUES (?,?,?,?,14,?,'active')",
                    (name, email, join, dept, month_add(parse_date(join), 3).isoformat()),
                ).lastrowid
                conn.execute(
                    "INSERT INTO users (employee_id,email,password_hash,role) VALUES (?,?,?,'employee')",
                    (emp_id, email, hash_password("Password123")),
                )
        self.redirect("/admin")

    def delete_seed_data(self, user):
        with db() as conn:
            rows = conn.execute("SELECT id FROM employees WHERE email LIKE 'sample.%@example.test'").fetchall()
            for row in rows:
                conn.execute("DELETE FROM employees WHERE id=?", (row["id"],))
        self.redirect("/admin")

    def not_found(self, user=None):
        self.send_html("Not found", '<section class="panel"><h1>Page not found</h1><p><a href="/">Go home</a></p></section>', user, 404)


def run():
    init_db()
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), App)
    try:
        print(f"LeaveDesk running at http://127.0.0.1:{port}")
    except Exception:
        pass
    server.serve_forever()


def seed_dev_admin():
    init_db()
    with db() as conn:
        existing = conn.execute("SELECT id FROM users WHERE email = 'admin@example.com'").fetchone()
        if existing:
            print("Local development admin already exists: admin@example.com")
            return
        emp_id = conn.execute(
            """
            INSERT INTO employees (name, email, join_date, department, job_title, annual_entitlement, probation_end_date, status)
            VALUES ('Local Admin', 'admin@example.com', ?, 'HR', 'Admin', 14, ?, 'active')
            """,
            (date.today().isoformat(), month_add(date.today(), 3).isoformat()),
        ).lastrowid
        user_id = conn.execute(
            "INSERT INTO users (employee_id, email, password_hash, role, active) VALUES (?, 'admin@example.com', ?, 'admin', 1)",
            (emp_id, hash_password("Admin123!")),
        ).lastrowid
        add_audit_log(conn, user_id, "employee_created", "Local development test admin created.", emp_id, before_value={}, after_value={"email": "admin@example.com", "role": "admin"}, notes="Local development only.")
    print("Created local development admin: admin@example.com / Admin123!")


if __name__ == "__main__":
    if "--seed-dev-admin" in os.sys.argv:
        seed_dev_admin()
    else:
        run()
