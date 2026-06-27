# Evolution API — Webhook Setup (Sprint 18)

Configure Evolution API to POST incoming WhatsApp messages to MRV4ULT AI.

Prerequisites:

- Evolution API running ([evolution_setup.md](evolution_setup.md))
- WhatsApp instance connected ([`/whatsapp`](../README.md) dashboard)
- MRV4ULT dashboard running: `uvicorn app:app --reload` on port **8000**

---

## Webhook endpoint

MRV4ULT AI exposes:

```text
POST http://<mrv4ult-host>:8000/webhook/evolution
```

The handler:

1. Logs the full JSON payload to the console (for debugging)
2. Ignores outgoing messages (`fromMe: true`)
3. Maps group messages to `WhatsAppMessage`
4. Calls `collect_message()` → existing ingest pipeline

Only **WhatsApp group** messages are imported. Direct (1:1) chats are skipped.

---

## Local development: expose port 8000

Evolution API runs in Docker and must reach your machine on port 8000.

### Option A — Docker → host (Windows / macOS)

Use `host.docker.internal`:

```text
http://host.docker.internal:8000/webhook/evolution
```

### Option B — Public tunnel (recommended for first test)

Use [ngrok](https://ngrok.com/) or similar:

```powershell
ngrok http 8000
```

Use the HTTPS URL ngrok prints, for example:

```text
https://abc123.ngrok-free.app/webhook/evolution
```

---

## Register the webhook in Evolution API

Replace placeholders:

- `EVOLUTION_URL` — default `http://localhost:8080`
- `AUTHENTICATION_API_KEY` — from `.env`
- `EVOLUTION_INSTANCE_NAME` — default `mrv4ult`
- `WEBHOOK_URL` — URL Evolution can reach (see above)

**PowerShell:**

```powershell
$body = @{
  webhook = @{
    enabled = $true
    url = "WEBHOOK_URL"
    webhookByEvents = $false
    webhookBase64 = $false
    events = @(
      "MESSAGES_UPSERT",
      "GROUPS_UPSERT",
      "GROUPS_UPDATE"
    )
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri "$env:EVOLUTION_URL/webhook/set/$env:EVOLUTION_INSTANCE_NAME" `
  -Headers @{ apikey = $env:AUTHENTICATION_API_KEY } `
  -ContentType "application/json" `
  -Body $body
```

**curl:**

```bash
curl -X POST "$EVOLUTION_URL/webhook/set/$EVOLUTION_INSTANCE_NAME" \
  -H "apikey: $AUTHENTICATION_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "enabled": true,
      "url": "WEBHOOK_URL",
      "webhookByEvents": false,
      "webhookBase64": false,
      "events": [
        "MESSAGES_UPSERT",
        "GROUPS_UPSERT",
        "GROUPS_UPDATE"
      ]
    }
  }'
```

### Why group events?

Incoming message webhooks do not always include the group display name. MRV4ULT caches group names from `GROUPS_UPSERT` and `GROUPS_UPDATE` events so imports have the correct `group_name`.

---

## Verify configuration

Check the current webhook:

```powershell
Invoke-RestMethod `
  -Method Get `
  -Uri "$env:EVOLUTION_URL/webhook/find/$env:EVOLUTION_INSTANCE_NAME" `
  -Headers @{ apikey = $env:AUTHENTICATION_API_KEY }
```

---

## End-to-end test

1. Start Evolution API: `docker compose up -d`
2. Start MRV4ULT: `uvicorn app:app --reload`
3. Register the webhook (above)
4. Confirm WhatsApp is connected at **http://127.0.0.1:8000/whatsapp**
5. Send a test message **in a WhatsApp group** your linked account is in (not from your own phone as a reply you sent from that same session — outgoing messages are ignored)
6. Watch the uvicorn console for `[Evolution webhook]` JSON logs
7. Open **http://127.0.0.1:8000/activity** — the import should appear

Example successful response from `POST /webhook/evolution`:

```json
{
  "status": "imported",
  "group": "HK Dealers",
  "dealer_whatsapp": "85291234567",
  "watches_parsed": 2,
  "new_offers": 2,
  "duplicate_offers": 0,
  "import_log_id": "..."
}
```

Ignored payloads return HTTP 200 with `"status": "ignored"` (for example outgoing messages or non-text content).

---

## Troubleshooting

| Symptom | Check |
|---------|--------|
| No webhook logs | Webhook URL not reachable from Docker; try ngrok or `host.docker.internal`. |
| `Group name not found` | Enable `GROUPS_UPSERT` / `GROUPS_UPDATE`; send a message after reconnect so group metadata syncs. |
| `outgoing message` | Expected for messages you send from the linked WhatsApp account. |
| `Only WhatsApp group messages` | Message was a direct chat, not a group. |
| Import error in logs | Supabase credentials, schema, or parser issue — check uvicorn traceback. |

---

## Related docs

- [whatsapp_collector_design.md](whatsapp_collector_design.md) — architecture
- [evolution_setup.md](evolution_setup.md) — Docker stack
