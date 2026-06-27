# MRV4ULT AI

Internal tooling for luxury watch brokers: parse WhatsApp dealer messages, store offers in Supabase, search inventory, and monitor imports.

---

## Prerequisites

- Python 3.11+
- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (for Evolution API)
- Supabase project ([schema](docs/schema.sql))
- OpenAI API key (optional — regex parser available)

---

## 1. Python environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy environment variables:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and set at minimum:

| Variable | Purpose |
|----------|---------|
| `SUPABASE_URL` | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase service role key |
| `AUTHENTICATION_API_KEY` | Evolution API master key |
| `EVOLUTION_URL` | Evolution API base URL (default `http://localhost:8080`) |
| `EVOLUTION_INSTANCE_NAME` | Instance name to create/connect (default `mrv4ult`) |

Postgres settings in `.env` are used by Docker Compose for Evolution API. See [Evolution setup](docs/evolution_setup.md).

Apply database schema:

1. Paste `docs/schema.sql` into the Supabase SQL editor (includes `import_logs` for Activity).
2. Run migrations whenever `docs/schema.sql` changes.

---

## 2. Start Evolution API (Docker)

From the project root:

```powershell
docker compose up -d
docker compose ps
docker compose logs -f evolution-api
```

Evolution API listens on **http://localhost:8080**.

Full Docker instructions: [docs/evolution_setup.md](docs/evolution_setup.md)

Design notes: [docs/whatsapp_collector_design.md](docs/whatsapp_collector_design.md)

---

## 3. Start the dashboard

```powershell
uvicorn app:app --reload
```

Open **http://127.0.0.1:8000**

| Route | Purpose |
|-------|---------|
| `/` | Search active offers |
| `/import` | Manual WhatsApp message import |
| `/activity` | Import history |
| `/whatsapp` | Create and connect Evolution API WhatsApp instance |
| `POST /webhook/evolution` | Evolution API webhook receiver (automatic imports) |

---

## 4. Connect WhatsApp (Sprint 17)

1. Ensure Evolution API is running (`docker compose up -d`).
2. Open **http://127.0.0.1:8000/whatsapp**.
3. Click **Create instance** if no instance exists.
4. Scan the QR code with your test WhatsApp account.
5. The page refreshes automatically every 5 seconds until connected.
6. When connected, the page shows phone number, status, and last connection time.

CLI alternative (simulated collector, no Evolution API):

```powershell
python whatsapp_collector.py
```

Evolution API client module: `evolution_client.py`

---

## 5. Automatic imports via webhook (Sprint 18)

1. Keep the dashboard running: `uvicorn app:app --reload`
2. Register Evolution API to POST to `http://<host>:8000/webhook/evolution`
3. Send a message in a WhatsApp group your linked account is in
4. Check the uvicorn console for `[Evolution webhook]` logs
5. Confirm the import on **http://127.0.0.1:8000/activity**

Full setup (Docker networking, ngrok, curl examples): [docs/evolution_webhook_setup.md](docs/evolution_webhook_setup.md)

---

## 6. Other CLI tools

```powershell
python watch_parser.py    # Regex parser
python ingest.py          # Import via stdin
python search.py          # Search CLI
```

---

## Project layout

```
MRV4ULT AI/
├── app.py                  # FastAPI dashboard
├── evolution_client.py     # Evolution API v2 client
├── evolution_webhook.py    # Evolution webhook → collector
├── whatsapp_collector.py   # Collector → ingest pipeline
├── ingest.py               # Import pipeline
├── search.py               # Search engine
├── database.py             # Supabase helpers
├── docker-compose.yml      # Evolution API + Postgres
├── docs/
│   ├── evolution_setup.md
│   ├── evolution_webhook_setup.md
│   └── whatsapp_collector_design.md
└── templates/              # Dashboard UI
```

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `/whatsapp` shows API error | Check `EVOLUTION_URL`, `AUTHENTICATION_API_KEY`, and `docker compose ps`. |
| QR code never appears | Confirm Evolution API logs; try recreating the instance from `/whatsapp`. |
| Database provider error | Use `DATABASE_PROVIDER=postgresql` — see [evolution_setup.md](docs/evolution_setup.md). |
| No activity after import | Apply `import_logs` table from `docs/schema.sql`. |
| Webhook not firing | See [evolution_webhook_setup.md](docs/evolution_webhook_setup.md) — URL must be reachable from Docker. |
