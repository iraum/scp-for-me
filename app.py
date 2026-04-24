#!/usr/bin/env python3.11
"""Two-pane file transfer UI. Left = this machine. Right = your browser's laptop.

Run on the server, SSH-forward the port, then open http://localhost:5555 in the
laptop browser:

    ssh -L 5555:localhost:5555 user@this-machine

Traffic goes over the tunnel; no SFTP back out is required.
"""
import os
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
# Largest single-upload size. 16 GiB default — plenty of headroom; adjust via env.
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_UPLOAD_GB", "16")) * 1024**3


def fmt_size(n):
    n = float(n)
    for unit in ["B", "K", "M", "G", "T"]:
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}P"


@app.route("/")
def index():
    return render_template_string(HTML)


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
                entries.append(
                    {
                        "name": entry.name,
                        "is_dir": is_dir,
                        "size": size,
                        "size_h": "" if is_dir else fmt_size(size),
                    }
                )
            except OSError:
                pass
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
        return jsonify({"path": str(p), "entries": entries})
    except PermissionError as e:
        return jsonify({"error": f"permission denied: {e}"}), 403
    except Exception as e:
        return jsonify({"error": f"{type(e).__name__}: {e}"}), 500


@app.route("/api/download")
def api_download():
    path = request.args.get("path", "")
    if not path:
        abort(400)
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        abort(404)
    return send_file(p, as_attachment=True, download_name=p.name)


@app.route("/api/upload", methods=["POST"])
def api_upload():
    target = request.args.get("dir", "")
    if not target:
        return jsonify({"error": "missing dir"}), 400
    d = Path(target).expanduser().resolve()
    if not d.is_dir():
        return jsonify({"error": f"not a directory: {d}"}), 400
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "no files"}), 400
    saved = []
    for f in files:
        # filename may include a relative path when coming from directory upload.
        # We only use the basename here — the directory-aware upload path uses
        # x-relpath headers via the /api/upload-one endpoint below.
        name = os.path.basename(f.filename or "")
        if not name:
            continue
        dest = d / name
        f.save(str(dest))
        saved.append(name)
    return jsonify({"ok": True, "saved": saved})


@app.route("/api/upload-one", methods=["POST"])
def api_upload_one():
    """Single-file upload with explicit filename in query — so the FS Access
    path can push bytes without a multipart round-trip (and preserves Unicode)."""
    target = request.args.get("dir", "")
    name = request.args.get("name", "")
    if not target or not name:
        return jsonify({"error": "missing dir or name"}), 400
    d = Path(target).expanduser().resolve()
    if not d.is_dir():
        return jsonify({"error": f"not a directory: {d}"}), 400
    name = os.path.basename(name)
    if not name:
        return jsonify({"error": "bad filename"}), 400
    dest = d / name
    with open(dest, "wb") as out:
        # stream the raw body
        chunk = request.stream.read(1024 * 1024)
        total = 0
        while chunk:
            out.write(chunk)
            total += len(chunk)
            chunk = request.stream.read(1024 * 1024)
    return jsonify({"ok": True, "saved": name, "bytes": total})


