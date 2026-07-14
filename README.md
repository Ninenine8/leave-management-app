# LeaveDesk - Singapore Leave Management App

LeaveDesk is an internal leave management app for a small Singapore company. The production deployment is now Cloudflare Workers with D1 for data and R2 for attachments. The older Python/SQLite app remains in the repository only as a local fallback/reference.

## Tech Stack

- Production backend and UI: Cloudflare Worker, server-rendered HTML
- Production database: Cloudflare D1
- Production file storage: Cloudflare R2 for uploaded attachments
- Local fallback: Python 3.12 standard library, SQLite, local filesystem uploads
- Styling: plain CSS in `static/styles.css`
- Tests: Python `unittest`
- Deployment target: Cloudflare Workers with D1 and R2

## Cloudflare Deployment

The production Cloudflare version is in:

- `src/worker.js` - Cloudflare Worker app
- `src/rules.js` - Singapore MOM leave and leave-day rules
- `migrations/0001_initial.sql` - D1 database schema and seed settings
- `migrations/0002_employee_leave_controls.sql` - employee leave controls, probation, MC, hospitalisation, childcare fields
- `wrangler.toml` - Cloudflare Worker, D1, and R2 bindings
- `package.json` - build, test, migration, and deploy scripts

### Current Stack Assessment

The original app in `app.py` is not Next.js, Vite, React, Express, or Prisma. It is a Python standard-library web server using SQLite and local filesystem uploads. That original runtime is not compatible with Cloudflare Workers because Workers do not run a normal Python `http.server`, and production cannot depend on a local SQLite file or `/uploads` folder.

The Cloudflare production app is a Worker-only JavaScript app. It uses:

- Cloudflare Workers for the web app and backend routes
- Cloudflare D1 for database storage
- Cloudflare R2 for uploaded MC/supporting documents
- Web Crypto PBKDF2 password hashing
- Secure HTTP-only session cookies

### Why Workers, D1, And R2

Use **Cloudflare Workers**, not a normal Node.js server.

Use **D1**, not local SQLite, for production database storage.

Use **R2**, not local `/uploads`, for uploaded PDFs, JPGs, and PNGs.

This is the simplest Cloudflare-compatible structure for the current app because the old Python/SQLite server cannot be directly deployed to Workers.

### Install Cloudflare Tooling

Install dependencies:

```bash
npm install
```

Login to Cloudflare:

```bash
npx wrangler login
```

### Create Cloudflare D1 Database

Run:

```bash
npx wrangler d1 create leavedesk-db
```

Copy the returned `database_id` into `wrangler.toml`:

```toml
[[d1_databases]]
binding = "DB"
database_name = "leavedesk-db"
database_id = "PASTE_DATABASE_ID_HERE"
migrations_dir = "migrations"
```

Apply the schema to Cloudflare:

```bash
npx wrangler d1 migrations apply leavedesk-db --remote
```

For local Worker testing with D1:

```bash
npx wrangler d1 migrations apply leavedesk-db --local
```

### Create Cloudflare R2 Bucket

Run:

```bash
npx wrangler r2 bucket create leave-management-attachments
```

Confirm `wrangler.toml` has:

```toml
[[r2_buckets]]
binding = "ATTACHMENTS"
bucket_name = "leave-management-attachments"
```

### Build And Test

Build check:

```bash
npm run build
```

Tests:

```bash
npm test
```

### Run Locally Through Wrangler

This is only for Cloudflare-style local testing:

```bash
npm run dev
```

Open the Wrangler URL shown in the terminal, usually:

```text
http://127.0.0.1:8787
```

### Deploy To Cloudflare

Deploy:

```bash
npm run deploy
```

After deployment, Wrangler prints a URL like:

```text
https://leave-management-app.<your-subdomain>.workers.dev
```

Open that URL. If the D1 database has no users, the app shows **Create First Admin Account**.

### Cloudflare Dashboard Steps

1. Open Cloudflare Dashboard.
2. Go to **Workers & Pages**.
3. Confirm the Worker named `leave-management-app` exists after deployment.
4. Go to **Storage & Databases > D1 SQL Database**.
5. Confirm `leave-management-db` exists and the migration tables are present.
6. Go to **R2 Object Storage**.
7. Confirm `leave-management-attachments` exists.
8. In the Worker settings, confirm bindings:
   - D1 binding name: `DB`
   - R2 binding name: `ATTACHMENTS`

### First Admin Setup After Deployment

1. Open the Workers URL.
2. Fill in **Create First Admin Account**.
3. Submit.
4. Log in with the admin email/password.

After one admin exists, `/setup` redirects away and public signup is disabled.

### Create Boss / Approver

1. Log in as Admin.
2. Open **Admin > Add employee/user**.
3. Enter the boss/manager details.
4. Set role to **Manager / Approver**.
5. Save.

### Create Employee And Assign Approver

1. Open **Admin > Add employee/user**.
2. Enter the employee details.
3. Set role to **Employee**.
4. Choose the boss/manager in **Approver**.
5. Save.

### Edit Join Date, Probation, Entitlements, And Balances

Admin can edit leave-related employee settings from:

```text
Admin dashboard > Employees > Edit
```

The edit page supports:

- Name, email, department, and job title
- Join date
- Employment status
- Work pattern
- Reporting manager / approver
- Annual leave entitlement
- MC / outpatient sick leave entitlement
- Hospitalisation leave entitlement
- Childcare eligibility and entitlement
- Probation start date
- Probation period in months
- Probation end date
- Probation override status
- Notes
- Manual annual, MC, hospitalisation, and childcare balance adjustments

If the join date changes, annual leave pro-ration is recalculated from the new join date. Manual balance adjustments are stored separately and continue to apply. Join date, entitlement, probation, approver, and manual balance changes are recorded in the audit log.

Default probation is 3 months from join date, ending one day before the same date 3 months later. Example:

```text
Join date: 6 Apr 2026
Default probation end date: 5 Jul 2026
```

### Test Leave Request Flow

1. Log in as the employee.
2. Submit Annual Leave from **Request leave**.
3. Submit Off-in-lieu if the employee has available off-in-lieu credit.
4. Log in as the boss/approver.
5. Open **Team**.
6. Approve or reject the request.
7. Log in as Admin to view balances, audit log, and CSV exports.

### Cloudflare Limitations In This Workspace

I can build and test the Worker code here, but I cannot actually deploy to your Cloudflare account from this sandbox because it requires your Cloudflare login and network access. Run `npm run deploy` on your machine after creating D1 and R2.

## Legacy Python Deployment Note

The old Python app can still run locally from `app.py`, but production should now use the Cloudflare Worker app. The Cloudflare version does not use production SQLite files or local upload folders.

Render/Railway deployment of the Python app is no longer the recommended path for this project.

## SQLite Production Suitability

The old Python app uses SQLite locally. Do not use that local SQLite file for Cloudflare production.

SQLite is acceptable only for the local fallback app if all of these are true:

- It is used by a small company.
- The app runs as one web service instance.
- The SQLite database is stored on a persistent disk.
- Admins use the built-in backup export regularly.

SQLite is not suitable if you need multiple app instances, heavy concurrent writes, advanced database administration, or managed point-in-time recovery. For Cloudflare production, use D1 as configured in `wrangler.toml`.

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

## Legacy Deploy To Render

This section is retained only for the old Python fallback app. For the current deployment, use the Cloudflare instructions above.

Legacy platform: Render paid web service with a persistent disk.

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
7. Confirm the service plan supports a persistent disk.
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
