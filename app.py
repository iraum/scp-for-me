#!/usr/bin/env python3.11
"""Two-pane file transfer UI. Left = this machine. Right = your browser's laptop.

Run on the server, SSH-forward the port, then open http://localhost:5555 in the
laptop browser:

    ssh -L 5555:localhost:5555 user@this-machine
"""
import getpass
import os
import socket
from pathlib import Path

from flask import (
    Flask,
    abort,
    jsonify,
    render_template_string,
    request,
    send_file,
)

app = Flask(__name__)
LISTEN_PORT = int(os.environ.get("PORT", "5555"))
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_GB", "64")) * 1024**3
SERVER_LABEL = f"{getpass.getuser()}@{socket.gethostname()}"


def fmt_size(n):
    n = float(n)
    for unit in ["B", "K", "M", "G", "T"]:
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


def sanitize_relpath(rel):
    """Return list of path parts, or None if the input is unsafe / empty."""
    rel = (rel or "").replace("\\", "/").lstrip("/")
    parts = [p for p in rel.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        return None
    return parts


@app.route("/")
def index():
    return render_template_string(HTML, server_label=SERVER_LABEL)


@app.route("/api/list")
def api_list():
    path = request.args.get("path", "")
    try:
        p = Path(path).expanduser().resolve() if path else Path.home()
        if not p.is_dir():
            return jsonify({"error": f"not a directory: {p}"}), 400
        entries = []
        for entry in p.iterdir():
            try:
                is_dir = entry.is_dir()
                size = entry.stat().st_size if not is_dir else 0
                entries.append({
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size": size,
                    "size_h": "" if is_dir else fmt_size(size),
                })
            except OSError:
                pass
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return jsonify({"path": str(p), "entries": entries})
    except PermissionError as e:
        return jsonify({"error": f"permission denied: {e}"}), 403
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/list-tree")
def api_list_tree():
    """Flatten a directory: [{relpath, size}, ...]. For recursive downloads."""
    path = request.args.get("path", "")
    if not path:
        return jsonify({"error": "missing path"}), 400
    try:
        root = Path(path).expanduser().resolve()
        if not root.is_dir():
            return jsonify({"error": f"not a directory: {root}"}), 400
        entries = []
        total = 0
        for dirpath, _dirnames, filenames in os.walk(root):
            for name in filenames:
                full = Path(dirpath) / name
                try:
                    size = full.stat().st_size
                except OSError:
                    continue
                rel = full.relative_to(root).as_posix()
                entries.append({"relpath": rel, "size": size})
                total += size
        return jsonify({"root": str(root), "entries": entries, "total": total})
    except PermissionError as e:
        return jsonify({"error": f"permission denied: {e}"}), 403
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/exists")
def api_exists():
    path = request.args.get("path", "")
    if not path:
        return jsonify({"exists": False, "is_dir": False})
    try:
        p = Path(path).expanduser().resolve()
        if p.exists():
            return jsonify({"exists": True, "is_dir": p.is_dir()})
    except Exception:
        pass
    return jsonify({"exists": False, "is_dir": False})


@app.route("/api/download")
def api_download():
    path = request.args.get("path", "")
    if not path:
        abort(400)
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        abort(404)
    return send_file(p, as_attachment=True, download_name=p.name)


@app.route("/api/upload-one", methods=["POST"])
def api_upload_one():
    """Single-file streaming upload. Body = raw bytes. Query: dir, name (may
    contain subpath, dirs created as needed), overwrite=0|1."""
    target = request.args.get("dir", "")
    name = request.args.get("name", "")
    overwrite = request.args.get("overwrite") == "1"
    if not target or not name:
        return jsonify({"error": "missing dir or name"}), 400
    try:
        base = Path(target).expanduser().resolve()
        if not base.is_dir():
            return jsonify({"error": f"not a directory: {base}"}), 400
        parts = sanitize_relpath(name)
        if parts is None:
            return jsonify({"error": "illegal path"}), 400
        dest = base.joinpath(*parts)
        try:
            dest.resolve().relative_to(base)
        except ValueError:
            return jsonify({"error": "path escape"}), 400
        if dest.exists() and not overwrite:
            return jsonify({"error": "exists", "relpath": "/".join(parts)}), 409
        if dest.exists() and dest.is_dir():
            return jsonify({"error": "destination is a directory"}), 400
        dest.parent.mkdir(parents=True, exist_ok=True)
        total = 0
        with open(dest, "wb") as out:
            while True:
                chunk = request.stream.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
        return jsonify({"ok": True, "saved": "/".join(parts), "bytes": total})
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>scp-for-me</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, system-ui, sans-serif; font-size: 16px;
         color: #222; background: #f5f5f5; display: flex; flex-direction: column; height: 100vh; }
  header { padding: 8px 14px; background: #2d3748; color: white; display: flex; align-items: center; gap: 12px; }
  header h1 { margin: 0; font-size: 17px; font-weight: 500; }
  header .hint { font-size: 14px; color: #a0aec0; }
  .panels { display: flex; flex: 1; overflow: hidden; gap: 1px; background: #cbd5e0; }
  .panel { flex: 1; display: flex; flex-direction: column; background: white; overflow: hidden; min-width: 0; }
  .panel h2 { margin: 0; padding: 6px 10px; font-size: 15px; font-weight: 600;
              background: #edf2f7; border-bottom: 1px solid #cbd5e0; display: flex; gap: 8px; align-items: baseline; }
  .panel h2 .addr { color: #718096; font-weight: 400; font-family: monospace; font-size: 14px; }
  .pathbar, .toolbar { display: flex; padding: 6px; gap: 4px; border-bottom: 1px solid #e2e8f0; align-items: center; }
  .toolbar { background: #fafbfc; }
  .pathbar button, .toolbar button {
    padding: 2px 10px; cursor: pointer; background: white;
    border: 1px solid #cbd5e0; border-radius: 3px; font-size: 15px;
  }
  .pathbar button:hover, .toolbar button:hover { background: #edf2f7; }
  .pathbar button:disabled, .toolbar button:disabled { opacity: 0.4; cursor: default; }
  .toolbar button.send { background: #4299e1; color: white; border-color: #3182ce; font-weight: 500; }
  .toolbar button.send:hover:not(:disabled) { background: #3182ce; }
  .toolbar button.send:disabled { background: #cbd5e0; color: #718096; border-color: #cbd5e0; }
  .pathbar input { flex: 1; padding: 3px 6px; font-family: monospace; font-size: 14px;
                   border: 1px solid #cbd5e0; border-radius: 3px; min-width: 0; }
  .pathbar .readonly-path { flex: 1; padding: 3px 6px; font-family: monospace; font-size: 14px;
                            background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 3px;
                            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .toolbar .sel-count { font-size: 14px; color: #718096; flex: 1; }
  .toolbar label { display: flex; align-items: center; gap: 5px; font-size: 14px; cursor: pointer; user-select: none; }

  .list { flex: 1; overflow-y: auto; font-family: monospace; font-size: 14px; position: relative; }
  .list.dragover { background: #ebf8ff; outline: 2px dashed #4299e1; outline-offset: -6px; }
  .row { display: flex; padding: 3px 10px; cursor: pointer; user-select: none; gap: 8px; align-items: center; }
  .row:hover { background: #ebf8ff; }
  .row.dir { color: #2b6cb0; font-weight: 500; }
  .row.selected { background: #bee3f8; }
  .row.selected:hover { background: #90cdf4; }
  .row input.sel { margin: 0; cursor: pointer; }
  .row .name { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .row .size { color: #718096; min-width: 60px; text-align: right; }
  .row.parent input.sel { visibility: hidden; }

  .empty { padding: 24px 16px; color: #718096; text-align: center; font-family: -apple-system, system-ui, sans-serif; }
  .empty button { margin-top: 8px; padding: 6px 14px; cursor: pointer; background: #4299e1; color: white;
                  border: none; border-radius: 3px; font-size: 15px; }
  .empty button:hover { background: #3182ce; }

  .progress-area { background: #f7fafc; border-top: 1px solid #cbd5e0; padding: 8px 14px; }
  .progress-area[hidden] { display: none; }
  .progress-row { display: flex; align-items: center; gap: 10px; font-size: 14px; margin-bottom: 4px; }
  .progress-row:last-child { margin-bottom: 0; }
  .progress-row .label { font-weight: 600; min-width: 78px; }
  .progress-row .text { flex: 1; font-family: monospace; color: #4a5568;
                        white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .progress-row .pct { min-width: 42px; text-align: right; font-family: monospace; color: #4a5568; }
  .progress-bar { flex: 2; height: 8px; background: #e2e8f0; border-radius: 4px; overflow: hidden; }
  .progress-bar .fill { height: 100%; background: #4299e1; width: 0%; transition: width 0.1s linear; }
  .progress-area button.cancel { padding: 2px 10px; background: white; border: 1px solid #fc8181;
                                 color: #c53030; cursor: pointer; border-radius: 3px; font-size: 14px; }
  .progress-area button.cancel:hover { background: #fff5f5; }

  .status { padding: 6px 12px; background: #1a202c; color: #cbd5e0;
            font-family: monospace; font-size: 14px; border-top: 1px solid #2d3748;
            max-height: 140px; overflow-y: auto; }
  .status div { padding: 1px 0; }
  .status .err { color: #fc8181; }
  .status .ok { color: #9ae6b4; }

  dialog { border: 1px solid #cbd5e0; border-radius: 6px; padding: 0; min-width: 380px; max-width: 540px;
           box-shadow: 0 10px 25px rgba(0,0,0,0.2); }
  dialog::backdrop { background: rgba(0,0,0,0.35); }
  dialog .dlg-head { padding: 12px 18px; border-bottom: 1px solid #e2e8f0; font-weight: 600; font-size: 16px; }
  dialog .dlg-body { padding: 14px 18px; font-size: 15px; color: #2d3748; word-break: break-word; }
  dialog .dlg-buttons { display: flex; gap: 6px; justify-content: flex-end;
                        padding: 10px 14px; border-top: 1px solid #e2e8f0; background: #f7fafc; flex-wrap: wrap; }
  dialog .dlg-buttons button { padding: 5px 12px; border: 1px solid #cbd5e0; background: white;
                               border-radius: 3px; cursor: pointer; font-size: 15px; }
  dialog .dlg-buttons button:hover { background: #edf2f7; }
  dialog .dlg-buttons button.primary { background: #4299e1; color: white; border-color: #3182ce; }
  dialog .dlg-buttons button.primary:hover { background: #3182ce; }
  dialog .dlg-buttons button.danger { background: #e53e3e; color: white; border-color: #c53030; }
  dialog .dlg-buttons button.danger:hover { background: #c53030; }
</style>
</head>
<body>
<header>
  <h1>scp-for-me</h1>
  <span class="hint">click a file/folder to transfer it • or check boxes and use → / ← buttons for multi-select</span>
  <label style="margin-left:auto; display:flex; align-items:center; gap:5px; font-size:14px; cursor:pointer; user-select:none;">
    <input type="checkbox" id="show-hidden" onchange="toggleHidden(this.checked)"> show hidden
  </label>
</header>

<div class="panels">
  <!-- SERVER (left) -->
  <div class="panel">
    <h2>Server <span class="addr">{{ server_label }}</span></h2>
    <div class="pathbar">
      <button onclick="serverUp()" title="parent directory">↑</button>
      <input id="server-path" onkeydown="if(event.key==='Enter')serverLoad(this.value)">
      <button onclick="serverLoad(document.getElementById('server-path').value)" title="reload">⟳</button>
      <button onclick="serverLoad('/mnt/spielraum')"
              title="/mnt/spielraum">📁 spielraum</button>
    </div>
    <div class="toolbar">
      <label><input type="checkbox" id="server-all" onchange="serverSelectAll(this.checked)"> all</label>
      <span class="sel-count" id="server-count">0 selected</span>
      <button class="send" id="server-send" onclick="sendServerSelection()" disabled>Send to laptop →</button>
    </div>
    <div class="list" id="server-list"
         ondragover="event.preventDefault(); this.classList.add('dragover')"
         ondragleave="this.classList.remove('dragover')"
         ondrop="onDropToServer(event)"></div>
  </div>

  <!-- LAPTOP (right) -->
  <div class="panel">
    <h2>Your machine <span class="addr" id="laptop-host">…</span> <span class="addr" id="laptop-mode">(no folder selected)</span></h2>
    <div class="pathbar">
      <button id="laptop-up-btn" onclick="laptopUp()" title="parent directory" disabled>↑</button>
      <span class="readonly-path" id="laptop-path">—</span>
      <button id="laptop-pick-btn" onclick="laptopPick()" title="choose folder">📁 open…</button>
    </div>
    <div class="toolbar">
      <label><input type="checkbox" id="laptop-all" onchange="laptopSelectAll(this.checked)"> all</label>
      <span class="sel-count" id="laptop-count">0 selected</span>
      <button class="send" id="laptop-send" onclick="sendLaptopSelection()" disabled>← Send to server</button>
    </div>
    <div class="list" id="laptop-list"></div>
  </div>
</div>

<div class="progress-area" id="progress-area" hidden>
  <div class="progress-row">
    <span class="label" id="progress-label">Transfer</span>
    <div class="progress-bar"><div class="fill" id="progress-fill-total"></div></div>
    <span class="pct" id="progress-total-pct">0%</span>
    <button class="cancel" onclick="cancelQueue()">Cancel</button>
  </div>
  <div class="progress-row">
    <span class="text" id="progress-current">—</span>
    <div class="progress-bar"><div class="fill" id="progress-fill-file"></div></div>
    <span class="pct" id="progress-file-pct">0%</span>
  </div>
</div>

<div class="status" id="status"></div>

<dialog id="dialog">
  <div class="dlg-head" id="dlg-title">Confirm</div>
  <div class="dlg-body" id="dlg-body"></div>
  <div class="dlg-buttons" id="dlg-buttons"></div>
</dialog>

<script>
// ======================================================================
// utilities
// ======================================================================
function log(msg, cls) {
  const s = document.getElementById("status");
  const d = document.createElement("div");
  if (cls) d.className = cls;
  d.textContent = "[" + new Date().toLocaleTimeString() + "] " + msg;
  s.appendChild(d);
  s.scrollTop = s.scrollHeight;
}
function esc(s) {
  return s.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function serverJoin(base, name) {
  const trimmed = base.endsWith("/") ? base.slice(0, -1) : base;
  return trimmed + "/" + name;
}
function parentPath(p) {
  if (!p || p === "/") return "/";
  const trimmed = p.endsWith("/") ? p.slice(0, -1) : p;
  const idx = trimmed.lastIndexOf("/");
  return idx <= 0 ? "/" : trimmed.slice(0, idx);
}
function fmtSize(n) {
  n = Number(n) || 0;
  for (const u of ["B","K","M","G","T"]) {
    if (n < 1024) return u === "B" ? `${n|0}${u}` : `${n.toFixed(1)}${u}`;
    n /= 1024;
  }
  return `${n.toFixed(1)}P`;
}
function sum(arr, f) { let s = 0; for (const x of arr) s += f(x); return s; }

// Hidden-file toggle (applies to both panels). Names starting with "." are hidden.
let showHidden = localStorage.getItem("scp-for-me:showHidden") === "1";
// Best-effort "user@host"-style label for the laptop side. Browsers don't
// expose the OS username or hostname (privacy), so we synthesize the
// richest identifier we can: browser@os[-version][ model][ arch].
async function laptopHostLabel() {
  const ua = navigator.userAgent || "";
  let browser = "browser";
  if (/Edg\//.test(ua)) browser = "Edge";
  else if (/OPR\//.test(ua)) browser = "Opera";
  else if (/Firefox\//.test(ua)) browser = "Firefox";
  else if (/Chrome\//.test(ua)) browser = "Chrome";
  else if (/Safari\//.test(ua)) browser = "Safari";

  const uaData = navigator.userAgentData;
  let os = uaData && uaData.platform;
  if (!os) {
    const p = (navigator.platform || "").toLowerCase();
    if (p.includes("mac") || /Mac OS X/.test(ua)) os = "macOS";
    else if (p.includes("win") || /Windows/.test(ua)) os = "Windows";
    else if (/Android/.test(ua)) os = "Android";
    else if (/iPhone|iPad|iPod/.test(ua)) os = "iOS";
    else if (p.includes("linux") || /Linux/.test(ua)) os = "Linux";
    else os = navigator.platform || "unknown";
  }

  let version = "", model = "", arch = "";
  if (uaData && uaData.getHighEntropyValues) {
    try {
      const hi = await uaData.getHighEntropyValues(
        ["platformVersion", "model", "architecture"]);
      version = (hi.platformVersion || "").split(".")[0];
      model = hi.model || "";
      arch = hi.architecture || "";
    } catch {}
  }
  let host = os;
  if (version) host += " " + version;
  if (model) host += " " + model;
  if (arch && !model) host += " " + arch;
  return `${browser}@${host}`;
}

function isHidden(name) { return name.startsWith("."); }
function isVisible(name) { return showHidden || !isHidden(name); }
function toggleHidden(on) {
  showHidden = on;
  localStorage.setItem("scp-for-me:showHidden", on ? "1" : "0");
  // Drop any now-hidden entries from selections to match what the user sees.
  if (!on) {
    for (const n of [...server.sel]) if (isHidden(n)) server.sel.delete(n);
    for (const n of [...laptop.sel]) if (isHidden(n)) laptop.sel.delete(n);
  }
  renderServer();
  laptopRender();
}

// ======================================================================
// dialog helper (uses native <dialog>)
// ======================================================================
function askUser(title, body, buttons) {
  return new Promise(resolve => {
    const d = document.getElementById("dialog");
    let settled = false;
    const finish = v => { if (settled) return; settled = true; try { d.close(); } catch {} resolve(v); };
    document.getElementById("dlg-title").textContent = title;
    const bodyEl = document.getElementById("dlg-body");
    bodyEl.innerHTML = "";
    if (typeof body === "string") bodyEl.textContent = body;
    else bodyEl.appendChild(body);
    const btns = document.getElementById("dlg-buttons");
    btns.innerHTML = "";
    for (const b of buttons) {
      const el = document.createElement("button");
      el.textContent = b.label;
      if (b.cls) el.className = b.cls;
      el.onclick = () => finish(b.value);
      btns.appendChild(el);
    }
    d.oncancel = ev => { ev.preventDefault(); finish("cancel"); };
    d.showModal();
  });
}

// ======================================================================
// SERVER panel (left)
// ======================================================================
const server = { path: "", entries: [], sel: new Set() };

async function serverLoad(path) {
  const url = `/api/list?path=${encodeURIComponent(path || "")}`;
  let res, data;
  try { res = await fetch(url); data = await res.json(); }
  catch (e) { log("server: " + e, "err"); return; }
  if (!res.ok) { log("server: " + (data.error || "failed"), "err"); return; }
  server.path = data.path;
  server.entries = data.entries;
  server.sel = new Set();
  document.getElementById("server-path").value = data.path;
  document.getElementById("server-all").checked = false;
  renderServer();
}
function serverUp() { serverLoad(parentPath(server.path)); }

function renderServer() {
  const list = document.getElementById("server-list");
  list.innerHTML = "";
  const up = document.createElement("div");
  up.className = "row dir parent";
  up.innerHTML = '<input type="checkbox" class="sel" disabled><span class="name">📁 ..</span><span class="size"></span>';
  up.onclick = serverUp;
  list.appendChild(up);
  for (const e of server.entries) {
    if (!isVisible(e.name)) continue;
    const row = document.createElement("div");
    row.className = "row" + (e.is_dir ? " dir" : "") + (server.sel.has(e.name) ? " selected" : "");
    const icon = e.is_dir ? "📁" : "📄";
    row.innerHTML = `
      <input type="checkbox" class="sel" ${server.sel.has(e.name) ? "checked" : ""}>
      <span class="name">${icon} ${esc(e.name)}</span>
      <span class="size">${e.size_h}</span>`;
    const cb = row.querySelector("input.sel");
    cb.onclick = ev => {
      ev.stopPropagation();
      if (cb.checked) server.sel.add(e.name); else server.sel.delete(e.name);
      row.classList.toggle("selected", cb.checked);
      updateServerSelUi();
    };
    row.onclick = () => {
      if (e.is_dir) serverLoad(serverJoin(server.path, e.name));
      else transfer("server->laptop", [e]);
    };
    list.appendChild(row);
  }
  updateServerSelUi();
}
function updateServerSelUi() {
  const n = server.sel.size;
  const visibleCount = server.entries.filter(e => isVisible(e.name)).length;
  document.getElementById("server-count").textContent =
    n === 0 ? "0 selected" : `${n} selected`;
  document.getElementById("server-send").disabled = n === 0 || queueActive;
  document.getElementById("server-all").checked =
    n > 0 && n === visibleCount;
}
function serverSelectAll(checked) {
  const visible = server.entries.filter(e => isVisible(e.name));
  server.sel = new Set(checked ? visible.map(e => e.name) : []);
  renderServer();
}
function sendServerSelection() {
  const items = server.entries.filter(e => server.sel.has(e.name));
  if (!items.length) return;
  transfer("server->laptop", items);
}

// ======================================================================
// LAPTOP panel (right)
// ======================================================================
const hasFSAPI = typeof window.showDirectoryPicker === "function";
const laptop = {
  stack: [],      // [{handle, name}] — last is current directory
  entries: [],    // current directory entries (enriched with size)
  sel: new Set(),
};

function laptopDisplayPath() { return laptop.stack.map(s => s.name).join("/") || "—"; }

async function laptopPick() {
  if (!hasFSAPI) {
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.onchange = async () => {
      if (!input.files.length) return;
      if (!server.path) { log("server path not loaded yet", "err"); return; }
      // Build a synthetic 'items' list for the queue
      const items = [];
      for (const f of input.files) {
        items.push({
          name: f.name, is_dir: false, size: f.size,
          __blob: f, __isBlob: true,
        });
      }
      await transfer("laptop->server", items);
    };
    input.click();
    return;
  }
  try {
    const handle = await window.showDirectoryPicker({ mode: "readwrite" });
    laptop.stack = [{ handle, name: handle.name }];
    laptop.sel = new Set();
    await laptopRender();
  } catch (e) {
    if (e.name !== "AbortError") log("folder picker: " + e, "err");
  }
}

async function laptopRender() {
  const list = document.getElementById("laptop-list");
  list.innerHTML = "";
  const pathSpan = document.getElementById("laptop-path");
  const mode = document.getElementById("laptop-mode");
  const upBtn = document.getElementById("laptop-up-btn");

  if (!laptop.stack.length) {
    pathSpan.textContent = "—";
    upBtn.disabled = true;
    laptop.entries = [];
    laptop.sel = new Set();
    mode.textContent = "";
    if (!hasFSAPI) {
      list.innerHTML = `<div class="empty">
        Firefox/Safari can't enumerate local folders.<br>
        Click <b>📁 open…</b> to pick files to upload, or drag &amp; drop files onto the server panel.
      </div>`;
    } else {
      list.innerHTML = `<div class="empty">
        Pick a folder on your laptop to browse it here.<br>
        <button onclick="laptopPick()">📁 open folder…</button>
      </div>`;
    }
    updateLaptopSelUi();
    return;
  }

  pathSpan.textContent = laptopDisplayPath();
  upBtn.disabled = laptop.stack.length <= 1;
  mode.textContent = "(" + laptop.stack[0].name + ")";

  const current = laptop.stack[laptop.stack.length - 1].handle;
  const items = [];
  try {
    for await (const [name, h] of current.entries()) {
      items.push({ name, handle: h, is_dir: h.kind === "directory" });
    }
  } catch (e) {
    log("laptop list: " + e, "err");
    return;
  }
  for (const it of items) {
    if (!it.is_dir) {
      try {
        const f = await it.handle.getFile();
        it.size = f.size;
        it.size_h = fmtSize(f.size);
      } catch { it.size = 0; it.size_h = ""; }
    } else {
      it.size = 0; it.size_h = "";
    }
  }
  items.sort((a, b) => (a.is_dir === b.is_dir)
    ? a.name.toLowerCase().localeCompare(b.name.toLowerCase())
    : (a.is_dir ? -1 : 1));
  laptop.entries = items;
  // Drop selected names that no longer exist in this directory listing.
  for (const n of [...laptop.sel]) if (!items.some(it => it.name === n)) laptop.sel.delete(n);

  const upRow = document.createElement("div");
  upRow.className = "row dir parent";
  upRow.innerHTML = '<input type="checkbox" class="sel" disabled><span class="name">📁 ..</span><span class="size"></span>';
  upRow.onclick = laptopUp;
  list.appendChild(upRow);
  for (const e of items) {
    if (!isVisible(e.name)) continue;
    const row = document.createElement("div");
    row.className = "row" + (e.is_dir ? " dir" : "") + (laptop.sel.has(e.name) ? " selected" : "");
    const icon = e.is_dir ? "📁" : "📄";
    row.innerHTML = `
      <input type="checkbox" class="sel" ${laptop.sel.has(e.name) ? "checked" : ""}>
      <span class="name">${icon} ${esc(e.name)}</span>
      <span class="size">${e.size_h || ""}</span>`;
    const cb = row.querySelector("input.sel");
    cb.onclick = ev => {
      ev.stopPropagation();
      if (cb.checked) laptop.sel.add(e.name); else laptop.sel.delete(e.name);
      row.classList.toggle("selected", cb.checked);
      updateLaptopSelUi();
    };
    row.onclick = async () => {
      if (e.is_dir) {
        laptop.stack.push({ handle: e.handle, name: e.name });
        laptop.sel = new Set();
        await laptopRender();
      } else {
        await transfer("laptop->server", [e]);
      }
    };
    list.appendChild(row);
  }
  updateLaptopSelUi();
}
function updateLaptopSelUi() {
  const n = laptop.sel.size;
  const visibleCount = laptop.entries.filter(e => isVisible(e.name)).length;
  document.getElementById("laptop-count").textContent =
    n === 0 ? "0 selected" : `${n} selected`;
  document.getElementById("laptop-send").disabled =
    n === 0 || queueActive || !server.path;
  document.getElementById("laptop-all").checked =
    n > 0 && n === visibleCount;
}
function laptopSelectAll(checked) {
  const visible = laptop.entries.filter(e => isVisible(e.name));
  laptop.sel = new Set(checked ? visible.map(e => e.name) : []);
  const rows = document.querySelectorAll("#laptop-list .row:not(.parent)");
  rows.forEach(row => {
    const name = row.querySelector(".name").textContent.trim().replace(/^📁 |^📄 /, "");
    const sel = laptop.sel.has(name);
    row.querySelector("input.sel").checked = sel;
    row.classList.toggle("selected", sel);
  });
  updateLaptopSelUi();
}
function laptopUp() {
  if (laptop.stack.length > 1) {
    laptop.stack.pop();
    laptop.sel = new Set();
    laptopRender();
  }
}
function sendLaptopSelection() {
  const items = laptop.entries.filter(e => laptop.sel.has(e.name));
  if (!items.length) return;
  transfer("laptop->server", items);
}

// ======================================================================
// Progress UI
// ======================================================================
const progress = {
  totalFiles: 0, doneFiles: 0,
  totalBytes: 0, doneBytes: 0,
  curName: "", curBytes: 0, curTotal: 0,
};
function progressShow() {
  document.getElementById("progress-area").hidden = false;
  progressRender();
}
function progressHide() {
  document.getElementById("progress-area").hidden = true;
}
function progressReset(totalFiles, totalBytes) {
  progress.totalFiles = totalFiles;
  progress.doneFiles = 0;
  progress.totalBytes = totalBytes;
  progress.doneBytes = 0;
  progress.curName = ""; progress.curBytes = 0; progress.curTotal = 0;
  progressRender();
}
function progressStartFile(name, total) {
  progress.curName = name;
  progress.curBytes = 0;
  progress.curTotal = total || 0;
  progressRender();
}
function progressUpdateFile(bytes, total) {
  progress.curBytes = bytes;
  if (total) progress.curTotal = total;
  progressRender();
}
function progressFinishFile(bytes) {
  progress.doneFiles += 1;
  progress.doneBytes += bytes || progress.curTotal || progress.curBytes;
  progress.curBytes = 0; progress.curTotal = 0; progress.curName = "";
  progressRender();
}
function progressSkipFile() {
  progress.doneFiles += 1;
  progress.doneBytes += progress.curTotal;  // treat as "processed"
  progress.curBytes = 0; progress.curTotal = 0; progress.curName = "";
  progressRender();
}
function progressRender() {
  const totalBytes = progress.totalBytes || 1;
  const overall = progress.doneBytes + progress.curBytes;
  const overallPct = Math.min(100, Math.round(overall * 100 / totalBytes));
  const filePct = progress.curTotal
    ? Math.min(100, Math.round(progress.curBytes * 100 / progress.curTotal))
    : 0;
  document.getElementById("progress-fill-total").style.width = overallPct + "%";
  document.getElementById("progress-fill-file").style.width = filePct + "%";
  document.getElementById("progress-total-pct").textContent = overallPct + "%";
  document.getElementById("progress-file-pct").textContent = progress.curTotal ? filePct + "%" : "—";
  document.getElementById("progress-label").textContent =
    `${progress.doneFiles} / ${progress.totalFiles}`;
  document.getElementById("progress-current").textContent = progress.curName
    ? `${progress.curName}  (${fmtSize(progress.curBytes)} / ${fmtSize(progress.curTotal)})`
    : "—";
}

// ======================================================================
// Transfer queue: confirmations, overwrite policy, progress, cancel
// ======================================================================
let queueActive = false;
let queueAbort = null;
let overwritePolicy = null;  // null | 'yes-all' | 'skip-all'

function cancelQueue() {
  if (queueAbort) { try { queueAbort.abort(); } catch {} }
}

async function transfer(direction, items) {
  if (queueActive) { log("a transfer is already running", "err"); return; }
  if (!items.length) return;

  // Plan: expand folders into flat file list
  let files;
  try {
    if (direction === "server->laptop") {
      if (!hasFSAPI || !laptop.stack.length) {
        // Fallback: trigger browser downloads one per file. Folders unsupported.
        const anyDir = items.some(i => i.is_dir);
        if (anyDir) {
          await askUser("Folder transfer not supported",
            "Downloading folders to your laptop requires Chrome/Edge with a picked folder. " +
            "Individual files will still download to your browser's Downloads folder.",
            [{ label: "OK", value: "ok", cls: "primary" }]);
        }
        const fileItems = items.filter(i => !i.is_dir);
        if (!fileItems.length) return;
        for (const f of fileItems) triggerBrowserDownload(serverJoin(server.path, f.name), f.name);
        log(`browser downloading ${fileItems.length} file(s) → your Downloads folder`, "ok");
        return;
      }
      files = await expandServerItems(items);
    } else {
      files = await expandLaptopItems(items);
    }
  } catch (e) {
    log("plan failed: " + e, "err");
    return;
  }
  if (!files.length) { log("nothing to transfer", "err"); return; }

  // Confirm big transfers
  const totalBytes = sum(files, f => f.size || 0);
  const bigEnough = files.length > 1 || totalBytes > 50 * 1024 * 1024;
  if (bigEnough) {
    const dest = direction === "server->laptop" ? "your laptop" : "the server";
    const targetPath = direction === "server->laptop" ? laptopDisplayPath() : server.path;
    const arrow = direction === "server->laptop" ? "↓" : "↑";
    const choice = await askUser(
      `Transfer ${files.length} file${files.length === 1 ? "" : "s"}?`,
      `${arrow} Copy ${files.length} file${files.length === 1 ? "" : "s"} ` +
      `(${fmtSize(totalBytes)}) to ${dest}:\n${targetPath}`,
      [
        { label: "Cancel", value: "cancel" },
        { label: "Transfer", value: "go", cls: "primary" },
      ]
    );
    if (choice !== "go") { log("cancelled", "err"); return; }
  }

  // Start queue
  queueActive = true;
  queueAbort = new AbortController();
  overwritePolicy = null;
  updateServerSelUi(); updateLaptopSelUi();
  progressReset(files.length, totalBytes);
  progressShow();
  log(`starting: ${files.length} file(s), ${fmtSize(totalBytes)} ${direction === "server->laptop" ? "→ laptop" : "→ server"}`);

  let ok = 0, skipped = 0, failed = 0, cancelled = false;
  try {
    for (const f of files) {
      if (queueAbort.signal.aborted) { cancelled = true; break; }
      progressStartFile(f.relpath, f.size);
      try {
        if (direction === "server->laptop") {
          const r = await runServerToLaptop(f);
          if (r === "skip") { skipped++; progressSkipFile(); }
          else { ok++; progressFinishFile(f.size); }
        } else {
          const r = await runLaptopToServer(f);
          if (r === "skip") { skipped++; progressSkipFile(); }
          else { ok++; progressFinishFile(f.size); }
        }
      } catch (e) {
        if (e && e.cancelled) { cancelled = true; break; }
        failed++;
        log(`✗ ${f.relpath}: ${(e && e.message) || e}`, "err");
        progressFinishFile(0);
      }
    }
  } finally {
    queueActive = false;
    queueAbort = null;
    progressHide();
    updateServerSelUi(); updateLaptopSelUi();
  }

  const parts = [];
  if (ok) parts.push(`${ok} copied`);
  if (skipped) parts.push(`${skipped} skipped`);
  if (failed) parts.push(`${failed} failed`);
  if (cancelled) parts.push("cancelled");
  log((cancelled || failed) ? parts.join(", ") : "✓ " + parts.join(", "),
      (failed || cancelled) ? "err" : "ok");

  // Refresh destination
  if (direction === "server->laptop") { if (laptop.stack.length) await laptopRender(); }
  else { await serverLoad(server.path); }
}

// Expand server-side items (possibly directories) into flat file list.
async function expandServerItems(items) {
  const out = [];
  for (const it of items) {
    const abs = serverJoin(server.path, it.name);
    if (!it.is_dir) {
      out.push({ absServerPath: abs, relpath: it.name, size: it.size || 0 });
    } else {
      const res = await fetch(`/api/list-tree?path=${encodeURIComponent(abs)}`);
      const data = await res.json();
      if (!res.ok) { log(`cannot read ${it.name}: ${data.error}`, "err"); continue; }
      for (const e of data.entries) {
        out.push({
          absServerPath: serverJoin(abs, e.relpath),
          relpath: it.name + "/" + e.relpath,
          size: e.size,
        });
      }
    }
  }
  return out;
}

// Expand laptop-side items (possibly directories) into flat file list.
async function expandLaptopItems(items) {
  const out = [];
  for (const it of items) {
    if (it.__isBlob) {
      out.push({ blob: it.__blob, relpath: it.name, size: it.size });
      continue;
    }
    if (!it.is_dir) {
      const f = await it.handle.getFile();
      out.push({ fileHandle: it.handle, relpath: it.name, size: f.size });
    } else {
      await walkLaptopDir(it.handle, it.name, out);
    }
  }
  return out;
}
async function walkLaptopDir(dirHandle, prefix, out) {
  for await (const [name, h] of dirHandle.entries()) {
    const rp = prefix + "/" + name;
    if (h.kind === "directory") await walkLaptopDir(h, rp, out);
    else {
      const f = await h.getFile();
      out.push({ fileHandle: h, relpath: rp, size: f.size });
    }
  }
}

// ---- server → laptop (one file) -------------------------------------
async function runServerToLaptop(f) {
  // resolve destination directory handle, creating subdirs as needed
  const parts = f.relpath.split("/");
  const filename = parts.pop();
  let dir = laptop.stack[laptop.stack.length - 1].handle;
  for (const seg of parts) {
    dir = await dir.getDirectoryHandle(seg, { create: true });
  }

  // existence check
  let exists = false;
  try {
    await dir.getFileHandle(filename, { create: false });
    exists = true;
  } catch (e) {
    if (e.name !== "NotFoundError") throw e;
  }
  if (exists) {
    const choice = await askOverwrite(f.relpath, "on your laptop");
    if (choice === "skip" || choice === "skip-all") return "skip";
    if (choice === "cancel") throw { cancelled: true };
    // yes / yes-all → proceed
  }

  const url = `/api/download?path=${encodeURIComponent(f.absServerPath)}`;
  const res = await fetch(url, { signal: queueAbort.signal });
  if (!res.ok) throw new Error("HTTP " + res.status);

  const fileHandle = await dir.getFileHandle(filename, { create: true });
  const writable = await fileHandle.createWritable();
  const total = Number(res.headers.get("Content-Length") || f.size || 0);
  let received = 0;
  const pts = new TransformStream({
    transform(chunk, ctrl) {
      received += chunk.byteLength;
      progressUpdateFile(received, total);
      ctrl.enqueue(chunk);
    },
  });
  try {
    await res.body.pipeThrough(pts).pipeTo(writable);
  } catch (e) {
    try { await writable.abort(); } catch {}
    if (queueAbort.signal.aborted) throw { cancelled: true };
    throw e;
  }
  return "ok";
}

// ---- laptop → server (one file) -------------------------------------
async function runLaptopToServer(f) {
  const blob = f.blob || await f.fileHandle.getFile();

  // existence check
  const dest = serverJoin(server.path, f.relpath);
  const existsRes = await fetch(`/api/exists?path=${encodeURIComponent(dest)}`);
  const existsData = await existsRes.json().catch(() => ({}));
  if (existsData.is_dir) {
    throw new Error("destination is a directory");
  }
  let overwrite = false;
  if (existsData.exists) {
    const choice = await askOverwrite(f.relpath, "on the server");
    if (choice === "skip" || choice === "skip-all") return "skip";
    if (choice === "cancel") throw { cancelled: true };
    overwrite = true;
  }

  const url = `/api/upload-one?dir=${encodeURIComponent(server.path)}`
            + `&name=${encodeURIComponent(f.relpath)}`
            + (overwrite ? "&overwrite=1" : "");
  await uploadXhr(url, blob);
  return "ok";
}

// XHR upload so we get progress events
function uploadXhr(url, blob) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) progressUpdateFile(e.loaded, e.total);
    };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText); } catch {}
      if (xhr.status >= 200 && xhr.status < 300 && data.ok) resolve(data);
      else reject(new Error(data.error || ("HTTP " + xhr.status)));
    };
    xhr.onerror = () => reject(new Error("network error"));
    xhr.onabort = () => reject({ cancelled: true });
    const onAbort = () => xhr.abort();
    if (queueAbort) queueAbort.signal.addEventListener("abort", onAbort);
    xhr.send(blob);
  });
}

// ---- overwrite dialog -----------------------------------------------
async function askOverwrite(relpath, where) {
  if (overwritePolicy === "yes-all") return "yes-all";
  if (overwritePolicy === "skip-all") return "skip-all";
  const choice = await askUser(
    "File exists",
    `${relpath}\nalready exists ${where}. Overwrite?`,
    [
      { label: "Cancel",    value: "cancel" },
      { label: "Skip",      value: "skip" },
      { label: "Skip all",  value: "skip-all" },
      { label: "Overwrite", value: "yes", cls: "danger" },
      { label: "Overwrite all", value: "yes-all", cls: "danger" },
    ]
  );
  if (choice === "yes-all") overwritePolicy = "yes-all";
  if (choice === "skip-all") overwritePolicy = "skip-all";
  return choice;
}

// ---- fallback download (no FS API / no folder picked) ---------------
function triggerBrowserDownload(srcPath, name) {
  const a = document.createElement("a");
  a.href = `/api/download?path=${encodeURIComponent(srcPath)}`;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

// ---- drag-and-drop from OS → server panel ---------------------------
async function onDropToServer(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove("dragover");
  const files = ev.dataTransfer.files;
  if (!files || !files.length) return;
  const items = [];
  for (const f of files) {
    items.push({ name: f.name, is_dir: false, size: f.size, __blob: f, __isBlob: true });
  }
  await transfer("laptop->server", items);
}

// ======================================================================
// boot
// ======================================================================
document.getElementById("show-hidden").checked = showHidden;
laptopHostLabel().then(s => { document.getElementById("laptop-host").textContent = s; });
log(hasFSAPI
  ? "ready. check boxes + use → / ← to transfer multiple items. folders supported."
  : "ready. Firefox/Safari has no live laptop browser — use 📁 open… to upload, drag-drop to server panel, or click server files to download.");
serverLoad("");
laptopRender();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print(f"scp-for-me: serving on http://127.0.0.1:{LISTEN_PORT}")
    print("On your laptop: ssh -L 5555:localhost:5555 user@this-host, then open http://localhost:5555")
    app.run(host="127.0.0.1", port=LISTEN_PORT, debug=False)
