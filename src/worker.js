import {
  calculateAnnualLeaveEntitlement,
  chargeableLeaveDays,
  formatDate,
  parseDate,
  qualifiesForSaturdayOffInLieu,
  validateAttachment,
} from "./rules.js";

const SESSION_DAYS = 7;

export default {
  async fetch(request, env) {
    try {
      return await handleRequest(request, env);
    } catch (error) {
      console.error(error);
      return htmlPage("Error", `<section class="panel"><h1>Something went wrong</h1><p class="error">${escapeHtml(error.message || "Unexpected error")}</p></section>`, null, 500);
    }
  },
};

async function handleRequest(request, env) {
  if (!env.DB) return htmlPage("Missing D1 binding", `<p class="error">Cloudflare D1 binding DB is not configured.</p>`, null, 500);
  const url = new URL(request.url);
  const user = await currentUser(request, env);
  const setupNeeded = await firstAdminNeeded(env);

  if (url.pathname === "/healthz") return new Response("ok");
  if (url.pathname === "/style.css") return new Response(css(), { headers: { "content-type": "text/css; charset=utf-8" } });
  if (url.pathname === "/setup") return request.method === "POST" ? createFirstAdmin(request, env) : setupPage(env);
  if (setupNeeded) return redirect("/setup");
  if (url.pathname === "/login") return request.method === "POST" ? login(request, env) : loginPage();
  if (url.pathname === "/logout") return logout(request, env);
  if (!user) return redirect("/login");

  if (url.pathname === "/") return dashboard(env, user);
  if (url.pathname === "/leave/new") return request.method === "POST" ? submitLeave(request, env, user) : leaveForm(env, user);
  if (url.pathname.startsWith("/leave/") && url.pathname.endsWith("/cancel") && request.method === "POST") return cancelLeave(env, user, idFromPath(url.pathname));
  if (url.pathname === "/manager") return requireManager(user, () => managerDashboard(env, user));
  if (url.pathname.startsWith("/manager/requests/") && request.method === "POST") return requireManager(user, () => decideLeave(env, user, idFromPath(url.pathname), url.pathname.endsWith("/approve") ? "approved" : "rejected", false));
  if (url.pathname === "/admin") return requireAdmin(user, () => adminDashboard(env, user));
  if (url.pathname === "/admin/employees/new") return requireAdmin(user, () => request.method === "POST" ? createEmployee(request, env, user) : employeeForm(env, user));
  if (url.pathname.startsWith("/admin/employees/") && url.pathname.endsWith("/edit")) return requireAdmin(user, () => request.method === "POST" ? updateEmployee(request, env, user, idFromPath(url.pathname)) : editEmployeeForm(env, user, idFromPath(url.pathname)));
  if (url.pathname === "/admin/holidays") return requireAdmin(user, () => request.method === "POST" ? addHoliday(request, env, user) : holidaysPage(env, user));
  if (url.pathname === "/admin/off-in-lieu/generate" && request.method === "POST") return requireAdmin(user, () => generateOffInLieu(env, user));
  if (url.pathname.startsWith("/admin/requests/") && request.method === "POST") return requireAdmin(user, () => decideLeave(env, user, idFromPath(url.pathname), url.pathname.endsWith("/approve") ? "approved" : "rejected", true));
  if (url.pathname === "/admin/export/employees.csv") return requireAdmin(user, () => exportEmployees(env));
  if (url.pathname === "/admin/export/leaves.csv") return requireAdmin(user, () => exportLeaveRequests(env));
  if (url.pathname === "/admin/export/audit-log.csv") return requireAdmin(user, () => exportAuditLog(env));
  if (url.pathname.startsWith("/attachments/")) return attachmentDownload(env, user, idFromPath(url.pathname));
  return htmlPage("Not found", `<section class="panel"><h1>Page not found</h1><p><a href="/">Go home</a></p></section>`, user, 404);
}

async function firstAdminNeeded(env) {
  const row = await env.DB.prepare("SELECT COUNT(*) AS count FROM users WHERE role = 'admin'").first();
  return !row || row.count === 0;
}

async function setupPage(env) {
  if (!(await firstAdminNeeded(env))) return redirect("/login");
  return htmlPage("Create First Admin", `<section class="auth"><h1>Create First Admin Account</h1><form method="post" class="form-grid">${input("Name", "name")}${input("Email", "email", "email")}${input("Password", "password", "password")}${input("Confirm password", "confirm_password", "password")}<button>Create admin</button></form></section>`);
}

async function createFirstAdmin(request, env) {
  if (!(await firstAdminNeeded(env))) return redirect("/login");
  const data = await request.formData();
  if (data.get("password") !== data.get("confirm_password")) return htmlPage("Setup", `<p class="error">Passwords do not match.</p><p><a href="/setup">Back</a></p>`, null, 400);
  const today = formatDate(new Date());
  const probation = formatDate(addMonths(parseDate(today), 3));
  const passwordHash = await hashPassword(String(data.get("password") || ""));
  const employee = await env.DB.prepare(`
    INSERT INTO employees (name, email, department, job_title, join_date, probation_end_date, annual_entitlement, status)
    VALUES (?, ?, 'HR', 'Admin', ?, ?, 14, 'active') RETURNING id
  `).bind(data.get("name"), String(data.get("email")).toLowerCase(), today, probation).first();
  const user = await env.DB.prepare("INSERT INTO users (employee_id, email, password_hash, role, active) VALUES (?, ?, ?, 'admin', 1) RETURNING id")
    .bind(employee.id, String(data.get("email")).toLowerCase(), passwordHash).first();
  await audit(env, user.id, "first_admin_created", null, { email: String(data.get("email")).toLowerCase() }, "First admin setup.");
  return redirect("/login");
}

