const API = ""; // same origin
const state = { token: localStorage.getItem("rdm_token"), user: null, lastLocation: null };
const STATUS_OPTIONS = ["Submitted", "Under Review", "Assigned", "In Progress",
  "Repair Completed", "Verified", "Closed", "Rejected", "Duplicate"];

// ------------------------------------------------------------- helpers ----
function toast(msg, type = "") {
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = msg;
  document.getElementById("toast-container").appendChild(el);
  setTimeout(() => el.remove(), 3500);
}

async function api(path, options = {}) {
  const headers = options.headers || {};
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(API + path, { ...options, headers });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function severityBadge(sev) {
  return `<span class="badge badge-${sev}">${sev}</span>`;
}

function fmtDate(s) {
  if (!s) return "";
  return new Date(s.replace(" ", "T") + "Z").toLocaleString();
}

// -------------------------------------------------------------- auth UI ---
document.querySelectorAll(".tab-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("login-form").classList.toggle("hidden", btn.dataset.tab !== "login");
    document.getElementById("register-form").classList.toggle("hidden", btn.dataset.tab !== "register");
  });
});

document.getElementById("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const email = document.getElementById("login-email").value;
  const password = document.getElementById("login-password").value;
  await doLogin(email, password);
});

document.getElementById("register-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("reg-name").value;
  const email = document.getElementById("reg-email").value;
  const password = document.getElementById("reg-password").value;
  try {
    const data = await api("/api/auth/register", { method: "POST", body: JSON.stringify({ name, email, password }) });
    setSession(data.token, data.user);
  } catch (err) {
    document.getElementById("auth-error").textContent = err.message;
  }
});

document.querySelectorAll(".demo-btn").forEach(btn => {
  btn.addEventListener("click", () => doLogin(btn.dataset.email, btn.dataset.pass));
});

async function doLogin(email, password) {
  try {
    const data = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
    setSession(data.token, data.user);
  } catch (err) {
    document.getElementById("auth-error").textContent = err.message;
  }
}

function setSession(token, user) {
  state.token = token;
  state.user = user;
  localStorage.setItem("rdm_token", token);
  document.getElementById("auth-error").textContent = "";
  boot();
}

document.getElementById("logout-btn").addEventListener("click", () => {
  localStorage.removeItem("rdm_token");
  location.reload();
});

// -------------------------------------------------------------- boot ------
async function boot() {
  if (!state.token) return;
  try {
    state.user = await api("/api/auth/me");
  } catch {
    localStorage.removeItem("rdm_token");
    return;
  }
  document.getElementById("auth-screen").classList.add("hidden");
  document.getElementById("app-shell").classList.remove("hidden");
  document.getElementById("user-badge").textContent = `${state.user.name} · ${state.user.role}`;

  document.getElementById("citizen-view").classList.toggle("hidden", state.user.role !== "citizen");
  document.getElementById("authority-view").classList.toggle("hidden", state.user.role !== "authority");
  document.getElementById("admin-view").classList.toggle("hidden", state.user.role !== "admin");

  if (state.user.role === "citizen") await loadCitizenView();
  if (state.user.role === "authority") await loadAuthorityView();
  if (state.user.role === "admin") await loadAdminView();
}

// ============================================================ CITIZEN ====
async function loadCitizenView() {
  const complaints = await api("/api/complaints");
  const active = complaints.filter(c => !["Closed", "Rejected", "Duplicate"].includes(c.status)).length;
  const resolved = complaints.filter(c => c.status === "Closed").length;
  document.getElementById("citizen-stats").innerHTML = `
    ${statCard(complaints.length, "Total Reports")}
    ${statCard(active, "Active Complaints")}
    ${statCard(resolved, "Resolved Complaints")}
  `;
  renderComplaintList("citizen-complaints", complaints, false);
  renderMap("citizen-map", await api("/api/map/damages"));
}

function statCard(num, label) {
  return `<div class="stat-card"><div class="num">${num}</div><div class="label">${label}</div></div>`;
}

document.getElementById("capture-location").addEventListener("click", () => {
  if (!navigator.geolocation) return toast("Geolocation not supported by this browser", "error");
  navigator.geolocation.getCurrentPosition(
    pos => {
      state.lastLocation = { lat: pos.coords.latitude, lng: pos.coords.longitude };
      document.getElementById("location-display").textContent =
        `📍 ${pos.coords.latitude.toFixed(4)}, ${pos.coords.longitude.toFixed(4)}`;
    },
    () => toast("Could not get location — you can still submit without it", "error")
  );
});

