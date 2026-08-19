"use strict";

/* ---------- state ---------- */

let servers = [];
let selectedServerId = null;
let eventSource = null;
let autoScroll = true;
let pollTimer = null;

/* ---------- dom helpers ---------- */

const $ = (id) => document.getElementById(id);

function escapeHtml(text) {
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function toast(text, kind = "") {
  const el = document.createElement("div");
  el.className = "toast " + kind;
  el.textContent = text;
  $("toasts").appendChild(el);
  setTimeout(() => el.remove(), 5000);
}

/* ---------- screens ---------- */

function showLogin() {
  stopPolling();
  closeStream();
  $("app").classList.add("hidden");
  $("login").classList.remove("hidden");
  $("token").focus();
}

function enterApp() {
  $("login").classList.add("hidden");
  $("app").classList.remove("hidden");
  startPolling();
  loadServers();
}

/* ---------- api ---------- */

function describeError(data, status, fallback) {
  if (data && data.error) {
    if (typeof data.error === "string") return data.error;
    if (data.error.kind === "port-conflict") {
      return data.error.port_type + " port " + data.error.port + " is already in use.";
    }
    if (data.error.kind === "mod-operation") {
      return "Mods are currently being installed or updated. Please wait.";
    }
    if (data.error.kind) return "Server error: " + data.error.kind;
  }
  return fallback || "Request failed (" + status + ")";
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let data = null;
  try {
    data = await res.json();
  } catch (e) {
    /* not json */
  }
  if (res.status === 401 && data && data.code === "auth") {
    showLogin();
    throw new Error("auth");
  }
  if (!res.ok) {
    throw new Error(describeError(data, res.status));
  }
  return data;
}

/* ---------- login / logout ---------- */

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const token = $("token").value.trim();
  $("login-error").classList.add("hidden");
  if (!token) return;
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    if (res.ok) {
      $("token").value = "";
      enterApp();
    } else {
      $("login-error").textContent = "Invalid access token.";
      $("login-error").classList.remove("hidden");
    }
  } catch (err) {
    $("login-error").textContent = "Could not reach the daemon.";
    $("login-error").classList.remove("hidden");
  }
});

$("logout-btn").addEventListener("click", async () => {
  try {
    await api("/api/auth/logout", { method: "POST" });
  } catch (e) {
    /* ignore */
  }
  showLogin();
});

/* ---------- server list ---------- */

async function loadServers() {
  try {
    const data = await api("/api/servers");
    servers = data.servers || [];
  } catch (e) {
    if (e.message !== "auth") return;
    return;
  }
  renderList();

  if (selectedServerId && !servers.some((s) => s.id === selectedServerId)) {
    selectServer(null);
  }
}

function renderList() {
  const list = $("server-list");
  list.textContent = "";
  if (servers.length === 0) {
    const empty = document.createElement("div");
    empty.className = "empty-state";
    empty.style.padding = "20px";
    empty.textContent = "No servers found.";
    list.appendChild(empty);
    return;
  }
  for (const s of servers) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "server-row" + (s.id === selectedServerId ? " selected" : "");
    row.innerHTML =
      '<span class="dot ' + escapeHtml(s.status) + '"></span>' +
      '<span class="name">' + escapeHtml(s.name) + "</span>" +
      '<span class="sub">' + playerSummary(s) + "</span>";
    row.addEventListener("click", () => selectServer(s.id));
    list.appendChild(row);
  }
}

function playerSummary(s) {
  if (s.status === "running") return s.player_count + "/" + s.max_players;
  return escapeHtml(s.status);
}