async function loginPage(message = "") {
  return htmlPage("Login", `<section class="auth"><h1>LeaveDesk Login</h1>${message ? `<p class="error">${escapeHtml(message)}</p>` : ""}<form method="post" class="form-grid">${input("Email", "email", "email")}${input("Password", "password", "password")}<button>Log in</button></form></section>`);
}

async function login(request, env) {
  const data = await request.formData();
  const email = String(data.get("email") || "").toLowerCase();
  const user = await env.DB.prepare("SELECT users.*, employees.name, employees.status FROM users JOIN employees ON employees.id = users.employee_id WHERE users.email = ? AND users.active = 1").bind(email).first();
  if (!user || user.status === "inactive" || !(await verifyPassword(String(data.get("password") || ""), user.password_hash))) return loginPage("Invalid email or password.");
  const token = cryptoRandom();
  const expires = new Date(Date.now() + SESSION_DAYS * 86400000).toISOString();
  await env.DB.prepare("INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)").bind(token, user.id, expires).run();
  return redirect("/", { "set-cookie": `session=${token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=${SESSION_DAYS * 86400}` });
}

async function logout(request, env) {
  const token = cookie(request, "session");
  if (token) await env.DB.prepare("DELETE FROM sessions WHERE token = ?").bind(token).run();
  return redirect("/login", { "set-cookie": "session=; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=0" });
}

async function currentUser(request, env) {
  const token = cookie(request, "session");
  if (!token) return null;
  const user = await env.DB.prepare(`
    SELECT users.*, employees.name, employees.email AS employee_email, employees.department, employees.join_date, employees.annual_entitlement, employees.mom_eligibility_override,
           employees.work_pattern, employees.custom_work_days, employees.approver_user_id, employees.status
    FROM sessions JOIN users ON users.id = sessions.user_id JOIN employees ON employees.id = users.employee_id
    WHERE sessions.token = ? AND sessions.expires_at > datetime('now') AND users.active = 1
  `).bind(token).first();
  return user || null;
}

async function dashboard(env, user) {
  if (user.role === "admin") return adminDashboard(env, user);
  const year = new Date().getUTCFullYear();
  const balance = await employeeBalance(env, user.employee_id, year);
  const requests = await env.DB.prepare("SELECT * FROM leave_requests WHERE employee_id = ? ORDER BY created_at DESC LIMIT 20").bind(user.employee_id).all();
  const approver = user.approver_user_id ? await env.DB.prepare("SELECT employees.name FROM users JOIN employees ON employees.id = users.employee_id WHERE users.id = ?").bind(user.approver_user_id).first() : null;
  const rows = requests.results.map((r) => `<tr><td>${escapeHtml(r.leave_type)}</td><td>${r.start_date} to ${r.end_date}</td><td>${r.days}</td><td>${status(r.status)}</td><td>${r.attachment_key ? `<a href="/attachments/${r.id}">Attachment</a>` : ""}</td></tr>`).join("");
  return htmlPage("My Leave", `<header class="page-head"><div><h1>My leave</h1><p>Approver: ${escapeHtml(approver?.name || "Admin / HR")}</p></div><a class="button" href="/leave/new">Request leave</a></header>
    <section class="cards"><div><strong>${balance.annualBalance}</strong><span>Annual leave balance</span></div><div><strong>${balance.pendingAnnual}</strong><span>Pending annual leave</span></div><div><strong>${balance.oilBalance}</strong><span>Off-in-lieu balance</span></div></section>
    <section class="panel"><h2>Leave history</h2><table><thead><tr><th>Type</th><th>Dates</th><th>Days</th><th>Status</th><th>File</th></tr></thead><tbody>${rows || emptyRow(5)}</tbody></table></section>`, user);
}

