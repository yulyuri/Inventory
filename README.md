# Inventory System

Built with FastAPI and SQLite. 
Was meant to run on a Rpi5 via Docker and then made accessible remotely through Cloudflare Tunnel.

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
├── main.py              # FastAPI backend + frontend (single file to test whether everything was working)
├── requirements.txt     # Python dependencies
├── Docker/
│   ├── Dockerfile
│   └── docker-compose.yml
└── data/                # SQLite database (auto-created, not committed)
```


-The main file you should be looking at is files in the docker folder
## Getting Started

### Running locally (only for testing, else not important)

1. Install dependencies:
```bash
pip install fastapi uvicorn qrcode[pil] pillow itsdangerous
```

2. Run the app:
```bash
uvicorn main:app --reload
```

3. Open `http://localhost:8000` in your browser.

Default password: `homelab123` — **please change this before deploying**

### Running with Docker 

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
PASSWORD = "homelab123"                                # Change this to your own password!
```

## Usage

- **Add items** using the `+ Add Item` button
- **Set a parent** to define where an item lives (e.g. parent = "Storage Box A")
- **Nest as deep as you want** — Room → Box → Bag → Item all works
- **Search** works across name, description, ID, and location breadcrumb

## Small problemo

- **QR codes are hardcoded to `localhost`** — scanning them outside of a local browser won't work correctly. This will be fixed once a proper domain is configured.

## Roadmap / Future Improvements that should be made

- [ ] **Fix QR code URL** — make the base URL configurable (cloudflare thing) so scanned QR codes work from any device, not just localhost
- [ ] **Niimbot label printer integration** — replace QR code download with direct wireless printing to a Niimbot B21 (if you plan to puchase else its redundant) via Bluetooth
- [ ] **Improve hierarchy visualisation** — make the parent-child tree structure more visually obvious, e.g. a tree view or collapsible nested list in the UI (- or something ZJ does)
- [ ] **Multi-user support** — separate logins for different household members (Probably need to do more sql configuration. Right now its only one single table so its easy. But with mutiple users probably need a Users table )

## Stack Used

- [FastAPI](https://fastapi.tiangolo.com/) — backend
- SQLite — database
- [itsdangerous](https://itsdangerous.palletsprojects.com/) — session signing
- [qrcode](https://github.com/lincolnloop/python-qrcode) — QR generation
- Docker — containerization
- Cloudflare Tunnel — remote access
