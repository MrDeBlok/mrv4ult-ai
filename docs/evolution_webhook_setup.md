# Evolution API — Webhook Setup (Sprint 18)

Configure Evolution API to POST incoming WhatsApp messages to MRV4ULT AI.

Tested against **Evolution API v2.3.7**.

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

Only **WhatsApp group and private dealer** messages are imported. Direct chats are mapped to the synthetic group `Private Offers`.

Private chats that arrive with WhatsApp `@lid` identifiers are supported when a phone or stable LID contact id can be resolved from the webhook payload.

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

## Register the webhook in Evolution API (v2.3.7)

**Endpoint:**

```text
POST {EVOLUTION_URL}/webhook/set/{EVOLUTION_INSTANCE_NAME}
Header: apikey: {AUTHENTICATION_API_KEY}
```

**Payload schema (v2.3.7):** wrap settings in a top-level `webhook` object. Use `byEvents` and `base64` (not the older `webhookByEvents` / `webhookBase64` field names).

**Event names** must match the v2.3.7 enum exactly. For group metadata, use `GROUP_UPDATE` — **`GROUPS_UPDATE` is invalid** and returns:

```text
webhook.events[...] is not one of enum values
```

Replace placeholders:

- `EVOLUTION_URL` — default `http://localhost:8080`
- `AUTHENTICATION_API_KEY` — from `.env`
- `EVOLUTION_INSTANCE_NAME` — default `mrv4ult`
- `WEBHOOK_URL` — URL Evolution can reach (see above)

**PowerShell:**

```powershell
$EvolutionUrl = if ($env:EVOLUTION_URL) { $env:EVOLUTION_URL } else { "http://localhost:8080" }
$InstanceName = if ($env:EVOLUTION_INSTANCE_NAME) { $env:EVOLUTION_INSTANCE_NAME } else { "mrv4ult" }
$WebhookUrl = "http://host.docker.internal:8000/webhook/evolution"

$body = @{
  webhook = @{
    enabled = $true
    url = $WebhookUrl
    byEvents = $false
    base64 = $false
    events = @(
      "MESSAGES_UPSERT",
      "GROUPS_UPSERT",
      "GROUP_UPDATE",
      "CHATS_UPSERT"
    )
  }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri "$EvolutionUrl/webhook/set/$InstanceName" `
  -Headers @{ apikey = $env:AUTHENTICATION_API_KEY } `
  -ContentType "application/json" `
  -Body $body
```

A successful registration returns HTTP **201 Created**.

**curl:**

```bash
curl -X POST "$EVOLUTION_URL/webhook/set/$EVOLUTION_INSTANCE_NAME" \
  -H "apikey: $AUTHENTICATION_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "webhook": {
      "enabled": true,
      "url": "WEBHOOK_URL",
      "byEvents": false,
      "base64": false,
      "events": [
        "MESSAGES_UPSERT",
        "GROUPS_UPSERT",
        "GROUP_UPDATE",
        "CHATS_UPSERT"
      ]
    }
  }'
```

### v2.3.7 event enum (reference)

Valid values include:

`APPLICATION_STARTUP`, `QRCODE_UPDATED`, `MESSAGES_SET`, `MESSAGES_UPSERT`, `MESSAGES_EDITED`, `MESSAGES_UPDATE`, `MESSAGES_DELETE`, `SEND_MESSAGE`, `SEND_MESSAGE_UPDATE`, `CONTACTS_SET`, `CONTACTS_UPSERT`, `CONTACTS_UPDATE`, `PRESENCE_UPDATE`, `CHATS_SET`, `CHATS_UPSERT`, `CHATS_UPDATE`, `CHATS_DELETE`, `GROUPS_UPSERT`, **`GROUP_UPDATE`**, `GROUP_PARTICIPANTS_UPDATE`, `CONNECTION_UPDATE`, `LABELS_EDIT`, `LABELS_ASSOCIATION`, `CALL`, `TYPEBOT_START`, `TYPEBOT_CHANGE_STATUS`, `INSTANCE_CREATE`, `INSTANCE_DELETE`, `REMOVE_INSTANCE`, `LOGOUT_INSTANCE`, `STATUS_INSTANCE`

MRV4ULT registers the minimum set above. `MESSAGES_UPSERT` delivers new messages; group/chat events populate the in-memory group name cache.

### Why group and chat events?

Incoming message webhooks do not always include the group display name. MRV4ULT caches group names from `GROUPS_UPSERT`, `GROUP_UPDATE`, and `CHATS_UPSERT` events so imports have the correct `group_name`.

---

## Verify configuration

Check the current webhook:

```powershell
$EvolutionUrl = if ($env:EVOLUTION_URL) { $env:EVOLUTION_URL } else { "http://localhost:8080" }
$InstanceName = if ($env:EVOLUTION_INSTANCE_NAME) { $env:EVOLUTION_INSTANCE_NAME } else { "mrv4ult" }

Invoke-RestMethod `
  -Method Get `
  -Uri "$EvolutionUrl/webhook/find/$InstanceName" `
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
| `webhook.events[...] is not one of enum values` | Use v2.3.7 names: `GROUP_UPDATE` not `GROUPS_UPDATE`; use `byEvents` / `base64` in the payload. |
| `instance requires property "webhook"` | Wrap settings in a top-level `"webhook": { ... }` object. |
| No webhook logs | Webhook URL not reachable from Docker; try ngrok or `host.docker.internal`. |
| `Group name not found` | Should no longer block imports (Sprint 18.2 fetches metadata or falls back to remoteJid). If imports still fail, check Evolution API connectivity and instance name in `.env`. |
| `outgoing message` | Expected for messages you send from the linked WhatsApp account. |
| `Only WhatsApp group messages` | Legacy note — private dealer chats are imported as `Private Offers`. |
| `Could not determine dealer WhatsApp number` | Participant arrived as `@lid` without phone fields; check `[WhatsApp webhook trace]` logs for skip reason. |
| Import error in logs | Supabase credentials, schema, or parser issue — check uvicorn traceback. |

---

## Related docs

- [whatsapp_collector_design.md](whatsapp_collector_design.md) — architecture
- [evolution_setup.md](evolution_setup.md) — Docker stack