async function adminDashboard(env, user) {
  const year = new Date().getUTCFullYear();
  const employees = await env.DB.prepare("SELECT * FROM employees ORDER BY status, name").all();
  const requests = await env.DB.prepare("SELECT leave_requests.*, employees.name FROM leave_requests JOIN employees ON employees.id = leave_requests.employee_id WHERE leave_requests.status = 'pending' ORDER BY leave_requests.created_at").all();
  const empRows = [];
  for (const e of employees.results) {
    const b = await employeeBalance(env, e.id, year);
    empRows.push(`<tr><td>${escapeHtml(e.name)}<small>${escapeHtml(e.email)}</small></td><td>${escapeHtml(e.department)}</td><td>${escapeHtml(e.join_date)}</td><td>${b.completedMonths}</td><td>${b.annualEntitlement}</td><td>${b.annualBalance}</td><td>${b.oilBalance}</td><td>${status(e.status)}</td><td><a href="/admin/employees/${e.id}/edit">Edit</a></td></tr>`);
  }
  const reqRows = requests.results.map((r) => `<tr><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.leave_type)}</td><td>${r.start_date} to ${r.end_date}</td><td>${r.days}</td><td>${r.attachment_key ? `<a href="/attachments/${r.id}">Attachment</a>` : ""}</td><td class="actions"><form method="post" action="/admin/requests/${r.id}/approve"><button>Approve</button></form><form method="post" action="/admin/requests/${r.id}/reject"><button class="danger">Reject</button></form></td></tr>`).join("");
  return htmlPage("Admin", `<header class="page-head"><div><h1>Admin dashboard</h1><p>Employees, pending approvals, exports, and setup.</p></div><div class="actions"><a class="button" href="/admin/employees/new">Add employee/user</a><a class="button ghost" href="/admin/holidays">Public holidays</a><a class="button ghost" href="/admin/export/employees.csv">Export employees</a><a class="button ghost" href="/admin/export/leaves.csv">Export leaves</a><a class="button ghost" href="/admin/export/audit-log.csv">Export audit log</a></div></header>
    <section class="panel"><h2>Pending requests</h2><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>File</th><th>Action</th></tr></thead><tbody>${reqRows || emptyRow(6)}</tbody></table></section>
    <section class="panel"><h2>Employees</h2><table><thead><tr><th>Name</th><th>Dept</th><th>Join date</th><th>Months</th><th>2026 entitlement</th><th>Annual bal.</th><th>OIL bal.</th><th>Status</th><th>Action</th></tr></thead><tbody>${empRows.join("") || emptyRow(9)}</tbody></table></section>`, user);
}

async function managerDashboard(env, user) {
  const requests = await env.DB.prepare("SELECT leave_requests.*, employees.name FROM leave_requests JOIN employees ON employees.id = leave_requests.employee_id WHERE leave_requests.approver_user_id = ? ORDER BY leave_requests.created_at DESC").bind(user.id).all();
  const rows = requests.results.map((r) => `<tr><td>${escapeHtml(r.name)}</td><td>${escapeHtml(r.leave_type)}</td><td>${r.start_date} to ${r.end_date}</td><td>${r.days}</td><td>${status(r.status)}</td><td>${r.status === "pending" ? `<form method="post" action="/manager/requests/${r.id}/approve"><button>Approve</button></form><form method="post" action="/manager/requests/${r.id}/reject"><button class="danger">Reject</button></form>` : ""}</td></tr>`).join("");
  return htmlPage("Team", `<section class="panel"><h1>Team approvals</h1><table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th>Status</th><th>Action</th></tr></thead><tbody>${rows || emptyRow(6)}</tbody></table></section>`, user);
}

async function employeeForm(env, user) {
  const approvers = await env.DB.prepare("SELECT users.id, employees.name, users.role FROM users JOIN employees ON employees.id = users.employee_id WHERE users.active = 1 AND users.role IN ('admin', 'manager') ORDER BY employees.name").all();
  const opts = [`<option value="">Admin / HR fallback</option>`, ...approvers.results.map((a) => `<option value="${a.id}">${escapeHtml(a.name)} (${a.role})</option>`)].join("");
  return htmlPage("Add employee", `<section class="panel"><h1>Add employee or user</h1><form method="post" class="form-grid">${input("Name", "name")}${input("Email", "email", "email")}${input("Department", "department")}${input("Job title", "job_title", "text", "", false)}${input("Join date", "join_date", "date")}${input("Password", "password", "password")}<label>Role<select name="role"><option value="employee">Employee</option><option value="manager">Manager / Approver</option><option value="admin">Admin / HR</option></select></label><label>Work pattern<select name="work_pattern"><option value="five_day">5-day Monday to Friday</option><option value="five_half_day">5.5-day week</option><option value="six_day">6-day week</option><option value="custom">Custom</option></select></label><label>Approver<select name="approver_user_id">${opts}</select></label>${input("Annual entitlement", "annual_entitlement", "number", "14")}<button>Save</button></form></section>`, user);
}

async function createEmployee(request, env, user) {
  const data = await request.formData();
  const joinDate = String(data.get("join_date"));
  const probation = formatDate(addMonths(parseDate(joinDate), 3));
  const employee = await env.DB.prepare(`
    INSERT INTO employees (name, email, department, job_title, join_date, probation_end_date, annual_entitlement, work_pattern, approver_user_id, status)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active') RETURNING id
  `).bind(data.get("name"), String(data.get("email")).toLowerCase(), data.get("department"), data.get("job_title") || "", joinDate, probation, Number(data.get("annual_entitlement") || 14), data.get("work_pattern") || "five_day", nullableInt(data.get("approver_user_id"))).first();
  const passwordHash = await hashPassword(String(data.get("password") || "Password123"));
  const userRow = await env.DB.prepare("INSERT INTO users (employee_id, email, password_hash, role, active) VALUES (?, ?, ?, ?, 1) RETURNING id").bind(employee.id, String(data.get("email")).toLowerCase(), passwordHash, data.get("role") || "employee").first();
  await audit(env, user.id, "employee_created", null, { employee_id: employee.id, email: String(data.get("email")).toLowerCase(), role: data.get("role") }, "Employee/user created.");
  await generateCreditsForEmployee(env, user.id, employee.id);
  return redirect("/admin");
}

