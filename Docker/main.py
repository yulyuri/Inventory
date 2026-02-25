from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3
import random
import qrcode
import base64
from io import BytesIO
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired

app = FastAPI()

DB_PATH = "/app/data/inventory.db"

# --- Config ---
SECRET_KEY = "change-this-to-something-secret"
PASSWORD = "homelab123"  # Change this to your password
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days
serializer = URLSafeTimedSerializer(SECRET_KEY)

# --- Auth helpers ---
def create_session_token():
    return serializer.dumps({"logged_in": True})

def verify_session(request: Request):
    token = request.cookies.get("session")
    if not token:
        return False
    try:
        serializer.loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False

def require_auth(request: Request):
    if not verify_session(request):
        raise HTTPException(status_code=401, detail="Not authenticated")

# --- DB ---
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            short_name TEXT NOT NULL,
            description TEXT DEFAULT '',
            parent_id INTEGER REFERENCES items(id),
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()

init_db()

# --- Hierarchy helper ---
def get_breadcrumb(conn, item_id):
    """Walk up the parent chain and return list of {id, short_name}"""
    crumbs = []
    visited = set()
    current_id = item_id
    while current_id and current_id not in visited:
        visited.add(current_id)
        row = conn.execute("SELECT id, short_name, parent_id FROM items WHERE id = ?", (current_id,)).fetchone()
        if not row:
            break
        crumbs.insert(0, {"id": row["id"], "short_name": row["short_name"]})
        current_id = row["parent_id"]
    return crumbs

def get_breadcrumb_string(conn, parent_id):
    """Return breadcrumb string for an item's parent chain"""
    if not parent_id:
        return ""
    crumbs = get_breadcrumb(conn, parent_id)
    return " > ".join(c["short_name"] for c in crumbs)

# --- Models ---
class ItemCreate(BaseModel):
    short_name: str
    description: Optional[str] = ""
    parent_id: Optional[int] = None

class ItemUpdate(BaseModel):
    short_name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[int] = None

class LoginRequest(BaseModel):
    password: str

# --- Auth Routes ---
@app.post("/api/login")
def login(data: LoginRequest, response: Response):
    if data.password != PASSWORD:
        raise HTTPException(status_code=401, detail="Wrong password")
    token = create_session_token()
    response.set_cookie("session", token, max_age=SESSION_MAX_AGE, httponly=True, samesite="lax")
    return {"message": "Logged in"}

@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie("session")
    return {"message": "Logged out"}

@app.get("/api/me")
def me(request: Request):
    return {"logged_in": verify_session(request)}

# --- API Routes ---
@app.get("/api/items")
def list_items(request: Request):
    require_auth(request)
    conn = get_db()
    items = conn.execute("SELECT * FROM items ORDER BY id DESC").fetchall()
    result = []
    for i in items:
        item = dict(i)
        item["breadcrumb"] = get_breadcrumb_string(conn, i["parent_id"])
        result.append(item)
    conn.close()
    return result

@app.get("/api/items/{item_id}")
def get_item(item_id: int, request: Request):
    require_auth(request)
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item = dict(item)
    if item["parent_id"]:
        parent = conn.execute("SELECT id, short_name FROM items WHERE id = ?", (item["parent_id"],)).fetchone()
        item["parent"] = dict(parent) if parent else None
    else:
        item["parent"] = None
    item["breadcrumb"] = get_breadcrumb(conn, item_id)
    conn.close()
    return item

@app.post("/api/items")
def create_item(data: ItemCreate, request: Request):
    require_auth(request)
    new_id = random.randint(1000000000, 9999999999)
    conn = get_db()
    conn.execute(
        "INSERT INTO items (id, short_name, description, parent_id) VALUES (?, ?, ?, ?)",
        (new_id, data.short_name, data.description, data.parent_id)
    )
    conn.commit()
    conn.close()
    return {"id": new_id, "message": "Item created"}

@app.put("/api/items/{item_id}")
def update_item(item_id: int, data: ItemUpdate, request: Request):
    require_auth(request)
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item = dict(item)
    new_name = data.short_name if data.short_name is not None else item["short_name"]
    new_desc = data.description if data.description is not None else item["description"]
    new_parent = data.parent_id if data.parent_id is not None else item["parent_id"]
    conn.execute(
        "UPDATE items SET short_name=?, description=?, parent_id=?, updated_at=datetime('now') WHERE id=?",
        (new_name, new_desc, new_parent, item_id)
    )
    conn.commit()
    conn.close()
    return {"message": "Updated"}