HTML = r"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title>scp-for-me</title>
<style>
  * { box-sizing: border-box; }
  body { margin: 0; font-family: -apple-system, system-ui, sans-serif; font-size: 14px;
         color: #222; background: #f5f5f5; display: flex; flex-direction: column; height: 100vh; }
  header { padding: 8px 14px; background: #2d3748; color: white; display: flex; align-items: center; gap: 12px; }
  header h1 { margin: 0; font-size: 15px; font-weight: 500; }
  header .hint { font-size: 12px; color: #a0aec0; }
  .panels { display: flex; flex: 1; overflow: hidden; gap: 1px; background: #cbd5e0; }
  .panel { flex: 1; display: flex; flex-direction: column; background: white; overflow: hidden; min-width: 0; }
  .panel h2 { margin: 0; padding: 6px 10px; font-size: 13px; font-weight: 600;
              background: #edf2f7; border-bottom: 1px solid #cbd5e0; display: flex; gap: 8px; align-items: baseline; }
  .panel h2 .addr { color: #718096; font-weight: 400; font-family: monospace; font-size: 12px; }
  .pathbar { display: flex; padding: 6px; gap: 4px; border-bottom: 1px solid #e2e8f0; align-items: center; }
  .pathbar button { padding: 2px 10px; cursor: pointer; background: white; border: 1px solid #cbd5e0; border-radius: 3px; }
  .pathbar button:hover { background: #edf2f7; }
  .pathbar button:disabled { opacity: 0.4; cursor: default; }
  .pathbar input { flex: 1; padding: 3px 6px; font-family: monospace; font-size: 12px;
                   border: 1px solid #cbd5e0; border-radius: 3px; min-width: 0; }
  .pathbar .readonly-path { flex: 1; padding: 3px 6px; font-family: monospace; font-size: 12px;
                            background: #f7fafc; border: 1px solid #e2e8f0; border-radius: 3px;
                            white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .list { flex: 1; overflow-y: auto; font-family: monospace; font-size: 12px; position: relative; }
  .list.dragover { background: #ebf8ff; outline: 2px dashed #4299e1; outline-offset: -6px; }
  .row { display: flex; padding: 3px 10px; cursor: pointer; user-select: none; gap: 8px; }
  .row:hover { background: #ebf8ff; }
  .row.dir { color: #2b6cb0; font-weight: 500; }
  .row .name { flex: 1; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .row .size { color: #718096; min-width: 60px; text-align: right; }
  .empty { padding: 24px 16px; color: #718096; text-align: center; font-family: -apple-system, system-ui, sans-serif; }
  .empty button { margin-top: 8px; padding: 6px 14px; cursor: pointer; background: #4299e1; color: white;
                  border: none; border-radius: 3px; font-size: 13px; }
  .empty button:hover { background: #3182ce; }
  .status { padding: 6px 12px; background: #1a202c; color: #cbd5e0;
            font-family: monospace; font-size: 12px; border-top: 1px solid #2d3748;
            max-height: 140px; overflow-y: auto; }
  .status div { padding: 1px 0; }
  .status .err { color: #fc8181; }
  .status .ok { color: #9ae6b4; }
</style>
</head>
<body>
<header>
  <h1>scp-for-me</h1>
  <span class="hint">click a directory to navigate • click a file to copy to the other side</span>
</header>
<div class="panels">
  <!-- Left panel: server filesystem -->
  <div class="panel">
    <h2>Server <span class="addr">this machine</span></h2>
    <div class="pathbar">
      <button onclick="serverUp()" title="parent directory">↑</button>
      <input id="server-path" onkeydown="if(event.key==='Enter')serverLoad(this.value)">
      <button onclick="serverLoad(document.getElementById('server-path').value)" title="reload">⟳</button>
    </div>
    <div class="list" id="server-list"
         ondragover="event.preventDefault(); this.classList.add('dragover')"
         ondragleave="this.classList.remove('dragover')"
         ondrop="onDropToServer(event)"></div>
  </div>

  <!-- Right panel: your browser's machine (the laptop you're viewing from) -->
  <div class="panel">
    <h2>Your machine <span class="addr" id="laptop-mode">(no folder selected)</span></h2>
    <div class="pathbar">
      <button id="laptop-up-btn" onclick="laptopUp()" title="parent directory" disabled>↑</button>
      <span class="readonly-path" id="laptop-path">—</span>
      <button id="laptop-pick-btn" onclick="laptopPick()" title="choose folder">📁 open…</button>
    </div>
    <div class="list" id="laptop-list"></div>
  </div>
</div>
<div class="status" id="status"></div>

<script>
// ---------- tiny utilities ----------
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
function joinPath(base, name) {
  const trimmed = base.endsWith("/") ? base.slice(0, -1) : base;
  return trimmed + "/" + name;
}
function parentPath(p) {
  if (!p || p === "/") return "/";
  const trimmed = p.endsWith("/") ? p.slice(0, -1) : p;
  const idx = trimmed.lastIndexOf("/");
  return idx <= 0 ? "/" : trimmed.slice(0, idx);
}

// ---------- SERVER panel (left) ----------
const server = { path: "", entries: [] };

async function serverLoad(path) {
  const url = `/api/list?path=${encodeURIComponent(path || "")}`;
  let res, data;
  try { res = await fetch(url); data = await res.json(); }
  catch (e) { log("server: " + e, "err"); return; }
  if (!res.ok) { log("server: " + (data.error || "failed"), "err"); return; }
  server.path = data.path;
  server.entries = data.entries;
  document.getElementById("server-path").value = data.path;
  const list = document.getElementById("server-list");
  list.innerHTML = "";
  const up = document.createElement("div");
  up.className = "row dir";
  up.innerHTML = '<span class="name">📁 ..</span><span class="size"></span>';
  up.onclick = serverUp;
  list.appendChild(up);
  for (const e of data.entries) {
    const row = document.createElement("div");
    row.className = "row" + (e.is_dir ? " dir" : "");
    const icon = e.is_dir ? "📁" : "📄";
    row.innerHTML = `<span class="name">${icon} ${esc(e.name)}</span><span class="size">${e.size_h}</span>`;
    row.onclick = () => {
      if (e.is_dir) serverLoad(joinPath(data.path, e.name));
      else transferServerToLaptop(e);
    };
    list.appendChild(row);
  }
}
function serverUp() { serverLoad(parentPath(server.path)); }

// ---------- LAPTOP panel (right) — File System Access API preferred ----------
const hasFSAPI = typeof window.showDirectoryPicker === "function";
const laptop = {
  stack: [],      // array of {handle, name} — last = current
  entries: [],    // current directory entries
};

function laptopDisplayPath() {
  if (!laptop.stack.length) return "—";
  return laptop.stack.map(s => s.name).join("/");
}

async function laptopPick() {
  if (!hasFSAPI) {
    // Fallback: use a hidden file input and let user pick files to upload
    const input = document.createElement("input");
    input.type = "file";
    input.multiple = true;
    input.onchange = async () => {
      for (const f of input.files) {
        await uploadBlob(f, f.name);
      }
    };
    input.click();
    return;
  }
  try {
    const handle = await window.showDirectoryPicker({ mode: "readwrite" });
    laptop.stack = [{ handle, name: handle.name }];
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
    if (!hasFSAPI) {
      mode.textContent = "(click open… to upload files — full browsing needs Chrome/Edge)";
      list.innerHTML = `<div class="empty">
        Firefox/Safari can't enumerate local folders.<br>
        Click <b>📁 open…</b> to pick files to upload, or drag &amp; drop files onto the left panel.
      </div>`;
    } else {
      mode.textContent = "(no folder selected)";
      list.innerHTML = `<div class="empty">
        Pick a folder on your laptop to browse it here.<br>
        <button onclick="laptopPick()">📁 open folder…</button>
      </div>`;
    }
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
  // gather sizes for files
  for (const it of items) {
    if (!it.is_dir) {
      try {
        const f = await it.handle.getFile();
        it.size = f.size;
        it.size_h = fmtSize(f.size);
      } catch { it.size = 0; it.size_h = ""; }
    }
  }
  items.sort((a, b) => (a.is_dir === b.is_dir)
    ? a.name.toLowerCase().localeCompare(b.name.toLowerCase())
    : (a.is_dir ? -1 : 1));
  laptop.entries = items;

  const upRow = document.createElement("div");
  upRow.className = "row dir";
  upRow.innerHTML = '<span class="name">📁 ..</span><span class="size"></span>';
  upRow.onclick = laptopUp;
  list.appendChild(upRow);
  for (const e of items) {
    const row = document.createElement("div");
    row.className = "row" + (e.is_dir ? " dir" : "");
    const icon = e.is_dir ? "📁" : "📄";
    row.innerHTML = `<span class="name">${icon} ${esc(e.name)}</span><span class="size">${e.size_h || ""}</span>`;
    row.onclick = async () => {
      if (e.is_dir) {
        laptop.stack.push({ handle: e.handle, name: e.name });
        await laptopRender();
      } else {
        await transferLaptopToServer(e);
      }
    };
    list.appendChild(row);
  }
}

function laptopUp() {
  if (laptop.stack.length > 1) {
    laptop.stack.pop();
    laptopRender();
  }
}

function fmtSize(n) {
  for (const u of ["B","K","M","G","T"]) {
    if (n < 1024) return u === "B" ? `${n|0}${u}` : `${n.toFixed(1)}${u}`;
    n /= 1024;
  }
  return `${n.toFixed(1)}P`;
}

// ---------- transfers ----------
async function transferServerToLaptop(entry) {
  const srcPath = joinPath(server.path, entry.name);
  log(`↓ downloading ${entry.name} (${entry.size_h}) from server…`);
  const url = `/api/download?path=${encodeURIComponent(srcPath)}`;

  if (hasFSAPI && laptop.stack.length) {
    // Write directly into the picked folder via FS Access API
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error("HTTP " + res.status);
      const dirHandle = laptop.stack[laptop.stack.length - 1].handle;
      const fileHandle = await dirHandle.getFileHandle(entry.name, { create: true });
      const writable = await fileHandle.createWritable();
      await res.body.pipeTo(writable);
      log(`✓ ${entry.name} saved to laptop: ${laptopDisplayPath()}/${entry.name}`, "ok");
      await laptopRender();
    } catch (e) {
      log(`✗ download failed: ${e}`, "err");
    }
  } else {
    // Fallback: let the browser download via anchor click
    const a = document.createElement("a");
    a.href = url;
    a.download = entry.name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    log(`✓ browser downloading ${entry.name} → your Downloads folder`, "ok");
  }
}

async function transferLaptopToServer(entry) {
  try {
    const file = await entry.handle.getFile();
    await uploadBlob(file, entry.name);
  } catch (e) {
    log(`✗ upload failed: ${e}`, "err");
  }
}

async function uploadBlob(blob, name) {
  if (!server.path) { log("server path not loaded yet", "err"); return; }
  log(`↑ uploading ${name} (${fmtSize(blob.size)}) to server…`);
  const url = `/api/upload-one?dir=${encodeURIComponent(server.path)}&name=${encodeURIComponent(name)}`;
  const res = await fetch(url, {
    method: "POST",
    body: blob,
    headers: { "Content-Type": "application/octet-stream" },
  });
  const data = await res.json().catch(() => ({}));
  if (res.ok && data.ok) {
    log(`✓ ${name} uploaded to server: ${server.path}/${name}`, "ok");
    serverLoad(server.path);
  } else {
    log(`✗ upload failed: ${data.error || ("HTTP " + res.status)}`, "err");
  }
}

// Drag-and-drop from desktop → server panel (always works, no FS API needed)
async function onDropToServer(ev) {
  ev.preventDefault();
  ev.currentTarget.classList.remove("dragover");
  const items = ev.dataTransfer.files;
  if (!items || !items.length) return;
  for (const f of items) {
    await uploadBlob(f, f.name);
  }
}

// ---------- boot ----------
log(hasFSAPI
  ? "ready. right panel: click 📁 open… to pick a laptop folder."
  : "ready. Firefox/Safari: no live laptop browser — use 📁 open… to upload, drag-drop to left panel, or click server files to download.");
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
