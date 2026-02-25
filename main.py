import sqlite3
import os
import io
import time
import random
import qrcode
from fastapi import FastAPI, HTTPException, Depends, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="Inventory System")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_PATH = os.environ.get("DB_PATH", "/data/inventory.db")
BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

def get_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY,
            short_name TEXT NOT NULL,
            description TEXT,
            parent_id INTEGER REFERENCES items(id),
            created_at INTEGER,
            updated_at INTEGER
        )
    """)
    conn.commit()
    conn.close()

init_db()

def generate_id():
    # 10-digit numeric ID like boss's system
    return random.randint(1000000000, 9999999999)

class ItemCreate(BaseModel):
    short_name: str
    description: Optional[str] = None
    parent_id: Optional[int] = None

class ItemUpdate(BaseModel):
    short_name: Optional[str] = None
    description: Optional[str] = None
    parent_id: Optional[int] = None

def get_ancestors(conn, item_id):
    ancestors = []
    current = item_id
    visited = set()
    while current:
        if current in visited:
            break
        visited.add(current)
        row = conn.execute("SELECT id, short_name, parent_id FROM items WHERE id = ?", (current,)).fetchone()
        if not row:
            break
        ancestors.append({"id": row["id"], "short_name": row["short_name"]})
        current = row["parent_id"]
    ancestors.reverse()
    return ancestors

@app.get("/api/items")
def list_items(search: str = ""):
    conn = get_db()
    if search:
        rows = conn.execute(
            "SELECT * FROM items WHERE short_name LIKE ? OR description LIKE ? OR CAST(id AS TEXT) LIKE ? ORDER BY short_name",
            (f"%{search}%", f"%{search}%", f"%{search}%")
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM items ORDER BY short_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

@app.get("/api/items/{item_id}")
def get_item(item_id: int):
    conn = get_db()
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Item not found")
    item = dict(row)
    # Get parent info
    if item["parent_id"]:
        parent = conn.execute("SELECT id, short_name FROM items WHERE id = ?", (item["parent_id"],)).fetchone()
        item["parent"] = dict(parent) if parent else None
    else:
        item["parent"] = None
    # Get ancestors chain
    item["ancestors"] = get_ancestors(conn, item_id)
    # Get children
    children = conn.execute("SELECT id, short_name FROM items WHERE parent_id = ?", (item_id,)).fetchall()
    item["children"] = [dict(c) for c in children]
    conn.close()
    return item

@app.post("/api/items")
def create_item(item: ItemCreate):
    conn = get_db()
    # Validate parent exists
    if item.parent_id:
        parent = conn.execute("SELECT id FROM items WHERE id = ?", (item.parent_id,)).fetchone()
        if not parent:
            raise HTTPException(400, "Parent item not found")
    
    new_id = generate_id()
    # Ensure unique
    while conn.execute("SELECT id FROM items WHERE id = ?", (new_id,)).fetchone():
        new_id = generate_id()
    
    now = int(time.time())
    conn.execute(
        "INSERT INTO items (id, short_name, description, parent_id, created_at, updated_at) VALUES (?,?,?,?,?,?)",
        (new_id, item.short_name, item.description, item.parent_id, now, now)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM items WHERE id = ?", (new_id,)).fetchone()
    conn.close()
    return dict(row)

@app.put("/api/items/{item_id}")
def update_item(item_id: int, item: ItemUpdate):
    conn = get_db()
    existing = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    if not existing:
        raise HTTPException(404, "Item not found")
    
    updates = {}
    if item.short_name is not None:
        updates["short_name"] = item.short_name
    if item.description is not None:
        updates["description"] = item.description
    if "parent_id" in item.model_fields_set:
        # Prevent circular reference
        if item.parent_id == item_id:
            raise HTTPException(400, "Item cannot be its own parent")
        updates["parent_id"] = item.parent_id
    
    if updates:
        updates["updated_at"] = int(time.time())
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE items SET {set_clause} WHERE id = ?",
            list(updates.values()) + [item_id]
        )
        conn.commit()
    
    row = conn.execute("SELECT * FROM items WHERE id = ?", (item_id,)).fetchone()
    conn.close()
    return dict(row)

@app.delete("/api/items/{item_id}")
def delete_item(item_id: int):
    conn = get_db()
    # Unparent children first
    conn.execute("UPDATE items SET parent_id = NULL WHERE parent_id = ?", (item_id,))
    conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@app.get("/api/items/{item_id}/qr")
def get_qr(item_id: int):
    conn = get_db()
    row = conn.execute("SELECT short_name FROM items WHERE id = ?", (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Item not found")
    
    url = f"{BASE_URL}/item/{item_id}"
    qr = qrcode.QRCode(version=1, box_size=10, border=2)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return Response(content=buf.read(), media_type="image/png")

@app.get("/api/parents")
def list_parents():
    """Get all items for parent dropdown"""
    conn = get_db()
    rows = conn.execute("SELECT id, short_name, parent_id FROM items ORDER BY short_name").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# Serve frontend
@app.get("/", response_class=HTMLResponse)
@app.get("/item/{item_id}", response_class=HTMLResponse)
def frontend(item_id: int = None):
    html_path = os.path.join(os.path.dirname(__file__), "..", "static", "index.html")
    with open(html_path) as f:
        return f.read()
