# LeaveDesk - Singapore Leave Management App

LeaveDesk is a local-first internal leave management app for a small Singapore company. It uses Python's built-in web server and SQLite, with no external package install required.

## Tech Stack

- Backend and UI: Python 3.12 standard library, `http.server`, server-rendered HTML
- Database: SQLite
- File storage: local filesystem for uploaded attachments
- Styling: plain CSS in `static/styles.css`
- Tests: Python `unittest`
- Deployment target: Render web service with a persistent disk

## Deployment Recommendation

Recommended platform: Render.

This app is a long-running Python web app that writes to SQLite and stores uploaded files. Render is a better fit than Vercel because Render runs a normal web service and supports persistent disks. Vercel functions have a read-only deployment filesystem with only temporary scratch space, so they are not a good fit for this app's SQLite database and uploaded attachments.

Railway can also run this kind of app, but the included deployment files are already prepared for Render. Use Railway only if you prefer its dashboard and are comfortable configuring a persistent volume manually.

## SQLite Production Suitability

SQLite is acceptable for this app if all of these are true:

- It is used by a small company.
- The app runs as one web service instance.
- The SQLite database is stored on a persistent disk.
- Admins use the built-in backup export regularly.

SQLite is not suitable if you need multiple app instances, heavy concurrent writes, advanced database administration, or managed point-in-time recovery. In that case, migrate to PostgreSQL on Render, Supabase, or Neon. For the current small-company internal use case, the simplest reliable deployment is Render plus a persistent disk.

## Install

No dependencies are required beyond Python 3.12 or newer.

```powershell
python --version
```

If you are running inside Codex, you can use the bundled Python:

```powershell
& "C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" --version
```

## Environment Variables

Optional:

```powershell
$env:PORT="8000"
```

If `PORT` is not set, the app runs on port `8000`.

Optional host binding:

```powershell
$env:HOST="0.0.0.0"
```

By default, the app binds to `0.0.0.0` and you still open it at `http://127.0.0.1:8000`.

For deployment, use:

```text
HOST=0.0.0.0
DATA_DIR=/var/data
UPLOAD_DIR=/var/data/uploads
DB_PATH=/var/data/leave_app.sqlite3
```

`PORT` is usually supplied automatically by the hosting platform.

See `.env.example` for a copyable template. Do not commit real `.env` files.

## Database Setup And Migrations

The database is created and migrated automatically when the app starts.

```powershell
python app.py
```

The app stores data in:

- `data/leave_app.sqlite3`
- `uploads/` for leave attachments

On Render, use a persistent disk mounted at `/var/data` so the SQLite database and uploaded attachments survive restarts and deployments.

## Deploy To Render

Recommended platform: Render paid web service with a persistent disk.

Reason: this is a stateful Python + SQLite internal app. Render can run the Python server and attach a persistent disk. Vercel is not recommended for this app because serverless deployments are a poor fit for local SQLite writes and uploaded files.

Important: do not deploy this app on a free/ephemeral service without a persistent disk. If the database is not stored under `/var/data`, company data can disappear on restart or redeploy.

### Deployment Files Included

- `requirements.txt`
- `runtime.txt`
- `Procfile`
- `render.yaml`

### Exact Render Steps

1. Push this project to a GitHub repository.
2. Open Render.
3. Choose `New +` > `Blueprint`.
4. Connect the GitHub repository.
5. Render will read `render.yaml`.
6. Confirm the web service named `leavedesk`.
7. Confirm the service plan supports persistent disks.
8. Confirm the persistent disk:

```text
Name: leavedesk-data
Mount path: /var/data
Size: 1 GB
```

9. Deploy.

Render build command:

```bash
pip install -r requirements.txt && python -m py_compile app.py leave_rules.py
```

Render start command:

```bash
python app.py
```

Render health check:

```text
/healthz
```

Database setup command:

```text
No separate command is required.
```

The app creates and migrates the SQLite database automatically during startup.

### Render Environment Variables

Set these if Render does not apply them from `render.yaml`:

```text
HOST=0.0.0.0
DATA_DIR=/var/data
UPLOAD_DIR=/var/data/uploads
DB_PATH=/var/data/leave_app.sqlite3
```

Do not set `PORT` manually on Render unless Render asks you to. Render supplies it.

### Deployment URL

After deployment, Render gives you a URL like:

```text
https://leavedesk.onrender.com
```

Open that URL. If the database is empty, you will see `Create First Admin Account`.

### Create First Admin After Deployment

1. Open the Render URL.
2. Fill in name, email, password, and confirm password.
3. Submit.
4. Log in with that admin email/password.

Do not use the local development seed admin on a real deployment.

## Run Locally

Open PowerShell in this project folder:

```powershell
cd "C:\Users\LohWeeHui\Documents\Codex\2026-07-08\build-a-simple-leave-management-app"
```

Start the app:

```powershell
python app.py
```

On Windows, you can also double-click:

```text
Start LeaveDesk.cmd
```

If the browser still cannot connect, double-click:

```text
Diagnose LeaveDesk.cmd
```

That checks Python, checks app syntax, starts the app, and keeps the result visible.

Or run:

```powershell
.\Start LeaveDesk.ps1
```

These open a command window and keep the app running. If Codex starts the server in the background, your environment may stop that background process after the command finishes. For normal use, start the app yourself with one of the commands above and keep the window open.

