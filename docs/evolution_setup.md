# Evolution API — Local Docker Setup

Sprint 16 prepares a local Evolution API environment for MRV4ULT AI. **No MRV4ULT AI integration is included yet** — this is only the WhatsApp Web bridge stack.

See also: [`whatsapp_collector_design.md`](whatsapp_collector_design.md)

---

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) (or Docker Engine + Docker Compose v2)
- A free port **8080** on your machine
- A dedicated WhatsApp number for testing (recommended — not your primary account)

---

## Quick start

### 1. Create configuration

From the project root:

**Windows (PowerShell):**

```powershell
Copy-Item .env.example .env
```

**macOS / Linux:**

```bash
cp .env.example .env
```

Edit `.env` and set:

- `AUTHENTICATION_API_KEY` — strong random key for Evolution API
- `POSTGRES_PASSWORD` — Postgres password
- `DATABASE_CONNECTION_URI` — must use the **same** username, password, database name, and host `postgres` (the Docker service name)

Example URI:

```text
postgresql://evolution:YOUR_PASSWORD@postgres:5432/evolution?schema=public
```

### 2. Start the stack

```bash
docker compose up -d
```

This starts:

- **postgres** — PostgreSQL 15 with a persistent Docker volume
- **evolution-api** — waits for Postgres to be healthy, then starts on port **8080**

First run pulls images and may take a few minutes.

### 3. Verify the services

```bash
docker compose ps
docker compose logs -f evolution-api
```

Both containers should be `running`. Evolution API should **not** log `Database provider none invalid.`

```bash
curl http://localhost:8080
```

A JSON response or API info page indicates the API is running.

### 4. Stop the stack

```bash
docker compose down
```

WhatsApp session files remain in `./data/evolution/instances`. Postgres data remains in the `evolution_postgres_data` Docker volume.

To remove Postgres data as well:

```bash
docker compose down -v
```

---

## Configuration

Settings are loaded from `.env` via `env_file` in `docker-compose.yml`.

### Evolution API

| Variable | Purpose |
|----------|---------|
| `SERVER_URL` | Base URL for QR codes and callbacks. Use `http://localhost:8080` locally. |
| `AUTHENTICATION_API_KEY` | Master API key. Send as header `apikey` on every request. |
| `DATABASE_ENABLED` | Must be `true` when using Postgres. |
| `DATABASE_PROVIDER` | Must be `postgresql` (Evolution API does not support `none`). |
| `DATABASE_CONNECTION_URI` | Postgres connection string. Host must be `postgres` inside Docker Compose. |
| `DATABASE_CONNECTION_CLIENT_NAME` | Optional client label for this installation. |
| `CACHE_REDIS_ENABLED` | `false` for local dev without Redis. |
| `LOG_LEVEL` | `ERROR`, `WARN`, `INFO`, or `DEBUG`. |

Evolution API v2 uses `DATABASE_CONNECTION_URI` (not `DATABASE_URL`). Keep the `?schema=public` suffix.

### Postgres service

| Variable | Purpose |
|----------|---------|
| `POSTGRES_DATABASE` | Database name (default: `evolution`) |
| `POSTGRES_USERNAME` | Database user (default: `evolution`) |
| `POSTGRES_PASSWORD` | Database password — must match the URI |

---

## Persistent data

| Data | Location |
|------|----------|
| WhatsApp instances / sessions | `./data/evolution/instances` (bind mount) |
| Evolution metadata (Postgres) | Docker volume `evolution_postgres_data` |

Do not delete `./data/evolution/instances` unless you want to reset WhatsApp sessions.

---

## Create a WhatsApp instance (manual test)

After both containers are healthy, create an instance (replace `YOUR_API_KEY`):

**Windows (cmd):**

```bash
curl -X POST http://localhost:8080/instance/create ^
  -H "Content-Type: application/json" ^
  -H "apikey: YOUR_API_KEY" ^
  -d "{\"instanceName\": \"mrv4ult-dev\", \"integration\": \"WHATSAPP-BAILEYS\", \"qrcode\": true}"
```

**PowerShell:**

```powershell
curl.exe -X POST http://localhost:8080/instance/create `
  -H "Content-Type: application/json" `
  -H "apikey: YOUR_API_KEY" `
  -d '{\"instanceName\": \"mrv4ult-dev\", \"integration\": \"WHATSAPP-BAILEYS\", \"qrcode\": true}'
```

Scan the QR code with the test WhatsApp account. Check connection state:

```bash
curl http://localhost:8080/instance/connectionState/mrv4ult-dev -H "apikey: YOUR_API_KEY"
```

Refer to [Evolution API documentation](https://doc.evolution-api.com/) if endpoint shapes differ by version.

---

## Project layout

```
MRV4ULT AI/
├── docker-compose.yml              # evolution-api + postgres
├── .env.example                    # Template configuration
├── .env                            # Local secrets (not committed)
└── data/evolution/instances/       # WhatsApp sessions (not committed)
```

Postgres files live in the named volume `evolution_postgres_data`, not in the project folder.

---

## Troubleshooting

| Issue | What to try |
|-------|-------------|
| `Database provider none invalid.` | Set `DATABASE_PROVIDER=postgresql` and `DATABASE_ENABLED=true`. Remove any `none` value. |
| Evolution API exits on startup | Check `docker compose logs evolution-api`. Verify `DATABASE_CONNECTION_URI` host is `postgres`, not `localhost`. |
| Postgres connection refused | Run `docker compose ps` — postgres must be healthy before evolution-api starts. |
| Port 8080 in use | Change the host port in `docker-compose.yml` (e.g. `"8081:8080"`) and update `SERVER_URL`. |
| Auth errors on API calls | Confirm `apikey` header matches `AUTHENTICATION_API_KEY` in `.env`. |
| Session lost after restart | Keep `./data/evolution/instances` mounted. |
| QR code not generated | Confirm `SERVER_URL` matches how you reach the API. |

After changing Postgres credentials, update **both** `POSTGRES_PASSWORD` and `DATABASE_CONNECTION_URI`, then recreate containers:

```bash
docker compose down
docker compose up -d
```

---

## What is not included

- Webhook endpoint in MRV4ULT AI
- `whatsapp_collector.py` → Evolution API adapter
- Automatic import on new group messages
- Production deployment or TLS reverse proxy

Those steps follow in later sprints after this Docker environment is stable.
