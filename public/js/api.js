async function api(path, options) {
  const opts = Object.assign({ credentials: "same-origin" }, options || {});
  if (opts.body && typeof opts.body !== "string") {
    opts.body = JSON.stringify(opts.body);
    opts.headers = Object.assign({ "Content-Type": "application/json" }, opts.headers || {});
  }
  const res = await fetch(path, opts);
  if (res.status === 401) {
    window.location.href = "/login.html";
    throw new Error("unauthorized");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `request failed (${res.status})`);
  }
  return data;
}

function renderNav(active) {
  const links = [
    ["/index.html", "Dashboard"],
    ["/sequences.html", "Sequences"],
    ["/accounts.html", "Accounts"],
    ["/hubspot.html", "HubSpot"],
  ];
  const el = document.getElementById("nav");
  if (!el) return;
  el.innerHTML =
    '<div class="brand">Cold Email Sequencer</div>' +
    links
      .map(
        ([href, label]) =>
          `<a href="${href}"${href === active ? ' class="active"' : ""}>${label}</a>`
      )
      .join("") +
    '<button id="logout-btn">Log out</button>';
  document.getElementById("logout-btn").addEventListener("click", async () => {
    await api("/api/auth/logout", { method: "POST" }).catch(() => {});
    window.location.href = "/login.html";
  });
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
