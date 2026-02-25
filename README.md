# 📦 Inventory System

A lightweight self-hosted inventory management system built with FastAPI and SQLite. Designed to run on a Raspberry Pi via Docker, accessible remotely through Cloudflare Tunnel.

## Features

- 🔐 Password-protected login with session cookies
- 📁 Hierarchical parent-child item structure (e.g. Room → Box → Bag → Component)
- 🔍 Search by name, description, location, or ID
- 🧭 Breadcrumb trail showing full location path
- 📷 QR code generation per item — scan to jump directly to that item's page
- ⬇️ Download QR codes for printing labels

## Project Structure

```
inventory/
├── main.py              # FastAPI backend + frontend (single file)
├── requirements.txt     # Python dependencies
├── Docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── data/                # SQLite database (auto-created, not committed)
```

## Getting Started

### Running locally (for testing)

1. Install dependencies:
```bash
pip install fastapi uvicorn qrcode[pil] pillow itsdangerous
```

2. Run the app:
```bash
uvicorn main:app --reload
```

3. Open `http://localhost:8000` in your browser.

Default password: `7ate9` — **please change this before deploying**

### Running with Docker (recommended for Pi)

1. Copy the contents of the `Docker/` folder and `main.py` into the same directory on your server.

2. Build and start:
```bash
docker compose up -d --build
```

3. Access at `http://your-server-ip:8084`

## Configuration

Before deploying, edit these two lines at the top of `main.py`:

```python
SECRET_KEY = "change-this-to-something-secret"  # Used to sign session cookies
PASSWORD = "7ate9"                                # Change this to your own password!
```

## Usage

- **Add items** using the `+ Add Item` button
- **Set a parent** to define where an item lives (e.g. parent = "Storage Box A")
- **Nest as deep as you want** — Room → Box → Bag → Item all works
- **Search** works across name, description, ID, and location breadcrumb

## Known Limitations

- **QR codes are hardcoded to `localhost`** — scanning them outside of a local browser won't work correctly. This will be fixed once a proper domain is configured.

## Roadmap / Future Improvements

- [ ] **Fix QR code URL** — make the base URL configurable so scanned QR codes work from any device, not just localhost
- [ ] **Niimbot label printer integration** — replace QR code download with direct wireless printing to a Niimbot B21 via Bluetooth
- [ ] **Improve hierarchy visualisation** — make the parent-child tree structure more visually obvious, e.g. a tree view or collapsible nested list in the UI
- [ ] **Dockerize with environment variables** — move `PASSWORD` and `SECRET_KEY` out of `main.py` into a `.env` file for cleaner deployment
- [ ] **Multi-user support** — separate logins for different household members

## Stack

- [FastAPI](https://fastapi.tiangolo.com/) — backend
- SQLite — database
- [itsdangerous](https://itsdangerous.palletsprojects.com/) — session signing
- [qrcode](https://github.com/lincolnloop/python-qrcode) — QR generation
- Docker — containerization
- Cloudflare Tunnel — remote access