async function editEmployeeForm(env, user, employeeId, error = "") {
  const employee = await env.DB.prepare(`
    SELECT employees.*, users.email AS login_email, users.role, users.active
    FROM employees JOIN users ON users.employee_id = employees.id
    WHERE employees.id = ?
  `).bind(employeeId).first();
  if (!employee) return htmlPage("Employee not found", `<p class="error">Employee not found.</p><p><a href="/admin">Back to admin</a></p>`, user, 404);
  const approvers = await env.DB.prepare(`
    SELECT users.id, employees.name, users.role
    FROM users JOIN employees ON employees.id = users.employee_id
    WHERE users.active = 1 AND users.role IN ('admin', 'manager') AND employees.id != ?
    ORDER BY employees.name
  `).bind(employeeId).all();
  const approverOptions = [`<option value="">Admin / HR fallback</option>`, ...approvers.results.map((a) => `<option value="${a.id}" ${String(employee.approver_user_id || "") === String(a.id) ? "selected" : ""}>${escapeHtml(a.name)} (${a.role})</option>`)].join("");
  const calc = calculateAnnualLeaveEntitlement({
    joinDate: employee.join_date,
    calculationYear: 2026,
    annualEntitlementDays: employee.annual_entitlement,
    enforceThreeMonthRule: (await setting(env, "enforce_mom_three_month_rule", "yes")) === "yes",
    companyOverride: Boolean(employee.mom_eligibility_override),
  });
  return htmlPage("Edit employee", `<section class="panel"><h1>Edit employee</h1>${error ? `<p class="error">${escapeHtml(error)}</p>` : ""}<p>${escapeHtml(calc.explanation)}</p><form method="post" class="form-grid">
    ${input("Name", "name", "text", employee.name)}
    ${input("Email", "email", "email", employee.email)}
    ${input("Department", "department", "text", employee.department)}
    ${input("Job title", "job_title", "text", employee.job_title || "", false)}
    ${input("Join date", "join_date", "date", employee.join_date)}
    ${input("Probation end date", "probation_end_date", "date", employee.probation_end_date)}
    ${input("Annual entitlement", "annual_entitlement", "number", String(employee.annual_entitlement))}
    <label>Role<select name="role">
      <option value="employee" ${employee.role === "employee" ? "selected" : ""}>Employee</option>
      <option value="manager" ${employee.role === "manager" ? "selected" : ""}>Manager / Approver</option>
      <option value="admin" ${employee.role === "admin" ? "selected" : ""}>Admin / HR</option>
    </select></label>
    <label>Work pattern<select name="work_pattern">
      <option value="five_day" ${employee.work_pattern === "five_day" ? "selected" : ""}>5-day Monday to Friday</option>
      <option value="five_half_day" ${employee.work_pattern === "five_half_day" ? "selected" : ""}>5.5-day week</option>
      <option value="six_day" ${employee.work_pattern === "six_day" ? "selected" : ""}>6-day week</option>
      <option value="custom" ${employee.work_pattern === "custom" ? "selected" : ""}>Custom</option>
    </select></label>
    <label>Custom work days<input name="custom_work_days" value="${escapeHtml(employee.custom_work_days || "")}" placeholder="1,2,3,4,5"></label>
    <label>Approver<select name="approver_user_id">${approverOptions}</select></label>
    <label>Status<select name="status">
      <option value="active" ${employee.status === "active" ? "selected" : ""}>Active</option>
      <option value="inactive" ${employee.status === "inactive" ? "selected" : ""}>Inactive</option>
      <option value="resigned" ${employee.status === "resigned" ? "selected" : ""}>Resigned</option>
    </select></label>
    <label><input type="checkbox" name="mom_eligibility_override" value="1" ${employee.mom_eligibility_override ? "checked" : ""}> Company override for MOM 3-month eligibility</label>
    <label><input type="checkbox" name="active" value="1" ${employee.active ? "checked" : ""}> Login account active</label>
    <button>Save changes</button><a class="button ghost" href="/admin">Cancel</a>
  </form></section>`, user);
}

async function updateEmployee(request, env, user, employeeId) {
  const before = await env.DB.prepare(`
    SELECT employees.*, users.id AS user_id, users.role, users.active
    FROM employees JOIN users ON users.employee_id = employees.id
    WHERE employees.id = ?
  `).bind(employeeId).first();
  if (!before) return htmlPage("Employee not found", `<p class="error">Employee not found.</p><p><a href="/admin">Back to admin</a></p>`, user, 404);
  const data = await request.formData();
  const approverUserId = nullableInt(data.get("approver_user_id"));
  if (approverUserId && approverUserId === before.user_id) return editEmployeeForm(env, user, employeeId, "Employee cannot be their own approver.");
  const after = {
    name: String(data.get("name") || "").trim(),
    email: String(data.get("email") || "").toLowerCase().trim(),
    department: String(data.get("department") || "").trim(),
    job_title: String(data.get("job_title") || "").trim(),
    join_date: String(data.get("join_date") || ""),
    probation_end_date: String(data.get("probation_end_date") || ""),
    annual_entitlement: Number(data.get("annual_entitlement") || 14),
    role: String(data.get("role") || "employee"),
    work_pattern: String(data.get("work_pattern") || "five_day"),
    custom_work_days: String(data.get("custom_work_days") || ""),
    approver_user_id: approverUserId,
    status: String(data.get("status") || "active"),
    mom_eligibility_override: data.get("mom_eligibility_override") === "1" ? 1 : 0,
    active: data.get("active") === "1" ? 1 : 0,
  };
  await env.DB.prepare(`
    UPDATE employees SET name = ?, email = ?, department = ?, job_title = ?, join_date = ?, probation_end_date = ?, annual_entitlement = ?,
      mom_eligibility_override = ?, work_pattern = ?, custom_work_days = ?, approver_user_id = ?, status = ?, updated_at = CURRENT_TIMESTAMP
    WHERE id = ?
  `).bind(after.name, after.email, after.department, after.job_title, after.join_date, after.probation_end_date, after.annual_entitlement, after.mom_eligibility_override, after.work_pattern, after.custom_work_days, after.approver_user_id, after.status, employeeId).run();
  await env.DB.prepare("UPDATE users SET email = ?, role = ?, active = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?")
    .bind(after.email, after.role, after.active, before.user_id).run();
  if (before.join_date !== after.join_date) await audit(env, user.id, "join_date_changed", { join_date: before.join_date }, { join_date: after.join_date, employee_id: employeeId }, "Join date changed by admin.");
  if (Number(before.annual_entitlement) !== after.annual_entitlement) await audit(env, user.id, "entitlement_changed", { annual_entitlement: before.annual_entitlement }, { annual_entitlement: after.annual_entitlement, employee_id: employeeId }, "Annual entitlement changed by admin.");
  await audit(env, user.id, "employee_edited", before, after, "Employee profile updated.");
  await generateCreditsForEmployee(env, user.id, employeeId);
  return redirect("/admin");
}

