from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
import sqlite3
import random
import qrcode
import base64
import requests as req_lib
import tempfile
import os
from io import BytesIO
from datetime import datetime
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from PIL import Image, ImageDraw, ImageFont

app = FastAPI()

DB_PATH = "/app/data/inventory.db"
SECRET_KEY = "change-this-to-something-secret"
PASSWORD = "homelab123"
SESSION_MAX_AGE = 60 * 60 * 24 * 7
serializer = URLSafeTimedSerializer(SECRET_KEY)

# --- Printer config ---
NIIMBLUE_SERVER = os.environ.get("NIIMBLUE_SERVER", "http://172.17.0.1:5000")
PRINTER_MAC = os.environ.get("PRINTER_MAC", "C3:1B:20:05:13:83")
LABEL_WIDTH = 384
LABEL_HEIGHT = 240
BASE_URL = os.environ.get("BASE_URL", "https://inventory.tanscloud.space")

# --- Auth ---
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

def get_breadcrumb(conn, item_id):
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

# --- Item Routes ---
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
    qr.add_data(f"{BASE_URL}/item/{item_id}")
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode()
    return {"qr": f"data:image/png;base64,{b64}"}

# --- Label generation ---
def generate_label(item_name: str, description: str, item_id: int) -> bytes:
    img = Image.new("RGB", (LABEL_WIDTH, LABEL_HEIGHT), "white")
    draw = ImageDraw.Draw(img)

    PAD = 14

    # ── QR code (right side, full height) ──
    qr_size = LABEL_HEIGHT - PAD * 2
    qr_url = f"{BASE_URL}/item/{item_id}"
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(qr_url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    qr_img = qr_img.resize((qr_size, qr_size), Image.LANCZOS)
    qr_x = LABEL_WIDTH - qr_size - PAD
    qr_y = PAD
    img.paste(qr_img, (qr_x, qr_y))

    # ── Separator ──
    sep_x = qr_x - 12
    draw.line([(sep_x, PAD + 4), (sep_x, LABEL_HEIGHT - PAD - 4)], fill="#dddddd", width=1)

    # ── Text area ──
    text_w = sep_x - PAD - 8
    text_x = PAD

    # ── Fonts ──
    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 32)
        font_desc  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
        font_id    = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 18)
    except:
        font_title = ImageFont.load_default()
        font_desc  = font_title
        font_id    = font_title

    def line_h(font):
        return draw.textbbox((0, 0), "Ag", font=font)[3] + 4

    def wrap_text(text, font, max_width):
        words = text.split()
        if not words:
            return []
        lines, current = [], ""
        for word in words:
            test = (current + " " + word).strip()
            if draw.textbbox((0, 0), test, font=font)[2] <= max_width:
                current = test
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines

    def expand_text(text, font, max_width):
        """Split by newlines first, then word-wrap each chunk."""
        all_lines = []
        for chunk in text.split("\n"):
            chunk = chunk.strip()
            if chunk:
                all_lines.extend(wrap_text(chunk, font, max_width))
            else:
                all_lines.append("")  # blank = small gap
        return all_lines

    # ── Reserve space for ID at bottom ──
    id_text = f"#{item_id}"
    id_h = line_h(font_id)
    id_y = LABEL_HEIGHT - PAD - id_h
    bottom_limit = id_y - 6

    # ── Title ──
    title_lines = wrap_text(item_name, font_title, text_w)
    y = PAD + 2
    for line in title_lines[:3]:
        draw.text((text_x, y), line, font=font_title, fill="#111111")
        y += line_h(font_title)

    # ── Description — fit as many lines as space allows ──
    if description and description.strip():
        y += 8
        desc_lines = expand_text(description.strip(), font_desc, text_w)
        for line in desc_lines:
            if y + line_h(font_desc) > bottom_limit:
                break
            if line == "":
                y += line_h(font_desc) // 2
            else:
                draw.text((text_x, y), line, font=font_desc, fill="#555555")
                y += line_h(font_desc)

    # ── ID anchored at bottom left ──
    draw.text((text_x, id_y), id_text, font=font_id, fill="#bbbbbb")

    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

