function authToken() {
  return localStorage.getItem("auth_token") || "";
}

async function api(path, options) {
  const opts = Object.assign({ credentials: "same-origin" }, options || {});
  opts.headers = Object.assign({ "X-Auth-Token": authToken() }, opts.headers || {});
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.location.href = "/login.html";
    throw new Error("unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const error = new Error(data.error || `request failed (${res.status})`);
    error.data = data;
    throw error;
  }
  return data;
}

function applyStoredTheme() {
  document.body.classList.toggle("light-mode", localStorage.getItem("theme") === "light");
}

function renderNav(active) {
  const links = [
    ["/index.html", "Dashboard"],
    ["/sequences.html", "Sequences"],
    ["/accounts.html", "Accounts"],
    ["/hubspot.html", "HubSpot"],
    ["/suppression.html", "Suppression"],
  ];
  const el = document.getElementById("sidebar");
  if (!el) return;
  el.innerHTML = `
    <div class="sidebar-brand">
      <div class="brand-s">S</div>
      <div class="brand-name">Cold Email<br>Sequencer</div>
    </div>
    <nav class="sidebar-nav">
      ${links
        .map(
          ([href, label]) =>
            `<a href="${href}" class="nav-item${href === active ? " active" : ""}">${label}</a>`
        )
        .join("")}
    </nav>
    <div class="sidebar-user muted" style="font-size:0.75rem; padding:0 0.5rem 0.5rem; word-break:break-all">
      ${escapeHtml(localStorage.getItem("user_email") || "")}
    </div>
    <div class="sidebar-footer">
      <label class="toggle" title="Toggle light/dark mode">
        <input type="checkbox" id="theme-toggle">
        <span class="toggle-slider"></span>
      </label>
      <button id="logout-btn" class="nav-item" type="button">Log out</button>
    </div>`;

  document.getElementById("logout-btn").addEventListener("click", async () => {
    const token = authToken();
    localStorage.removeItem("auth_token");
    localStorage.removeItem("user_email");
    if (token) {
      // Best-effort - the local token is already cleared either way, so a
      // failed request here just leaves an unused session to expire on its own.
      fetch("/api/auth/logout", { method: "POST", headers: { "X-Auth-Token": token } }).catch(() => {});
    }
    window.location.href = "/login.html";
  });

  const themeToggle = document.getElementById("theme-toggle");
  themeToggle.checked = document.body.classList.contains("light-mode");
  themeToggle.addEventListener("change", () => {
    document.body.classList.toggle("light-mode", themeToggle.checked);
    localStorage.setItem("theme", themeToggle.checked ? "light" : "dark");
  });

  setUpMobileNav(el);
}

// Below a breakpoint the sidebar becomes an off-canvas panel opened by a
// hamburger button in a topbar - injected here so every page picks it up
// from this one shared renderNav() call instead of duplicating markup.
function setUpMobileNav(sidebar) {
  const topbar = document.createElement("div");
  topbar.className = "mobile-topbar";
  topbar.innerHTML = `
    <button class="hamburger-btn" id="hamburger-btn" aria-label="Open menu" type="button">&#9776;</button>
    <div class="brand-s">S</div>
    <div class="brand-name">Cold Email Sequencer</div>`;
  document.body.insertBefore(topbar, document.body.firstChild);

  const backdrop = document.createElement("div");
  backdrop.className = "sidebar-backdrop";
  document.body.appendChild(backdrop);

  function closeSidebar() {
    sidebar.classList.remove("open");
    backdrop.classList.remove("open");
  }
  topbar.querySelector("#hamburger-btn").addEventListener("click", () => {
    sidebar.classList.add("open");
    backdrop.classList.add("open");
  });
  backdrop.addEventListener("click", closeSidebar);
  sidebar.querySelectorAll(".nav-item").forEach((a) => a.addEventListener("click", closeSidebar));
}

function escapeHtml(str) {
  return String(str == null ? "" : str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function fmtUnix(seconds) {
  if (!seconds && seconds !== 0) return "";
  const d = new Date(seconds * 1000);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString();
}
