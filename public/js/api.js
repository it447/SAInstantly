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

// Wires a "+ Add link" button to insert a [text](url) tag into whichever
// textarea getTarget() currently points at - selected text becomes the link
// text, or "link" if nothing was selected. Rendered at send time as
// "text (url)" (see api/_lib/utils.py render_links) since emails here stay
// plain text - the URL is never hidden, just placed next to its label.
function wireLinkInsert(button, getTarget) {
  button.addEventListener("click", () => {
    const existing = document.querySelector(".link-insert-panel");
    if (existing) {
      existing.remove();
      return;
    }
    const textarea = getTarget();
    if (!textarea) return;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selectedText = textarea.value.slice(start, end);

    const panel = document.createElement("div");
    panel.className = "link-insert-panel";
    panel.innerHTML = `
      <input type="text" class="link-url-input" placeholder="https://example.com">
      <button type="button" class="btn link-insert-confirm">Insert</button>`;
    button.insertAdjacentElement("afterend", panel);
    const urlInput = panel.querySelector(".link-url-input");
    urlInput.focus();

    function close() {
      panel.remove();
    }
    function commit() {
      let url = urlInput.value.trim();
      if (!url) {
        close();
        return;
      }
      if (!/^https?:\/\//i.test(url)) url = `https://${url}`;
      const text = selectedText || "link";
      const tag = `[${text}](${url})`;
      textarea.value = textarea.value.slice(0, start) + tag + textarea.value.slice(end);
      const pos = start + tag.length;
      textarea.focus();
      textarea.setSelectionRange(pos, pos);
      close();
    }
    panel.querySelector(".link-insert-confirm").addEventListener("click", commit);
    urlInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        commit();
      }
      if (e.key === "Escape") close();
    });
    setTimeout(() => {
      document.addEventListener("click", function onDocClick(e) {
        if (!panel.contains(e.target) && e.target !== button) {
          panel.remove();
          document.removeEventListener("click", onDocClick);
        }
      });
    }, 0);
  });
}

// Wires a bold/italic/underline button to wrap the selection in whichever
// textarea getTarget() currently points at with `marker` on both sides
// (e.g. "**selected text**") - or insert `marker + placeholder + marker` and
// select the placeholder if nothing was selected, so typing replaces it
// immediately. Rendered at send time as real Unicode styled characters (see
// api/_lib/utils.py render_text_styles), since a plain-text email has no
// formatting layer to apply markup to.
function wireInlineStyle(button, getTarget, marker, placeholder) {
  button.addEventListener("click", () => {
    const textarea = getTarget();
    if (!textarea) return;
    const start = textarea.selectionStart;
    const end = textarea.selectionEnd;
    const selected = textarea.value.slice(start, end);
    const inner = selected || placeholder;
    const tag = `${marker}${inner}${marker}`;
    textarea.value = textarea.value.slice(0, start) + tag + textarea.value.slice(end);
    textarea.focus();
    if (selected) {
      const pos = start + tag.length;
      textarea.setSelectionRange(pos, pos);
    } else {
      textarea.setSelectionRange(start + marker.length, start + marker.length + inner.length);
    }
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

function fmtUnix(seconds) {
  if (!seconds && seconds !== 0) return "";
  const d = new Date(seconds * 1000);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString();
}