# --- Niimblue helpers ---
def ensure_printer_connected():
    try:
        r = req_lib.get(f"{NIIMBLUE_SERVER}/connected", timeout=5)
        data = r.json()
        if data.get("connected"):
            return True
        r = req_lib.post(f"{NIIMBLUE_SERVER}/connect", json={
            "transport": "ble",
            "address": PRINTER_MAC
        }, timeout=20)
        if r.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Connect failed: {r.text}")
        import time
        time.sleep(2)
        return True
    except req_lib.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Niimblue server not reachable. Is it running on the host?")

# --- Print Route ---
@app.post("/api/items/{item_id}/print")
def print_label(item_id: int, request: Request):
    require_auth(request)
    conn = get_db()
    item = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item = dict(item)
    description = item.get("description") or ""
    conn.close()

    label_bytes = generate_label(item["short_name"], description, item_id)
    label_b64 = base64.b64encode(label_bytes).decode()

    ensure_printer_connected()

    try:
        r = req_lib.post(f"{NIIMBLUE_SERVER}/print", json={
            "imageBase64": label_b64,
            "printTask": "D110M_V4",
            "labelWidth": LABEL_WIDTH,
            "labelHeight": LABEL_HEIGHT,
            "quantity": 1,
            "density": 3,
            "printDirection": "top"
        }, timeout=30)
        if r.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Print failed: {r.text}")
        return {"message": "Printed successfully"}
    except req_lib.exceptions.ConnectionError:
        raise HTTPException(status_code=503, detail="Niimblue server not reachable")

# --- Frontend HTML ---
HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Inventory</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,300;0,9..144,600;0,9..144,900;1,9..144,300&family=DM+Sans:wght@300;400;500&display=swap" rel="stylesheet">
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

:root {
  --cream: #f5f0e8;
  --cream-dark: #ede8dc;
  --ink: #1a1208;
  --ink-light: #3a2e1a;
  --warm: #c8a96e;
  --rust: #c4522a;
  --sage: #4a6741;
  --border: #d8d0c0;
  --muted: #8a7a60;
  --shadow: 4px 4px 0px var(--border);
  --shadow-lg: 6px 6px 0px var(--border);
}

body {
  font-family: 'DM Sans', sans-serif;
  background: var(--cream);
  color: var(--ink);
  min-height: 100vh;
}

body::before {
  content: '';
  position: fixed;
  inset: 0;
  opacity: 0.025;
  background-image: url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='noise'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23noise)'/%3E%3C/svg%3E");
  background-repeat: repeat;
  background-size: 128px;
  pointer-events: none;
  z-index: 9999;
}

.login-page { display: none; min-height: 100vh; align-items: center; justify-content: center; }
.login-page.active { display: flex; }

.login-card {
  background: white; border: 1.5px solid var(--border); border-radius: 24px;
  box-shadow: var(--shadow-lg); padding: 48px 40px; width: 100%; max-width: 360px; text-align: center;
}
.login-logo { font-family: 'Fraunces', serif; font-size: 2.4rem; font-weight: 900; line-height: 1; margin-bottom: 8px; }
.login-logo .accent { color: var(--rust); font-style: italic; font-weight: 300; }
.login-sub { color: var(--muted); font-size: 0.85rem; margin-bottom: 32px; }
.login-card input[type="password"] {
  width: 100%; padding: 12px 16px; border: 1.5px solid var(--border); border-radius: 12px;
  background: var(--cream); font-family: 'DM Sans', sans-serif; font-size: 0.95rem;
  letter-spacing: 3px; text-align: center; outline: none; margin-bottom: 14px; transition: border-color 0.2s;
}
.login-card input:focus { border-color: var(--warm); background: white; }
.btn-login {
  width: 100%; padding: 12px; background: var(--ink); color: var(--cream); border: none;
  border-radius: 12px; font-family: 'Fraunces', serif; font-size: 1rem; font-weight: 600;
  cursor: pointer; box-shadow: 3px 3px 0 var(--warm); transition: all 0.15s;
}
.btn-login:hover { transform: translate(-1px,-1px); box-shadow: 4px 4px 0 var(--warm); }
.btn-login:active { transform: translate(1px,1px); box-shadow: 2px 2px 0 var(--warm); }
.login-error { color: var(--rust); font-size: 0.82rem; margin-top: 10px; display: none; }