document.getElementById("report-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const fileInput = document.getElementById("report-image");
  if (!fileInput.files[0]) return;
  const fd = new FormData();
  fd.append("image", fileInput.files[0]);
  if (state.lastLocation) {
    fd.append("latitude", state.lastLocation.lat);
    fd.append("longitude", state.lastLocation.lng);
  }
  const resultBox = document.getElementById("report-result");
  resultBox.innerHTML = `<div class="empty-state">Analyzing image…</div>`;
  try {
    const data = await api("/api/reports", { method: "POST", body: fd });
    renderReportResult(data);
    await loadCitizenView();
  } catch (err) {
    resultBox.innerHTML = `<div class="empty-state">${err.message}</div>`;
  }
});

function renderReportResult(data) {
  const box = document.getElementById("report-result");
  if (data.needs_manual_review) {
    box.innerHTML = `<div class="result-card">
      <strong>⚠️ Low-confidence detection</strong>
      <p>${data.message}</p>
    </div>`;
    return;
  }
  if (data.duplicate) {
    box.innerHTML = `<div class="result-card">
      <strong>Possible duplicate found</strong>
      <p>${data.duplicate.message}</p>
      <button onclick="upvoteComplaint(${data.duplicate.existing_complaint_id})">👍 Upvote existing (${data.duplicate.complaint_ref})</button>
    </div>`;
    return;
  }
  const d = data.detections[0];
  box.innerHTML = `<div class="result-card">
    <strong>✅ Complaint created: ${data.complaint.complaint_ref}</strong>
    <p>${d.damage_type.replace("_", " ")} — ${severityBadge(d.severity)} (confidence ${(d.confidence * 100).toFixed(0)}%)</p>
    <p>Routed to: ${data.complaint.authority_name || "Unassigned (no matching authority on file)"}</p>
  </div>`;
}

async function upvoteComplaint(id) {
  try {
    await api(`/api/complaints/${id}/upvote`, { method: "POST" });
    toast("Upvoted!", "success");
    await loadCitizenView();
  } catch (err) {
    toast(err.message, "error");
  }
}

// =========================================================== AUTHORITY ===
async function loadAuthorityView() {
  const statusFilter = document.getElementById("filter-status");
  if (statusFilter.options.length <= 1) {
    STATUS_OPTIONS.forEach(s => statusFilter.innerHTML += `<option value="${s}">${s}</option>`);
  }
  await refreshAuthorityComplaints();
  renderMap("authority-map", await api("/api/map/damages"));
  await renderAnalytics();
}

async function refreshAuthorityComplaints() {
  const status = document.getElementById("filter-status").value;
  const severity = document.getElementById("filter-severity").value;
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (severity) params.set("severity", severity);
  const complaints = await api(`/api/complaints?${params.toString()}`);

  const pending = complaints.filter(c => c.status === "Submitted").length;
  const highPriority = complaints.filter(c => ["high", "critical"].includes(c.severity)).length;
  const inProgress = complaints.filter(c => c.status === "In Progress").length;
  const resolved = complaints.filter(c => c.status === "Closed").length;
  document.getElementById("authority-stats").innerHTML = `
    ${statCard(complaints.length, "Total Complaints")}
    ${statCard(pending, "Pending")}
    ${statCard(highPriority, "High Priority")}
    ${statCard(inProgress, "In Progress")}
    ${statCard(resolved, "Resolved")}
  `;
  renderComplaintList("authority-complaints", complaints, true);
}

document.getElementById("apply-filters").addEventListener("click", refreshAuthorityComplaints);

let charts = {};
async function renderAnalytics() {
  const a = await api("/api/analytics");
  drawChart("chart-status", "bar", a.by_status.map(r => r.status), a.by_status.map(r => r.count), "By Status");
  drawChart("chart-severity", "doughnut", a.by_severity.map(r => r.severity), a.by_severity.map(r => r.count), "By Severity");
  drawChart("chart-type", "bar", a.by_type.map(r => r.damage_type), a.by_type.map(r => r.count), "By Damage Type");
  drawChart("chart-month", "line", a.by_month.map(r => r.month), a.by_month.map(r => r.count), "By Month");
}

function drawChart(canvasId, type, labels, data, label) {
  const ctx = document.getElementById(canvasId);
  if (charts[canvasId]) charts[canvasId].destroy();
  charts[canvasId] = new Chart(ctx, {
    type, data: { labels, datasets: [{ label, data, backgroundColor: ["#0f766e", "#2563eb", "#f97316", "#eab308", "#ef4444", "#22c55e"] }] },
    options: { responsive: true, plugins: { legend: { display: type === "doughnut" } } },
  });
}