async function leaveForm(env, user, error = "") {
  const types = await env.DB.prepare("SELECT * FROM leave_types WHERE enabled = 1 ORDER BY name").all();
  const options = types.results.map((t) => `<option value="${escapeHtml(t.name)}">${escapeHtml(t.name)}</option>`).join("");
  const b = await employeeBalance(env, user.employee_id, new Date().getUTCFullYear());
  return htmlPage("Request leave", `<section class="panel"><h1>Request leave</h1>${error ? `<p class="error">${escapeHtml(error)}</p>` : ""}<p>Annual balance: ${b.annualBalance}. Off-in-lieu balance: ${b.oilBalance}.</p><form method="post" enctype="multipart/form-data" class="form-grid"><label>Leave type<select name="leave_type">${options}</select></label>${input("Start date", "start_date", "date")}${input("End date", "end_date", "date")}<label><input type="checkbox" name="half_day" value="1"> Half day</label><label>Reason<textarea name="reason" required></textarea></label><label>Attachment<input type="file" name="attachment" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"></label><button>Submit request</button></form></section>`, user);
}

async function submitLeave(request, env, user) {
  const data = await request.formData();
  const type = await env.DB.prepare("SELECT * FROM leave_types WHERE name = ? AND enabled = 1").bind(data.get("leave_type")).first();
  if (!type) return leaveForm(env, user, "Selected leave type is not available.");
  const holidays = await publicHolidaySet(env, data.get("start_date"), data.get("end_date"));
  let days;
  try {
    days = chargeableLeaveDays({ startDate: data.get("start_date"), endDate: data.get("end_date"), halfDay: data.get("half_day") === "1", publicHolidays: [...holidays], workPattern: user.work_pattern, customWorkDays: user.custom_work_days || "" });
    validateAttachment(data.get("attachment"), type.name);
    await ensureSufficientBalance(env, user.employee_id, type.balance_category, days);
  } catch (error) {
    await notifyAdmins(env, "Insufficient balance attempt", `${user.name}: ${error.message}`);
    return leaveForm(env, user, error.message);
  }
  let attachment = {};
  const file = data.get("attachment");
  if (file && file.name) {
    const key = `${user.employee_id}/${cryptoRandom()}-${safeName(file.name)}`;
    await env.ATTACHMENTS.put(key, await file.arrayBuffer(), { httpMetadata: { contentType: file.type } });
    attachment = { key, filename: file.name, type: file.type, size: file.size };
  }
  const requestRow = await env.DB.prepare(`
    INSERT INTO leave_requests (employee_id, leave_type, balance_category, start_date, end_date, half_day, days, reason, approver_user_id, attachment_key, attachment_filename, attachment_type, attachment_size)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id
  `).bind(user.employee_id, type.name, type.balance_category, data.get("start_date"), data.get("end_date"), data.get("half_day") === "1" ? 1 : 0, days, data.get("reason"), user.approver_user_id || null, attachment.key || null, attachment.filename || null, attachment.type || null, attachment.size || null).first();
  await audit(env, user.id, "leave_request_submitted", null, { request_id: requestRow.id, leave_type: type.name, days }, "Leave request submitted.");
  await notifyApprover(env, user, requestRow.id);
  return redirect("/");
}

async function decideLeave(env, user, requestId, statusValue, adminOverride) {
  const request = await env.DB.prepare("SELECT * FROM leave_requests WHERE id = ?").bind(requestId).first();
  if (!request || request.status !== "pending") return redirect(user.role === "admin" ? "/admin" : "/manager");
  if (user.role !== "admin" && request.approver_user_id !== user.id) return htmlPage("Forbidden", `<p class="error">You can only decide leave for assigned employees.</p>`, user, 403);
  if (request.employee_id === user.employee_id) return htmlPage("Forbidden", `<p class="error">You cannot approve your own leave.</p>`, user, 403);
  if (statusValue === "approved") await ensureSufficientBalance(env, request.employee_id, request.balance_category, request.days);
  await env.DB.prepare("UPDATE leave_requests SET status = ?, decided_by = ?, decided_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP WHERE id = ?").bind(statusValue, user.id, requestId).run();
  if (statusValue === "approved" && request.balance_category === "off_in_lieu") await consumeOil(env, request.employee_id, request.days);
  await audit(env, user.id, adminOverride ? "admin_override_approval" : `leave_${statusValue}`, request, { status: statusValue }, `Leave ${statusValue}.`);
  return redirect(user.role === "admin" ? "/admin" : "/manager");
}