.app-page { display: none; }
.app-page.active { display: block; }

.topbar {
  background: var(--ink); color: var(--cream); padding: 0 28px; height: 60px;
  display: flex; align-items: center; justify-content: space-between; position: sticky; top: 0; z-index: 100;
}
.topbar-logo { font-family: 'Fraunces', serif; font-size: 1.4rem; font-weight: 900; letter-spacing: -0.02em; }
.topbar-logo .accent { color: var(--warm); font-style: italic; font-weight: 300; }
.topbar-right { display: flex; align-items: center; gap: 20px; }
.topbar-user { font-size: 0.82rem; color: rgba(245,240,232,0.5); }
.btn-logout {
  background: rgba(245,240,232,0.1); border: 1px solid rgba(245,240,232,0.2); color: var(--cream);
  padding: 6px 14px; border-radius: 8px; font-size: 0.8rem; font-family: 'DM Sans', sans-serif;
  cursor: pointer; transition: background 0.15s;
}
.btn-logout:hover { background: rgba(245,240,232,0.2); }

.container { max-width: 1000px; margin: 0 auto; padding: 32px 20px; }

.toolbar { display: flex; gap: 10px; margin-bottom: 24px; }
.search-wrap { flex: 1; position: relative; }
.search-icon { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--muted); pointer-events: none; }
.search-input {
  width: 100%; padding: 11px 16px 11px 40px; background: white; border: 1.5px solid var(--border);
  border-radius: 12px; font-family: 'DM Sans', sans-serif; font-size: 0.92rem; color: var(--ink);
  outline: none; box-shadow: var(--shadow); transition: border-color 0.2s, box-shadow 0.2s;
}
.search-input:focus { border-color: var(--warm); box-shadow: 4px 4px 0 var(--warm); }
.search-input::placeholder { color: var(--muted); }
.btn-add {
  padding: 11px 20px; background: var(--ink); color: var(--cream); border: none; border-radius: 12px;
  font-family: 'Fraunces', serif; font-size: 0.95rem; font-weight: 600; cursor: pointer;
  white-space: nowrap; box-shadow: var(--shadow); transition: all 0.15s;
}
.btn-add:hover { transform: translate(-1px,-1px); box-shadow: var(--shadow-lg); }
.btn-add:active { transform: translate(1px,1px); box-shadow: 2px 2px 0 var(--border); }