@app.delete("/api/items/{item_id}")
def delete_item(item_id: int, request: Request):
    require_auth(request)
    conn = get_db()
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return {"message": "Deleted"}

@app.get("/api/items/{item_id}/qr")
def get_qr(item_id: int, request: Request):
    require_auth(request)
    qr = qrcode.QRCode(box_size=6, border=2)
    qr.add_data(f"http://localhost:8000/item/{item_id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"qr": f"data:image/png;base64,{b64}"}

# --- Frontend ---
HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Inventory System</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', sans-serif; background: #f0f2f5; color: #222; }

  .login-page {
    display: none; min-height: 100vh;
    align-items: center; justify-content: center; background: #f0f2f5;
  }
  .login-page.active { display: flex; }
  .login-card {
    background: white; border-radius: 16px; padding: 40px;
    width: 100%; max-width: 380px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.1); text-align: center;
  }
  .login-card h1 { font-size: 1.5rem; color: #2d3a8c; margin-bottom: 6px; }
  .login-card p { color: #aaa; font-size: 0.88rem; margin-bottom: 28px; }
  .login-card input {
    width: 100%; padding: 12px 14px;
    border: 1px solid #ddd; border-radius: 10px;
    font-size: 0.95rem; outline: none; margin-bottom: 14px;
    text-align: center; letter-spacing: 2px;
  }
  .login-card input:focus { border-color: #2d3a8c; }
  .login-card button {
    width: 100%; padding: 12px; background: #2d3a8c; color: white;
    border: none; border-radius: 10px; font-size: 0.95rem; font-weight: 600; cursor: pointer;
  }
  .login-card button:hover { background: #1e2a6e; }
  .login-error { color: #e74c3c; font-size: 0.82rem; margin-top: 10px; display: none; }

  .app-page { display: none; }
  .app-page.active { display: block; }

  .topbar {
    background: #2d3a8c; color: white;
    padding: 14px 24px; display: flex;
    align-items: center; justify-content: space-between;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
  }
  .topbar h1 { font-size: 1.2rem; font-weight: 600; }
  .topbar-right { display: flex; align-items: center; gap: 16px; }
  .topbar-right span { font-size: 0.85rem; opacity: 0.8; }
  .logout-btn {
    background: rgba(255,255,255,0.15); border: 1px solid rgba(255,255,255,0.3);
    color: white; padding: 5px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer;
  }
  .logout-btn:hover { background: rgba(255,255,255,0.25); }

  .container { max-width: 1100px; margin: 0 auto; padding: 24px 16px; }
  .search-bar { display: flex; gap: 10px; margin-bottom: 20px; }
  .search-bar input {
    flex: 1; padding: 10px 14px; border: 1px solid #ddd;
    border-radius: 8px; font-size: 0.95rem; outline: none; background: white;
  }
  .search-bar input:focus { border-color: #2d3a8c; }

  .btn {
    padding: 10px 18px; border: none; border-radius: 8px;
    cursor: pointer; font-size: 0.9rem; font-weight: 500; transition: opacity 0.15s;
  }
  .btn:hover { opacity: 0.85; }
  .btn-primary { background: #2d3a8c; color: white; }
  .btn-danger { background: #e74c3c; color: white; }
  .btn-secondary { background: #95a5a6; color: white; }
  .btn-sm { padding: 6px 12px; font-size: 0.8rem; }

  .card { background: white; border-radius: 12px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); overflow: hidden; }
  table { width: 100%; border-collapse: collapse; }
  thead { background: #f7f8fc; }
  th { text-align: left; padding: 12px 16px; font-size: 0.8rem; text-transform: uppercase; color: #888; letter-spacing: 0.5px; border-bottom: 1px solid #eee; }
  td { padding: 10px 16px; border-bottom: 1px solid #f0f0f0; font-size: 0.9rem; vertical-align: top; }
  tr:last-child td { border-bottom: none; }
  tr:hover td { background: #f9f9ff; cursor: pointer; }
  .id-cell { font-family: monospace; font-size: 0.78rem; color: #aaa; white-space: nowrap; }
  .name-cell { font-weight: 500; color: #2d3a8c; }
  .breadcrumb-cell { font-size: 0.75rem; color: #bbb; margin-top: 2px; }
  .desc-cell { color: #666; max-width: 360px; font-size: 0.85rem; }

  .modal-overlay {
    display: none; position: fixed; inset: 0;
    background: rgba(0,0,0,0.4); z-index: 100;
    align-items: center; justify-content: center;
  }
  .modal-overlay.active { display: flex; }
  .modal {
    background: white; border-radius: 14px; padding: 28px;
    width: 100%; max-width: 540px;
    box-shadow: 0 8px 32px rgba(0,0,0,0.18);
    position: relative; max-height: 90vh; overflow-y: auto;
  }
  .modal h2 { font-size: 1.1rem; margin-bottom: 20px; color: #2d3a8c; }
  .modal-close {
    position: absolute; top: 16px; right: 18px;
    background: none; border: none; font-size: 1.4rem; cursor: pointer; color: #aaa;
  }

  .form-group { margin-bottom: 16px; }
  .form-group label { display: block; font-size: 0.82rem; font-weight: 600; color: #555; margin-bottom: 6px; }
  .form-group input, .form-group textarea, .form-group select {
    width: 100%; padding: 9px 12px; border: 1px solid #ddd;
    border-radius: 8px; font-size: 0.9rem; outline: none; font-family: inherit;
  }
  .form-group input:focus, .form-group textarea:focus, .form-group select:focus { border-color: #2d3a8c; }
  .form-group textarea { resize: vertical; min-height: 80px; }

  .breadcrumb-trail {
    display: flex; align-items: center; flex-wrap: wrap;
    gap: 4px; margin-bottom: 14px; font-size: 0.82rem;
  }
  .breadcrumb-trail .crumb { color: #2d3a8c; cursor: pointer; font-weight: 500; }
  .breadcrumb-trail .crumb:hover { text-decoration: underline; }
  .breadcrumb-trail .crumb.current { color: #333; cursor: default; font-weight: 600; }
  .breadcrumb-trail .crumb.current:hover { text-decoration: none; }
  .breadcrumb-sep { color: #ccc; }

  .meta { font-size: 0.78rem; color: #aaa; margin-bottom: 4px; }
  .item-id-display { font-family: monospace; font-size: 0.78rem; color: #aaa; margin-bottom: 4px; }
  .divider { border: none; border-top: 1px solid #eee; margin: 18px 0; }
  .actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 16px; }

  .qr-section { text-align: center; margin-top: 16px; padding-top: 16px; border-top: 1px solid #eee; }
  .qr-section img { width: 120px; height: 120px; }
  .qr-section p { font-size: 0.75rem; color: #aaa; margin-top: 6px; }

  .empty-state { text-align: center; padding: 60px 20px; color: #bbb; }
  .empty-state p { font-size: 1rem; margin-bottom: 8px; }
</style>
</head>
<body>

<div class="login-page" id="loginPage">
  <div class="login-card">
    <h1>📦 Inventory</h1>
    <p>Enter your password to continue</p>
    <input type="password" id="passwordInput" placeholder="Password" onkeydown="if(event.key==='Enter') doLogin()">
    <button onclick="doLogin()">Login</button>
    <div class="login-error" id="loginError">Wrong password, try again.</div>
  </div>
</div>

<div class="app-page" id="appPage">
  <div class="topbar">
    <h1>📦 Inventory System</h1>
    <div class="topbar-right">
      <span>Logged in as: Chyn</span>
      <button class="logout-btn" onclick="doLogout()">Logout</button>
    </div>
  </div>
  <div class="container">
    <div class="search-bar">
      <input type="text" id="searchInput" placeholder="Search by name, description, location, or ID..." oninput="filterItems()">
      <button class="btn btn-primary" onclick="openAddModal()">+ Add Item</button>
    </div>
    <div class="card">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Name / Location</th>
            <th>Description</th>
          </tr>
        </thead>
        <tbody id="itemsTable">
          <tr><td colspan="3"><div class="empty-state"><p>Loading...</p></div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<!-- Detail Modal -->
<div class="modal-overlay" id="detailModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('detailModal')">×</button>
    <div id="detailContent"></div>
  </div>
</div>

<!-- Add Modal -->
<div class="modal-overlay" id="addModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('addModal')">×</button>
    <h2>Add New Item</h2>
    <div class="form-group">
      <label>Short Name *</label>
      <input type="text" id="addName" placeholder="e.g. M3 screws 10mm">
    </div>
    <div class="form-group">
      <label>Description</label>
      <textarea id="addDesc" placeholder="Notes, quantity, specs, serial numbers..."></textarea>
    </div>
    <div class="form-group">
      <label>Parent (Location)</label>
      <select id="addParent">
        <option value="">— No parent (top level) —</option>
      </select>
    </div>
    <div class="actions">
      <button class="btn btn-primary" onclick="submitAdd()">Save Item</button>
      <button class="btn btn-secondary" onclick="closeModal('addModal')">Cancel</button>
    </div>
  </div>
</div>

<script>
let allItems = [];

async function checkAuth() {
  const res = await fetch('/api/me');
  const data = await res.json();
  if (data.logged_in) showApp();
  else showLogin();
}

function showLogin() {
  document.getElementById('loginPage').classList.add('active');
  document.getElementById('appPage').classList.remove('active');
}

function showApp() {
  document.getElementById('loginPage').classList.remove('active');
  document.getElementById('appPage').classList.add('active');
  loadItems();
  const path = window.location.pathname;
  if (path.match(/^\\/item\\/\\d+$/)) {
    const id = parseInt(path.split('/').pop());
    setTimeout(() => openDetail(id), 400);
  }
}

async function doLogin() {
  const pw = document.getElementById('passwordInput').value;
  const res = await fetch('/api/login', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ password: pw })
  });
  if (res.ok) {
    document.getElementById('loginError').style.display = 'none';
    showApp();
  } else {
    document.getElementById('loginError').style.display = 'block';
    document.getElementById('passwordInput').value = '';
    document.getElementById('passwordInput').focus();
  }
}

async function doLogout() {
  await fetch('/api/logout', { method: 'POST' });
  showLogin();
}

async function loadItems() {
  const res = await fetch('/api/items');
  if (res.status === 401) { showLogin(); return; }
  allItems = await res.json();
  renderTable(allItems);
}

function renderTable(items) {
  const tbody = document.getElementById('itemsTable');
  if (!items.length) {
    tbody.innerHTML = `<tr><td colspan="3"><div class="empty-state"><p>No items yet</p><span>Click "+ Add Item" to get started</span></div></td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(i => `
    <tr onclick="openDetail(${i.id})">
      <td class="id-cell">${i.id}</td>
      <td>
        <div class="name-cell">${i.short_name}</div>
        ${i.breadcrumb ? `<div class="breadcrumb-cell">📁 ${i.breadcrumb}</div>` : ''}
      </td>
      <td class="desc-cell">${(i.description || '').substring(0, 100)}${(i.description||'').length > 100 ? '…' : ''}</td>
    </tr>
  `).join('');
}

function filterItems() {
  const q = document.getElementById('searchInput').value.toLowerCase();
  const filtered = allItems.filter(i =>
    i.short_name.toLowerCase().includes(q) ||
    (i.description || '').toLowerCase().includes(q) ||
    String(i.id).includes(q) ||
    (i.breadcrumb || '').toLowerCase().includes(q)
  );
  renderTable(filtered);
}

async function openDetail(id) {
  const item = await fetch(`/api/items/${id}`).then(r => r.json());
  const qrData = await fetch(`/api/items/${id}/qr`).then(r => r.json());

  const crumbs = item.breadcrumb || [];
  let breadcrumbHtml = '';
  if (crumbs.length > 1) {
    breadcrumbHtml = '<div class="breadcrumb-trail">';
    crumbs.forEach((crumb, idx) => {
      const isCurrent = idx === crumbs.length - 1;
      if (idx > 0) breadcrumbHtml += '<span class="breadcrumb-sep">›</span>';
      if (isCurrent) {
        breadcrumbHtml += `<span class="crumb current">${crumb.short_name}</span>`;
      } else {
        breadcrumbHtml += `<span class="crumb" onclick="closeModal('detailModal'); setTimeout(()=>openDetail(${crumb.id}),100)">${crumb.short_name}</span>`;
      }
    });
    breadcrumbHtml += '</div>';
  }

  document.getElementById('detailContent').innerHTML = `
    <div class="item-id-display">ID ${item.id}</div>
    ${breadcrumbHtml}
    <h2>${item.short_name}</h2>
    <div class="meta">Last modified: ${item.updated_at}</div>
    <hr class="divider">
    <div class="form-group">
      <label>Short Name</label>
      <input type="text" id="editName" value="${item.short_name}">
    </div>
    <div class="form-group">
      <label>Description</label>
      <textarea id="editDesc">${item.description || ''}</textarea>
    </div>
    <div class="form-group">
      <label>Parent (Location)</label>
      <select id="editParent">
        <option value="">— No parent —</option>
      </select>
    </div>
    <div class="actions">
      <button class="btn btn-primary btn-sm" onclick="saveEdit(${id})">Save</button>
      <button class="btn btn-danger btn-sm" onclick="deleteItem(${id})">Delete</button>
      <button class="btn btn-secondary btn-sm" onclick="closeModal('detailModal')">Cancel</button>
    </div>
    <div class="qr-section">
      <img src="${qrData.qr}" alt="QR Code">
      <p>Scan to open this item</p>
      <button class="btn btn-secondary btn-sm" style="margin-top:8px" onclick="downloadQR('${qrData.qr}', ${id})">⬇ Download QR</button>
    </div>
  `;

  populateParentSelect('editParent', item.id, item.parent_id);
  document.getElementById('detailModal').classList.add('active');
}

function populateParentSelect(selectId, excludeId = null, selectedId = null) {
  const select = document.getElementById(selectId);
  const items = allItems.filter(i => i.id !== excludeId);
  items.forEach(i => {
    const opt = document.createElement('option');
    opt.value = i.id;
    const label = i.breadcrumb ? `${i.breadcrumb} > ${i.short_name}` : i.short_name;
    opt.textContent = label;
    if (selectedId && i.id === selectedId) opt.selected = true;
    select.appendChild(opt);
  });
}

async function saveEdit(id) {
  const name = document.getElementById('editName').value.trim();
  const desc = document.getElementById('editDesc').value.trim();
  const parentVal = document.getElementById('editParent').value;
  if (!name) { alert('Short name is required'); return; }
  await fetch(`/api/items/${id}`, {
    method: 'PUT',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ short_name: name, description: desc, parent_id: parentVal ? parseInt(parentVal) : null })
  });
  closeModal('detailModal');
  loadItems();
}

async function deleteItem(id) {
  if (!confirm('Delete this item? Children will become parentless.')) return;
  await fetch(`/api/items/${id}`, { method: 'DELETE' });
  closeModal('detailModal');
  loadItems();
}

async function openAddModal() {
  document.getElementById('addName').value = '';
  document.getElementById('addDesc').value = '';
  const sel = document.getElementById('addParent');
  sel.innerHTML = '<option value="">— No parent (top level) —</option>';
  populateParentSelect('addParent');
  document.getElementById('addModal').classList.add('active');
  setTimeout(() => document.getElementById('addName').focus(), 100);
}

async function submitAdd() {
  const name = document.getElementById('addName').value.trim();
  const desc = document.getElementById('addDesc').value.trim();
  const parentVal = document.getElementById('addParent').value;
  if (!name) { alert('Short name is required'); return; }
  await fetch('/api/items', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ short_name: name, description: desc, parent_id: parentVal ? parseInt(parentVal) : null })
  });
  closeModal('addModal');
  loadItems();
}

function closeModal(id) {
  document.getElementById(id).classList.remove('active');
}

function downloadQR(dataUrl, itemId) {
  const a = document.createElement('a');
  a.href = dataUrl;
  a.download = `item_${itemId}_qr.png`;
  a.click();
}

document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => {
    if (e.target === overlay) overlay.classList.remove('active');
  });
});

checkAuth();
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    return HTML

@app.get("/item/{item_id}", response_class=HTMLResponse)
def item_page(item_id: int):
    return HTML

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