async function cancelLeave(env, user, requestId) {
  await env.DB.prepare("UPDATE leave_requests SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP WHERE id = ? AND employee_id = ? AND status = 'pending'").bind(requestId, user.employee_id).run();
  await audit(env, user.id, "leave_cancelled", null, { request_id: requestId }, "Employee cancelled pending request.");
  return redirect("/");
}

async function holidaysPage(env, user) {
  const holidays = await env.DB.prepare("SELECT * FROM public_holidays ORDER BY holiday_date").all();
  const rows = holidays.results.map((h) => `<tr><td>${h.holiday_date}</td><td>${escapeHtml(h.name)}</td></tr>`).join("");
  return htmlPage("Public holidays", `<section class="panel"><h1>Public holidays</h1><form method="post" class="inline-form">${input("Date", "holiday_date", "date")}${input("Name", "name")}<button>Add</button></form><form method="post" action="/admin/off-in-lieu/generate"><button>Generate Saturday off-in-lieu credits</button></form><table><thead><tr><th>Date</th><th>Name</th></tr></thead><tbody>${rows}</tbody></table></section>`, user);
}

async function addHoliday(request, env, user) {
  const data = await request.formData();
  await env.DB.prepare("INSERT OR REPLACE INTO public_holidays (holiday_date, name) VALUES (?, ?)").bind(data.get("holiday_date"), data.get("name")).run();
  await audit(env, user.id, "public_holiday_added", null, { date: data.get("holiday_date"), name: data.get("name") }, "Public holiday saved.");
  return redirect("/admin/holidays");
}

async function generateOffInLieu(env, user) {
  const employees = await env.DB.prepare("SELECT * FROM employees WHERE status = 'active'").all();
  for (const e of employees.results) await generateCreditsForEmployee(env, user.id, e.id);
  return redirect("/admin/holidays");
}

async function generateCreditsForEmployee(env, userId, employeeId) {
  const employee = await env.DB.prepare("SELECT * FROM employees WHERE id = ?").bind(employeeId).first();
  const comp = await setting(env, "saturday_ph_compensation_method", "off_in_lieu");
  const holidays = await env.DB.prepare("SELECT * FROM public_holidays").all();
  for (const h of holidays.results) {
    if (!qualifiesForSaturdayOffInLieu({ publicHolidayDate: h.holiday_date, workPattern: employee.work_pattern, customWorkDays: employee.custom_work_days || "", compensationMethod: comp })) continue;
    const exists = await env.DB.prepare("SELECT id FROM off_in_lieu_credits WHERE employee_id = ? AND public_holiday_date = ? AND status != 'cancelled'").bind(employeeId, h.holiday_date).first();
    if (exists) continue;
    const expiry = formatDate(addMonths(parseDate(h.holiday_date), Number(await setting(env, "off_in_lieu_default_expiry_months", "12"))));
    await env.DB.prepare("INSERT INTO off_in_lieu_credits (employee_id, public_holiday_name, public_holiday_date, credit_date, credit_amount_days, used_amount_days, expiry_date, status, notes, created_by) VALUES (?, ?, ?, ?, 1, 0, ?, 'active', 'Auto-generated for Saturday public holiday', ?)")
      .bind(employeeId, h.name, h.holiday_date, h.holiday_date, expiry, userId).run();
    await audit(env, userId, "off_in_lieu_credit_added", null, { employee_id: employeeId, holiday: h.name, date: h.holiday_date }, "Saturday public holiday off-in-lieu generated.");
  }
}

async function employeeBalance(env, employeeId, year) {
  const employee = await env.DB.prepare("SELECT * FROM employees WHERE id = ?").bind(employeeId).first();
  const enforce = (await setting(env, "enforce_mom_three_month_rule", "yes")) === "yes";
  const calc = calculateAnnualLeaveEntitlement({ joinDate: employee.join_date, calculationYear: year, annualEntitlementDays: employee.annual_entitlement, enforceThreeMonthRule: enforce, companyOverride: Boolean(employee.mom_eligibility_override) });
  const annualTaken = await sum(env, "SELECT COALESCE(SUM(days),0) AS total FROM leave_requests WHERE employee_id = ? AND balance_category = 'annual' AND status = 'approved' AND substr(start_date,1,4) = ?", [employeeId, String(year)]);
  const pendingAnnual = await sum(env, "SELECT COALESCE(SUM(days),0) AS total FROM leave_requests WHERE employee_id = ? AND balance_category = 'annual' AND status = 'pending' AND substr(start_date,1,4) = ?", [employeeId, String(year)]);
  const adjustments = await sum(env, "SELECT COALESCE(SUM(adjustment_days),0) AS total FROM leave_balances WHERE employee_id = ? AND balance_category = 'annual' AND year = ?", [employeeId, year]);
  const oil = await sum(env, "SELECT COALESCE(SUM(credit_amount_days - used_amount_days),0) AS total FROM off_in_lieu_credits WHERE employee_id = ? AND status = 'active' AND expiry_date >= date('now')", [employeeId]);
  const pendingOil = await sum(env, "SELECT COALESCE(SUM(days),0) AS total FROM leave_requests WHERE employee_id = ? AND balance_category = 'off_in_lieu' AND status = 'pending'", [employeeId]);
  return { completedMonths: calc.completedMonths, annualEntitlement: calc.payableEntitlement, annualBalance: round(calc.payableEntitlement + adjustments - annualTaken), pendingAnnual, oilBalance: round(oil), pendingOil, explanation: calc.explanation };
}