.card { background: white; border: 1.5px solid var(--border); border-radius: 18px; box-shadow: var(--shadow-lg); overflow: hidden; }
table { width: 100%; border-collapse: collapse; }
thead { background: var(--cream-dark); border-bottom: 1.5px solid var(--border); }
th { padding: 12px 18px; font-size: 0.72rem; font-weight: 500; text-transform: uppercase; letter-spacing: 0.08em; color: var(--muted); text-align: left; }
td { padding: 13px 18px; border-bottom: 1px solid #f0ece4; vertical-align: middle; }
tr:last-child td { border-bottom: none; }
tr:hover td { background: #faf7f2; cursor: pointer; }
.id-cell { font-size: 0.72rem; color: var(--muted); white-space: nowrap; width: 120px; }
.name-cell { font-weight: 500; font-size: 0.92rem; color: var(--ink); }
.breadcrumb-cell { font-size: 0.75rem; color: var(--muted); margin-top: 3px; }
.desc-cell { color: #6a5a40; font-size: 0.85rem; max-width: 300px; }
.empty-state { text-align: center; padding: 64px 20px; color: var(--muted); }
.empty-state .empty-icon { font-size: 2.5rem; margin-bottom: 12px; opacity: 0.5; }
.empty-state p { font-size: 0.95rem; margin-bottom: 6px; color: var(--ink-light); }
.empty-state span { font-size: 0.82rem; }

.modal-overlay {
  display: none; position: fixed; inset: 0; background: rgba(26,18,8,0.55); z-index: 200;
  align-items: flex-start; justify-content: center; padding: 40px 20px; overflow-y: auto; backdrop-filter: blur(2px);
}
.modal-overlay.active { display: flex; }
.modal {
  background: white; border: 1.5px solid var(--border); border-radius: 20px; box-shadow: var(--shadow-lg);
  width: 100%; max-width: 520px; padding: 32px; position: relative; animation: modalIn 0.2s ease;
}
@keyframes modalIn { from { opacity: 0; transform: translateY(-12px); } to { opacity: 1; transform: translateY(0); } }
.modal-close {
  position: absolute; top: 16px; right: 18px; background: var(--cream); border: 1.5px solid var(--border);
  border-radius: 8px; width: 32px; height: 32px; font-size: 1.1rem; cursor: pointer; color: var(--muted);
  display: flex; align-items: center; justify-content: center; transition: all 0.15s;
}
.modal-close:hover { background: var(--cream-dark); color: var(--ink); }
.modal-title { font-family: 'Fraunces', serif; font-size: 1.4rem; font-weight: 600; color: var(--ink); margin-bottom: 6px; }
.modal-subtitle { font-size: 0.8rem; color: var(--muted); margin-bottom: 24px; }

.item-id-tag {
  display: inline-block; background: var(--cream); border: 1px solid var(--border); border-radius: 6px;
  padding: 3px 10px; font-size: 0.72rem; color: var(--muted); margin-bottom: 10px; font-weight: 500;
}
.breadcrumb-trail { display: flex; align-items: center; flex-wrap: wrap; gap: 4px; margin-bottom: 6px; font-size: 0.8rem; }
.crumb { color: var(--warm); cursor: pointer; font-weight: 500; transition: color 0.15s; }
.crumb:hover { color: var(--rust); }
.crumb.current { color: var(--muted); cursor: default; }
.breadcrumb-sep { color: var(--border); }
.item-name-display { font-family: 'Fraunces', serif; font-size: 1.6rem; font-weight: 600; color: var(--ink); margin-bottom: 4px; line-height: 1.2; }
.item-meta { font-size: 0.75rem; color: var(--muted); margin-bottom: 20px; }
.divider { border: none; border-top: 1.5px solid var(--cream-dark); margin: 20px 0; }

.form-group { margin-bottom: 16px; }
.form-group label { display: block; font-size: 0.78rem; font-weight: 500; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 6px; }
.form-group input, .form-group textarea, .form-group select {
  width: 100%; padding: 10px 14px; background: var(--cream); border: 1.5px solid var(--border);
  border-radius: 10px; font-family: 'DM Sans', sans-serif; font-size: 0.9rem; color: var(--ink);
  outline: none; transition: border-color 0.2s, background 0.2s;
}
.form-group input:focus, .form-group textarea:focus, .form-group select:focus { border-color: var(--warm); background: white; }
.form-group textarea { resize: vertical; min-height: 80px; }

.actions { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 20px; align-items: center; }
.btn { padding: 9px 18px; border: none; border-radius: 10px; cursor: pointer; font-family: 'DM Sans', sans-serif; font-size: 0.88rem; font-weight: 500; transition: all 0.15s; }
.btn-primary { background: var(--ink); color: var(--cream); box-shadow: 3px 3px 0 var(--warm); }
.btn-primary:hover { transform: translate(-1px,-1px); box-shadow: 4px 4px 0 var(--warm); }
.btn-primary:active { transform: translate(1px,1px); box-shadow: 2px 2px 0 var(--warm); }
.btn-danger { background: #fdf0ec; color: var(--rust); border: 1.5px solid #f0c4b4; box-shadow: 3px 3px 0 #f0c4b4; }
.btn-danger:hover { transform: translate(-1px,-1px); box-shadow: 4px 4px 0 #f0c4b4; background: #fbe8e2; }
.btn-danger:active { transform: translate(1px,1px); box-shadow: 2px 2px 0 #f0c4b4; }
.btn-secondary { background: var(--cream); color: var(--ink); border: 1.5px solid var(--border); box-shadow: 3px 3px 0 var(--border); }
.btn-secondary:hover { transform: translate(-1px,-1px); box-shadow: 4px 4px 0 var(--border); background: var(--cream-dark); }
.btn-secondary:active { transform: translate(1px,1px); box-shadow: 2px 2px 0 var(--border); }
.btn-print { background: var(--sage); color: white; border: none; box-shadow: 3px 3px 0 #2a4a27; margin-left: auto; }
.btn-print:hover { transform: translate(-1px,-1px); box-shadow: 4px 4px 0 #2a4a27; background: #3a5a37; }
.btn-print:active { transform: translate(1px,1px); box-shadow: 2px 2px 0 #2a4a27; }
.btn-print:disabled { background: #b0b8af; box-shadow: none; transform: none; cursor: not-allowed; }
.btn-print.printing { opacity: 0.7; cursor: wait; }
.btn-sm { padding: 6px 14px; font-size: 0.82rem; }

.qr-section { text-align: center; margin-top: 24px; padding: 20px; background: var(--cream); border: 1.5px solid var(--border); border-radius: 14px; }
.qr-section img { width: 110px; height: 110px; border-radius: 6px; border: 1.5px solid var(--border); }
.qr-section p { font-size: 0.75rem; color: var(--muted); margin-top: 8px; }
.qr-actions { display: flex; gap: 8px; justify-content: center; margin-top: 10px; }

.toast-container { position: fixed; bottom: 24px; right: 24px; z-index: 9000; display: flex; flex-direction: column; gap: 8px; }
.toast { padding: 12px 18px; border-radius: 100px; font-size: 0.85rem; font-weight: 500; box-shadow: 3px 3px 0 var(--border); animation: toastIn 0.2s ease, toastOut 0.3s ease 2.7s forwards; border: 1.5px solid; }
.toast.success { background: #eef4ee; border-color: #b4d4b4; color: var(--sage); }
.toast.error { background: #fdf0ec; border-color: #f0c4b4; color: var(--rust); }
.toast.info { background: white; border-color: var(--border); color: var(--ink); }
@keyframes toastIn { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@keyframes toastOut { from { opacity: 1; } to { opacity: 0; } }

@media (max-width: 600px) {
  .topbar { padding: 0 16px; }
  .container { padding: 20px 12px; }
  .modal { padding: 24px 18px; border-radius: 14px; }
  .topbar-user { display: none; }
  th:last-child, td:last-child { display: none; }
}
</style>
</head>
<body>

<div class="toast-container" id="toastContainer"></div>

<div class="login-page" id="loginPage">
  <div class="login-card">
    <div class="login-logo"><span class="accent">inv</span>entory</div>
    <p class="login-sub">Enter your password to continue</p>
    <input type="password" id="passwordInput" placeholder="••••••••" onkeydown="if(event.key==='Enter') doLogin()">
    <button class="btn-login" onclick="doLogin()">Enter</button>
    <div class="login-error" id="loginError">Wrong password, try again.</div>
  </div>
</div>

<div class="app-page" id="appPage">
  <div class="topbar">
    <div class="topbar-logo"><span class="accent">inv</span>entory</div>
    <div class="topbar-right">
      <span class="topbar-user">Chyn's Home Lab</span>
      <button class="btn-logout" onclick="doLogout()">Logout</button>
    </div>
  </div>
  <div class="container">
    <div class="toolbar">
      <div class="search-wrap">
        <span class="search-icon">🔍</span>
        <input class="search-input" type="text" id="searchInput" placeholder="Search by name, location, ID…" oninput="filterItems()">
      </div>
      <button class="btn-add" onclick="openAddModal()">+ Add Item</button>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>ID</th><th>Name / Location</th><th>Description</th></tr></thead>
        <tbody id="itemsTable">
          <tr><td colspan="3"><div class="empty-state"><p>Loading…</p></div></td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>

<div class="modal-overlay" id="detailModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('detailModal')">×</button>
    <div id="detailContent"></div>
  </div>
</div>

<div class="modal-overlay" id="addModal">
  <div class="modal">
    <button class="modal-close" onclick="closeModal('addModal')">×</button>
    <div class="modal-title">New Item</div>
    <div class="modal-subtitle">Add something to your inventory</div>
    <div class="form-group">
      <label>Name *</label>
      <input type="text" id="addName" placeholder="e.g. M3 screws 10mm">
    </div>
    <div class="form-group">
      <label>Description</label>
      <textarea id="addDesc" placeholder="Notes, quantity, specs…&#10;Each line prints separately on the label"></textarea>
    </div>
    <div class="form-group">
      <label>Location (Parent)</label>
      <select id="addParent"><option value="">— Top level —</option></select>
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
  if (path.match(/^\/item\/\d+$/)) {
    const id = parseInt(path.split('/').pop());
    setTimeout(() => openDetail(id), 400);
  }
}

async function doLogin() {
  const pw = document.getElementById('passwordInput').value;
  const res = await fetch('/api/login', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
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

function toast(msg, type = 'info') {
  const container = document.getElementById('toastContainer');
  const el = document.createElement('div');
  el.className = `toast ${type}`;
  el.textContent = msg;
  container.appendChild(el);
  setTimeout(() => el.remove(), 3200);
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
    tbody.innerHTML = `<tr><td colspan="3"><div class="empty-state">
      <div class="empty-icon">📦</div><p>No items yet</p>
      <span>Click "+ Add Item" to get started</span>
    </div></td></tr>`;
    return;
  }
  tbody.innerHTML = items.map(i => `
    <tr onclick="openDetail(${i.id})">
      <td class="id-cell">${i.id}</td>
      <td>
        <div class="name-cell">${escHtml(i.short_name)}</div>
        ${i.breadcrumb ? `<div class="breadcrumb-cell">📁 ${escHtml(i.breadcrumb)}</div>` : ''}
      </td>
      <td class="desc-cell">${escHtml((i.description || '').substring(0, 80))}${(i.description||'').length > 80 ? '…' : ''}</td>
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

function escHtml(str) {
  return String(str).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
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
        breadcrumbHtml += `<span class="crumb current">${escHtml(crumb.short_name)}</span>`;
      } else {
        breadcrumbHtml += `<span class="crumb" onclick="closeModal('detailModal'); setTimeout(()=>openDetail(${crumb.id}),100)">${escHtml(crumb.short_name)}</span>`;
      }
    });
    breadcrumbHtml += '</div>';
  }

  document.getElementById('detailContent').innerHTML = `
    <span class="item-id-tag">#${item.id}</span>
    ${breadcrumbHtml}
    <div class="item-name-display">${escHtml(item.short_name)}</div>
    <div class="item-meta">Last modified: ${item.updated_at}</div>
    <hr class="divider">
    <div class="form-group">
      <label>Name</label>
      <input type="text" id="editName" value="${escHtml(item.short_name)}">
    </div>
    <div class="form-group">
      <label>Description</label>
      <textarea id="editDesc">${escHtml(item.description || '')}</textarea>
    </div>
    <div class="form-group">
      <label>Location (Parent)</label>
      <select id="editParent"><option value="">— No parent —</option></select>
    </div>
    <div class="actions">
      <button class="btn btn-primary btn-sm" onclick="saveEdit(${id})">Save</button>
      <button class="btn btn-danger btn-sm" onclick="deleteItem(${id})">Delete</button>
      <button class="btn btn-secondary btn-sm" onclick="closeModal('detailModal')">Close</button>
      <button class="btn btn-print btn-sm" id="printBtn" onclick="printLabel(${id})">🖨 Print Label</button>
    </div>
    <div class="qr-section">
      <img src="${qrData.qr}" alt="QR Code">
      <p>Scan to open this item</p>
      <div class="qr-actions">
        <button class="btn btn-secondary btn-sm" onclick="downloadQR('${qrData.qr}', ${id})">⬇ Download QR</button>
      </div>
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

async function printLabel(id) {
  const btn = document.getElementById('printBtn');
  if (!btn) return;
  btn.disabled = true;
  btn.classList.add('printing');
  btn.textContent = '⏳ Printing…';
  try {
    const res = await fetch(`/api/items/${id}/print`, { method: 'POST' });
    const data = await res.json();
    if (res.ok) {
      toast('✓ Label printed!', 'success');
      btn.textContent = '✓ Printed';
      setTimeout(() => { btn.textContent = '🖨 Print Label'; btn.disabled = false; btn.classList.remove('printing'); }, 2000);
    } else {
      toast('Print failed: ' + (data.detail || 'Unknown error'), 'error');
      btn.textContent = '🖨 Print Label'; btn.disabled = false; btn.classList.remove('printing');
    }
  } catch(e) {
    toast('Network error', 'error');
    btn.textContent = '🖨 Print Label'; btn.disabled = false; btn.classList.remove('printing');
  }
}

async function saveEdit(id) {
  const name = document.getElementById('editName').value.trim();
  const desc = document.getElementById('editDesc').value.trim();
  const parentVal = document.getElementById('editParent').value;
  if (!name) { toast('Name is required', 'error'); return; }
  await fetch(`/api/items/${id}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ short_name: name, description: desc, parent_id: parentVal ? parseInt(parentVal) : null })
  });
  toast('Saved', 'success');
  closeModal('detailModal');
  loadItems();
}

async function deleteItem(id) {
  if (!confirm('Delete this item?')) return;
  await fetch(`/api/items/${id}`, { method: 'DELETE' });
  toast('Deleted', 'info');
  closeModal('detailModal');
  loadItems();
}

async function openAddModal() {
  document.getElementById('addName').value = '';
  document.getElementById('addDesc').value = '';
  const sel = document.getElementById('addParent');
  sel.innerHTML = '<option value="">— Top level —</option>';
  populateParentSelect('addParent');
  document.getElementById('addModal').classList.add('active');
  setTimeout(() => document.getElementById('addName').focus(), 100);
}

async function submitAdd() {
  const name = document.getElementById('addName').value.trim();
  const desc = document.getElementById('addDesc').value.trim();
  const parentVal = document.getElementById('addParent').value;
  if (!name) { toast('Name is required', 'error'); return; }
  await fetch('/api/items', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ short_name: name, description: desc, parent_id: parentVal ? parseInt(parentVal) : null })
  });
  toast('Item added', 'success');
  closeModal('addModal');
  loadItems();
}

function closeModal(id) { document.getElementById(id).classList.remove('active'); }

function downloadQR(dataUrl, itemId) {
  const a = document.createElement('a');
  a.href = dataUrl; a.download = `item_${itemId}_qr.png`; a.click();
}

document.querySelectorAll('.modal-overlay').forEach(overlay => {
  overlay.addEventListener('click', e => { if (e.target === overlay) overlay.classList.remove('active'); });
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