Or with the bundled Python:

```powershell
& "C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" app.py
```

Open:

```text
http://127.0.0.1:8000
```

Keep the PowerShell window running while using the app. Do not close it after running `python app.py`.

If the browser shows `ERR_CONNECTION_REFUSED`, the app server is not running or it was started on a different port. Go back to PowerShell, start the app again, wait for the startup message, then refresh the browser.

When the app starts successfully, the terminal prints:

```text
LeaveDesk running at http://127.0.0.1:8000
```

## First Admin

On an empty database, the app shows `Create First Admin Account`.

Enter:

- Name
- Email
- Password
- Confirm password

After creation, log in with the admin email and password. The setup page is disabled once a user exists.

## Local Development Test Admin

For local development only:

```powershell
python app.py --seed-dev-admin
```

This creates:

```text
Email: admin@example.com
Password: Admin123!
```

Do not use this command for production company data.

## Roles

Admin / HR can manage users, employees, leave requests, leave settings, public holidays, off-in-lieu credits, audit logs, exports, and backups.

Manager / Approver can approve or reject leave for assigned employees only. Managers cannot access admin settings unless they are also Admin.

Employee can view balances, see their assigned approver, submit leave, view leave history, and cancel pending requests.

## Create Employees And Managers

Admin dashboard:

```text
Admin > Add employee or user
```

To create a boss/approver:

1. Fill in name, email, department, job title, join date, and password.
2. Set `User role` to `Manager / Approver`.
3. Keep `Login account` as `Active`.
4. Save.

To assign a boss/approver to an employee:

1. Open the employee edit page.
2. Set `Leave approver` to the manager/admin user.
3. Save.

If no approver is assigned, requests go to Admin / HR.

## Leave Calculation

Annual leave defaults to 14 days per calendar year.

The app follows Singapore MOM annual leave pro-ration:

```text
completed months of service / 12 x annual leave entitlement
```

Only full service months count. Calendar-month counting is not used.

Example for join date `6 Apr 2026`:

```text
6 Apr to 5 May = 1
6 May to 5 Jun = 2
6 Jun to 5 Jul = 3
6 Jul to 5 Aug = 4
6 Aug to 5 Sep = 5
6 Sep to 5 Oct = 6
6 Oct to 5 Nov = 7
6 Nov to 5 Dec = 8
6 Dec to 31 Dec is incomplete

8 / 12 x 14 = 9.33
Rounded down to 9 days
```

Admin setting:

```text
Enforce MOM 3-month eligibility rule: yes/no
```

Manual balance adjustments are available to Admin and are recorded in the audit log.

## Leave Requests And Approval

Employees submit leave from:

```text
My leave > Request leave
```

Required leave types:

- Annual Leave
- Medical Leave
- Hospitalisation Leave
- Childcare Leave
- Unpaid Leave
- Off-in-lieu

Medical Leave and Hospitalisation Leave require an attachment. Allowed files: PDF, JPG, PNG, maximum 5MB.

If the employee has an assigned approver, the request appears in:

```text
Team
```

If no approver is assigned, Admin / HR handles the request.

Approved leave deducts the correct balance. Pending, rejected, and cancelled leave do not deduct final balance.

## Public Holidays

Admin can manage public holidays:

```text
Admin > Public holidays
```

Default Singapore public holidays for 2026 are seeded automatically and can be edited or deleted.

Leave day calculation excludes:

- Employee non-working days based on work pattern
- Singapore public holidays

The leave request form previews chargeable days before submission.

## Saturday Public Holiday Off-In-Lieu

Company policy:

- 5-day Monday-Friday employees receive 1 off-in-lieu day when a Singapore public holiday falls on Saturday.
- 6-day employees do not receive Saturday off-in-lieu automatically.
- Sunday public holidays do not double-credit off-in-lieu.
- Off-in-lieu is separate from annual leave.

Admin setting:

```text
Saturday public holiday compensation: off_in_lieu / salary_in_lieu / none
```

Admin can manage credits:

```text
Admin > Off-in-lieu
```

Default expiry is 12 months from the public holiday date.

## Audit Log

Admin can view recent audit activity on the dashboard and export the full audit log.

Tracked actions include first admin created, employee/user changes, manager creation, approver assignment, join date changes, entitlement changes, probation changes, leave submission/approval/rejection/cancellation, admin override, balance adjustment, off-in-lieu changes, public holiday changes, and admin settings changes.

Each entry includes date/time, user, action type, before value, after value, and notes.

## Export And Backup

Admin CSV exports:

- Employee list
- User list
- Leave requests
- Leave balances
- Off-in-lieu credits
- Public holidays
- Audit log

CSV files include a UTF-8 BOM so they open correctly in Excel.

Admin can also download:

```text
Admin > Backup all data
```

The backup ZIP includes CSV files, SQLite database copy, SQL dump, and uploaded attachments.

## Reset Local Development Database

Stop the app, then delete:

```text
data/leave_app.sqlite3
```

The database will be recreated on the next run.

## Run Tests

```powershell
python -m unittest
```

Bundled Python:

```powershell
& "C:\Users\LohWeeHui\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" -m unittest
```

The tests cover authentication, first admin setup, MOM annual leave pro-ration, leave balances, leave requests, manager approval workflow, public holidays, off-in-lieu, attachments, notifications, audit log, exports, backup, and the full acceptance flow.