async function ensureSufficientBalance(env, employeeId, category, days) {
  if (category === "unpaid") return;
  const b = await employeeBalance(env, employeeId, new Date().getUTCFullYear());
  const available = category === "off_in_lieu" ? b.oilBalance : category === "annual" ? b.annualBalance : 9999;
  if (available < days) throw new Error(`Insufficient ${category.replaceAll("_", " ")} balance.`);
}

async function consumeOil(env, employeeId, days) {
  let remaining = days;
  const credits = await env.DB.prepare("SELECT * FROM off_in_lieu_credits WHERE employee_id = ? AND status = 'active' AND expiry_date >= date('now') ORDER BY expiry_date").bind(employeeId).all();
  for (const c of credits.results) {
    if (remaining <= 0) break;
    const available = c.credit_amount_days - c.used_amount_days;
    const use = Math.min(available, remaining);
    const newUsed = c.used_amount_days + use;
    await env.DB.prepare("UPDATE off_in_lieu_credits SET used_amount_days = ?, status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?").bind(newUsed, newUsed >= c.credit_amount_days ? "used" : "active", c.id).run();
    remaining -= use;
  }
}

async function attachmentDownload(env, user, requestId) {
  const r = await env.DB.prepare("SELECT * FROM leave_requests WHERE id = ?").bind(requestId).first();
  if (!r || !r.attachment_key) return htmlPage("Not found", `<p class="error">Attachment not found.</p>`, user, 404);
  if (user.role !== "admin" && r.employee_id !== user.employee_id && r.approver_user_id !== user.id) return htmlPage("Forbidden", `<p class="error">You cannot view this attachment.</p>`, user, 403);
  const object = await env.ATTACHMENTS.get(r.attachment_key);
  if (!object) return htmlPage("Not found", `<p class="error">Attachment file not found in R2.</p>`, user, 404);
  return new Response(object.body, { headers: { "content-type": r.attachment_type || "application/octet-stream", "content-disposition": `attachment; filename="${safeName(r.attachment_filename || "attachment")}"` } });
}

async function exportEmployees(env) {
  const rows = await env.DB.prepare("SELECT id, name, email, department, job_title, join_date, annual_entitlement, work_pattern, status FROM employees ORDER BY name").all();
  return csvResponse("employees.csv", rows.results);
}
async function exportLeaveRequests(env) {
  const rows = await env.DB.prepare("SELECT * FROM leave_requests ORDER BY created_at DESC").all();
  return csvResponse("leave-requests.csv", rows.results);
}
async function exportAuditLog(env) {
  const rows = await env.DB.prepare("SELECT * FROM audit_log ORDER BY created_at DESC").all();
  return csvResponse("audit-log.csv", rows.results);
}

async function publicHolidaySet(env, startDate, endDate) {
  const rows = await env.DB.prepare("SELECT holiday_date FROM public_holidays WHERE holiday_date BETWEEN ? AND ?").bind(startDate, endDate).all();
  return new Set(rows.results.map((r) => r.holiday_date));
}

async function setting(env, key, fallback) {
  const row = await env.DB.prepare("SELECT value FROM settings WHERE key = ?").bind(key).first();
  return row?.value ?? fallback;
}

async function sum(env, sql, values) {
  const stmt = env.DB.prepare(sql).bind(...values);
  return Number((await stmt.first())?.total || 0);
}

async function audit(env, userId, actionType, beforeValue, afterValue, notes = "") {
  await env.DB.prepare("INSERT INTO audit_log (user_id, action_type, before_value, after_value, notes) VALUES (?, ?, ?, ?, ?)")
    .bind(userId || null, actionType, beforeValue ? JSON.stringify(beforeValue) : null, afterValue ? JSON.stringify(afterValue) : null, notes).run();
}

async function notifyAdmins(env, title, message) {
  const admins = await env.DB.prepare("SELECT id FROM users WHERE role = 'admin' AND active = 1").all();
  for (const a of admins.results) await env.DB.prepare("INSERT INTO notifications (user_id, title, message, link) VALUES (?, ?, ?, '/admin')").bind(a.id, title, message).run();
}

async function notifyApprover(env, user, requestId) {
  if (user.approver_user_id) {
    await env.DB.prepare("INSERT INTO notifications (user_id, title, message, link) VALUES (?, 'New leave request submitted', ?, '/manager')").bind(user.approver_user_id, `${user.name} submitted leave request #${requestId}`).run();
  } else {
    await notifyAdmins(env, "New leave request submitted", `${user.name} submitted leave request #${requestId}`);
  }
}

function requireAdmin(user, fn) {
  if (user.role !== "admin") return htmlPage("Forbidden", `<p class="error">Admin access required.</p>`, user, 403);
  return fn();
}
function requireManager(user, fn) {
  if (user.role !== "admin" && user.role !== "manager") return htmlPage("Forbidden", `<p class="error">Approver access required.</p>`, user, 403);
  return fn();
}