function startPolling() {
  stopPolling();
  pollTimer = setInterval(loadServers, 2000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

/* ---------- selecting a server ---------- */

function selectServer(id) {
  selectedServerId = id;
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
  const consoleEl = $("console");
  consoleEl.textContent = "";
  autoScroll = true;

  if (!id) {
    $("server-panel").classList.add("hidden");
    $("empty-state").classList.remove("hidden");
    return;
  }

  $("empty-state").classList.add("hidden");
  $("server-panel").classList.remove("hidden");
  $("command-input").focus();

  const s = servers.find((x) => x.id === id);
  if (s) {
    $("server-name").textContent = s.name;
    $("server-meta").textContent =
      s.mc_version + " \u00b7 " + s.ram_mb + " MB RAM" +
      (s.loader_version ? " \u00b7 loader " + s.loader_version : "");
    applyStatus(s);
  } else {
    $("server-name").textContent = "";
    $("server-meta").textContent = "";
  }

  connectStream(id);
}

function applyStatus(s) {
  const badge = $("status-badge");
  badge.className = "badge " + escapeHtml(s.status);
  badge.textContent = s.status;

  const isRunning = s.status === "running" || s.status === "starting";
  $("cmd-start").disabled = isRunning || s.status === "stopping";
  $("cmd-stop").disabled = !isRunning;

  const meta = $("server-meta");
  let text = meta.textContent;
  if (s.status === "running" && s.pid) {
    text += " \u00b7 pid " + s.pid;
  }
  if (s.status === "running") {
    text += " \u00b7 " + (s.player_count || 0) + "/" + (s.max_players || "?") + " players";
  }

  renderList();
}

/* ---------- console ---------- */

function lineClass(line) {
  if (line.startsWith("[Hosty]")) return "info";
  if (/WARN/.test(line)) return "warn";
  if (/ERROR|Exception/.test(line)) return "error";
  return "";
}

function appendLine(line) {
  const consoleEl = $("console");
  const el = document.createElement("span");
  el.className = "line " + lineClass(line);
  el.textContent = line;
  consoleEl.appendChild(el);

  if (autoScroll) {
    consoleEl.scrollTop = consoleEl.scrollHeight;
  }
}

$("console").addEventListener("scroll", () => {
  const el = $("console");
  const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
  if (nearBottom !== autoScroll) {
    autoScroll = nearBottom;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }
});

/* ---------- live stream (SSE) ---------- */

function connectStream(id) {
  const url = "/api/servers/" + encodeURIComponent(id) + "/stream";
  eventSource = new EventSource(url);

  eventSource.onopen = () => {
    /* fetch succeeds; auth cookie already validated by the daemon */
  };

  eventSource.onmessage = (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch (e) {
      return;
    }
    switch (msg.type) {
      case "init":
        applyStatus({
          status: msg.status,
          pid: msg.pid,
          player_count: msg.player_count,
          max_players: msg.max_players,
        });
        for (const line of msg.history || []) appendLine(line);
        break;
      case "output":
        appendLine(msg.line);
        break;
      case "status":
        applyStatus(msg);
        break;
    }
  };

  eventSource.onerror = () => {
    /* EventSource retries automatically; surface a one-time hint */
    if (eventSource && eventSource.readyState === EventSource.CLOSED) {
      toast("Lost connection to the daemon \u2014 retrying\u2026", "error");
    }
  };
}

function closeStream() {
  if (eventSource) {
    eventSource.close();
    eventSource = null;
  }
}

/* ---------- actions ---------- */

$("cmd-start").addEventListener("click", () => act("start"));
$("cmd-stop").addEventListener("click", () => act("stop"));
$("cmd-stop").addEventListener("click", () => {
  if (selectedServerId) act("stop");
});

async function act(action) {
  if (!selectedServerId) return;
  try {
    await api("/api/servers/" + encodeURIComponent(selectedServerId) + "/" + action, { method: "POST" });
    toast(action[0].toUpperCase() + action.slice(1) + " requested.", "success");
  } catch (e) {
    if (e.message !== "auth") toast(e.message, "error");
    return;
  }
  loadServers();
}

$("command-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("command-input");
  const command = input.value.trim();
  if (!command || !selectedServerId) return;

  input.value = "";
  appendLine("> " + command + "\n");

  try {
    await api("/api/servers/" + encodeURIComponent(selectedServerId) + "/command", {
      method: "POST",
      body: JSON.stringify({ command }),
    });
  } catch (err) {
    if (err.message !== "auth") toast(err.message, "error");
  }
});

/* ---------- init ---------- */

$("token").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("login-form").requestSubmit();
});

async function boot() {
  try {
    await api("/api/servers");
    enterApp();
  } catch (e) {
    showLogin();
  }
}

boot();