// -------------------------------------------------------- shared render ---
function renderComplaintList(containerId, complaints, isAuthority) {
  const el = document.getElementById(containerId);
  if (!complaints.length) {
    el.innerHTML = `<div class="empty-state">No complaints yet.</div>`;
    return;
  }
  el.innerHTML = complaints.map(c => `
    <div class="complaint-card">
      <div class="complaint-main">
        <div class="complaint-ref">${c.complaint_ref} ${severityBadge(c.severity)} <span class="status-pill">${c.status}</span></div>
        <div class="complaint-meta">${(c.damage_type || "").replace("_", " ")} · ${c.city || c.address || "location unknown"} · ${fmtDate(c.created_at)}</div>
        ${c.authority_name ? `<div class="complaint-meta">Authority: ${c.authority_name}</div>` : ""}
        ${c.upvote_count !== undefined ? `<div class="complaint-meta">👍 ${c.upvote_count} upvotes</div>` : ""}
      </div>
      <div class="complaint-actions">
        ${!isAuthority ? `<button class="btn-secondary" onclick="upvoteComplaint(${c.id})">👍 Upvote</button>` : ""}
        ${isAuthority ? `<button onclick="openStatusModal(${c.id}, '${c.status}')">Update Status</button>` : ""}
      </div>
    </div>
  `).join("");
}

let maps = {};
function renderMap(containerId, markers) {
  const el = document.getElementById(containerId);
  if (maps[containerId]) { maps[containerId].remove(); }
  const map = L.map(containerId).setView([20.5937, 78.9629], 4.5);
  maps[containerId] = map;
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const colors = { low: "#22c55e", medium: "#eab308", high: "#f97316", critical: "#ef4444" };
  markers.forEach(m => {
    if (!m.latitude || !m.longitude) return;
    L.circleMarker([m.latitude, m.longitude], {
      radius: 8, fillColor: colors[m.severity] || "#64748b", color: "#fff", weight: 1, fillOpacity: 0.9,
    }).addTo(map).bindPopup(`<strong>${m.complaint_ref}</strong><br>${(m.damage_type || "").replace("_", " ")}<br>${m.severity} · ${m.status}`);
  });
  if (markers.length) {
    const valid = markers.filter(m => m.latitude && m.longitude);
    if (valid.length) map.fitBounds(valid.map(m => [m.latitude, m.longitude]), { maxZoom: 12 });
  }
}

// ----------------------------------------------------------- status modal -
let modalComplaintId = null;
function openStatusModal(id, currentStatus) {
  modalComplaintId = id;
  const sel = document.getElementById("modal-status");
  sel.innerHTML = STATUS_OPTIONS.map(s => `<option value="${s}" ${s === currentStatus ? "selected" : ""}>${s}</option>`).join("");
  document.getElementById("modal-remarks").value = "";
  document.getElementById("status-modal").classList.remove("hidden");
}
document.getElementById("modal-cancel").addEventListener("click", () => {
  document.getElementById("status-modal").classList.add("hidden");
});
document.getElementById("modal-save").addEventListener("click", async () => {
  const status = document.getElementById("modal-status").value;
  const remarks = document.getElementById("modal-remarks").value;
  try {
    await api(`/api/complaints/${modalComplaintId}/status`, { method: "PATCH", body: JSON.stringify({ status, remarks }) });
    document.getElementById("status-modal").classList.add("hidden");
    toast("Status updated", "success");
    await refreshAuthorityComplaints();
    await renderAnalytics();
  } catch (err) {
    toast(err.message, "error");
  }
});

// =============================================================== ADMIN ===
async function loadAdminView() {
  const [authorities, users, analytics] = await Promise.all([
    api("/api/authorities"), api("/api/users"), api("/api/analytics"),
  ]);
  document.getElementById("admin-stats").innerHTML = `
    ${statCard(analytics.totals.total, "Total Complaints")}
    ${statCard(authorities.length, "Authorities")}
    ${statCard(users.length, "Users")}
    ${statCard(analytics.avg_resolution_days ?? "—", "Avg Resolution (days)")}
  `;
  document.querySelector("#authorities-table tbody").innerHTML = authorities.map(a =>
    `<tr><td>${a.name}</td><td>${a.city}</td><td>${a.category}</td></tr>`).join("");
  document.querySelector("#users-table tbody").innerHTML = users.map(u =>
    `<tr><td>${u.name}</td><td>${u.email}</td><td>${u.role}</td></tr>`).join("");
}

document.getElementById("authority-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = document.getElementById("new-auth-name").value;
  const city = document.getElementById("new-auth-city").value;
  const category = document.getElementById("new-auth-category").value;
  try {
    await api("/api/authorities", { method: "POST", body: JSON.stringify({ name, city, category: category || undefined }) });
    e.target.reset();
    toast("Authority added", "success");
    await loadAdminView();
  } catch (err) {
    toast(err.message, "error");
  }
});

// --------------------------------------------------------------- init -----
boot();