async function hashPassword(password) {
  const salt = crypto.getRandomValues(new Uint8Array(16));
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt, iterations: 100000 }, key, 256);
  return `pbkdf2_sha256$100000$${hex(salt)}$${hex(new Uint8Array(bits))}`;
}

async function verifyPassword(password, stored) {
  const [scheme, iterations, saltHex, expectedHex] = String(stored).split("$");
  if (scheme !== "pbkdf2_sha256") return false;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(password), "PBKDF2", false, ["deriveBits"]);
  const bits = await crypto.subtle.deriveBits({ name: "PBKDF2", hash: "SHA-256", salt: fromHex(saltHex), iterations: Number(iterations) }, key, 256);
  return hex(new Uint8Array(bits)) === expectedHex;
}

function htmlPage(title, body, user = null, statusCode = 200) {
  const nav = user ? `<nav><a href="/">My leave</a>${user.role === "admin" ? `<a href="/admin">Admin</a>` : ""}${user.role === "manager" || user.role === "admin" ? `<a href="/manager">Team</a>` : ""}<a href="/logout">Logout</a></nav>` : "";
  return new Response(`<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>${escapeHtml(title)}</title><link rel="stylesheet" href="/style.css"></head><body><main>${nav}${body}</main></body></html>`, { status: statusCode, headers: { "content-type": "text/html; charset=utf-8" } });
}

function input(label, name, type = "text", value = "", required = true) {
  return `<label>${escapeHtml(label)}<input name="${name}" type="${type}" value="${escapeHtml(value)}" ${required ? "required" : ""}></label>`;
}
function redirect(location, headers = {}) {
  return new Response("", { status: 303, headers: { location, ...headers } });
}
function cookie(request, name) {
  return Object.fromEntries((request.headers.get("cookie") || "").split(";").map((p) => p.trim().split("=")).filter((p) => p.length === 2))[name];
}
function idFromPath(path) {
  return Number(path.split("/").filter(Boolean).find((x) => /^\d+$/.test(x)));
}
function nullableInt(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}
function cryptoRandom() {
  return hex(crypto.getRandomValues(new Uint8Array(24)));
}
function hex(bytes) {
  return [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}
function fromHex(value) {
  const bytes = new Uint8Array(value.length / 2);
  for (let i = 0; i < bytes.length; i += 1) bytes[i] = parseInt(value.slice(i * 2, i * 2 + 2), 16);
  return bytes;
}
function safeName(name) {
  return String(name || "attachment").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 120);
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function status(value) {
  return `<span class="status ${escapeHtml(value)}">${escapeHtml(value)}</span>`;
}
function emptyRow(cols) {
  return `<tr><td colspan="${cols}">No records yet.</td></tr>`;
}
function round(value) {
  return Math.round(Number(value) * 100) / 100;
}
function csvResponse(filename, rows) {
  const headers = rows[0] ? Object.keys(rows[0]) : ["empty"];
  const csv = "\ufeff" + [headers.join(","), ...rows.map((row) => headers.map((h) => `"${String(row[h] ?? "").replaceAll('"', '""')}"`).join(","))].join("\n");
  return new Response(csv, { headers: { "content-type": "text/csv; charset=utf-8", "content-disposition": `attachment; filename="${filename}"` } });
}
function addMonths(date, months) {
  const copy = new Date(date);
  copy.setUTCMonth(copy.getUTCMonth() + months);
  return copy;
}
function css() {
  return `body{margin:0;font-family:Inter,Arial,sans-serif;background:#f6f7f9;color:#17202a}main{max-width:1180px;margin:auto;padding:20px}nav{display:flex;gap:14px;justify-content:flex-end;margin-bottom:16px}a{color:#0b5cad}h1,h2{margin:0 0 12px}.panel,.auth{background:white;border:1px solid #e2e6ea;border-radius:8px;padding:18px;margin-bottom:16px}.auth{max-width:460px;margin:48px auto}.page-head{display:flex;justify-content:space-between;gap:16px;align-items:flex-start;margin-bottom:16px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;margin-bottom:16px}.cards div{background:white;border:1px solid #e2e6ea;border-radius:8px;padding:16px}.cards strong{display:block;font-size:28px}.cards span,small{display:block;color:#5f6b76}.form-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.inline-form,.actions{display:flex;gap:8px;flex-wrap:wrap}label{display:grid;gap:6px;font-weight:600}input,select,textarea{padding:10px;border:1px solid #cfd6dd;border-radius:6px;font:inherit}button,.button{display:inline-block;background:#1f6feb;color:white;border:0;border-radius:6px;padding:10px 12px;text-decoration:none;cursor:pointer}.ghost{background:#eef3f8;color:#1f2933}.danger{background:#b42318}table{width:100%;border-collapse:collapse}th,td{padding:10px;border-bottom:1px solid #e6e8eb;text-align:left;vertical-align:top}.status{padding:3px 8px;border-radius:999px;background:#eef3f8}.approved{background:#dff6dd}.rejected,.cancelled{background:#fde2df}.pending{background:#fff2cc}.error{color:#b42318}@media(max-width:760px){main{padding:12px}.page-head{display:block}table{font-size:14px;display:block;overflow-x:auto}.actions form{display:inline}}`;
}
