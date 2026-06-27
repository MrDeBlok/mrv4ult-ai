# WhatsApp Collector Design

Sprint 15 planning document for connecting MRV4ULT AI to WhatsApp via a **WhatsApp Web–based collector**.

This document describes the target architecture only. **No integration code is included in this sprint.**

---

## Goal

Automatically receive dealer offer messages from WhatsApp groups and forward them into the existing MRV4ULT AI import pipeline without changing parser or database logic.

Current foundation:

```
WhatsApp → WhatsAppMessage → collect_message() → ingest_message() → parser + Supabase
```

The collector module (`whatsapp_collector.py`) already defines the boundary. The next step is to replace the simulated message source with a real WhatsApp Web listener.

---

## Why not the official WhatsApp Business API?

MRV4ULT AI is built for **luxury watch brokers who already work inside existing WhatsApp groups**. The official WhatsApp Business API is a poor fit for this workflow in v1:

| Limitation | Impact on MRV4ULT AI |
|------------|----------------------|
| **Group messaging restrictions** | The Cloud API does not support reading arbitrary third-party group chats the way brokers use them today. Group imports are the core input channel. |
| **Onboarding and compliance** | Business API setup requires Meta Business verification, approved use cases, and template/message policies oriented toward customer support—not passive monitoring of dealer lists. |
| **Operational mismatch** | Brokers do not want to migrate dealers to a new number or restructure how groups work. They want automation on top of existing groups. |
| **Cost and complexity** | Per-conversation pricing and webhook infrastructure add overhead before the product has proven value for the broker workflow. |

The Business API remains useful later for **outbound notifications** (e.g. alerting a broker when a request matches). It is **not** the recommended path for **inbound group offer ingestion** in v1.

---

## Recommended approach: Evolution API

**Evolution API** is the recommended WhatsApp Web bridge for MRV4ULT AI.

Evolution API runs a WhatsApp Web session (linked via QR code), exposes HTTP webhooks and REST endpoints, and supports multi-device style connections. It is widely used for self-hosted WhatsApp automation and fits the collector pattern already defined in this project.

### High-level architecture

```
┌─────────────────┐     webhook / poll      ┌──────────────────────┐
│  Evolution API  │ ──────────────────────► │  MRV4ULT collector   │
│  (WhatsApp Web) │                         │  whatsapp_collector  │
└────────┬────────┘                         └──────────┬───────────┘
         │                                              │
         │ QR-linked session                            │ collect_message()
         ▼                                              ▼
   WhatsApp groups                              ingest_message() → Supabase
```

### Why Evolution API

- Supports **group messages** from a linked WhatsApp account
- Self-hosted: broker or operator controls the session and data
- Webhook-driven: new messages can trigger the collector in near real time
- Maps cleanly to the existing `WhatsAppMessage` dataclass
- Keeps parsing and persistence in MRV4ULT AI; Evolution API only transports events

Alternatives (Baileys direct, other Web bridges) are possible but Evolution API provides a stable HTTP interface and operational tooling with less low-level maintenance.

---

## Incoming message mapping

Evolution API (and similar Web bridges) emit events containing roughly:

- Chat / group identifier and display name
- Sender JID or phone number
- Sender push name (WhatsApp display name)
- Message body
- Message timestamp

These map to the internal model as follows.

### `WhatsAppMessage` fields

Defined in `whatsapp_collector.py`:

| Internal field | Source (Evolution API) | Notes |
|----------------|------------------------|-------|
| `group_name` | Group chat title / subject | Human-readable name shown in WhatsApp, e.g. `HK Dealers` |
| `dealer_whatsapp` | Sender phone or WhatsApp ID | Normalized E.164 where possible, e.g. `+85291234567` |
| `dealer_alias` | Sender push name / profile name | Optional; stored as dealer `display_name` in ingest |
| `message_text` | Text body of the message | Offer list plain text; media handling is out of scope for v1 |
| `received_at` | Message timestamp from WhatsApp | UTC `datetime`; used as `messages.received_at` |

### Mapping example

**WhatsApp (group message):**

- Group: `HK Dealers`
- Sender: `+85291234567` (push name: `John`)
- Body: multi-line offer list
- Time: `2026-06-25T14:30:00Z`

**Internal object:**

```python
WhatsAppMessage(
    group_name="HK Dealers",
    dealer_whatsapp="+85291234567",
    dealer_alias="John",
    message_text="ROLEX\n126200 green jub n6/26 74000usd",
    received_at=datetime(2026, 6, 25, 14, 30, tzinfo=timezone.utc),
)
```

### Filtering rules (collector responsibility)

The collector should **not** parse watch data. It may apply lightweight filters before calling `collect_message()`:

- Ignore non-text messages in v1 (images, voice, PDFs)
- Ignore messages from groups not on an allowlist (configured monitored groups)
- Optionally ignore broker’s own messages or system notifications
- Deduplicate by WhatsApp message ID before ingest (future: store `whatsapp_message_id` on `messages`)

---

## Flow into `collect_message()`

1. Evolution API receives a WhatsApp group message on the linked session.
2. Evolution API sends a webhook POST to MRV4ULT AI (or a small collector worker polls events).
3. Collector adapter validates the payload and builds a `WhatsAppMessage`.
4. `collect_message(message)` runs:
   - Validates required fields
   - Normalizes timezone on `received_at`
   - Calls `ingest_message()` with group, dealer, alias, text, and timestamp
5. `ingest_message()` handles parsing, duplicate detection, request matching, price intelligence, and `import_logs` activity logging.
6. Result (`IngestSummary`) can be logged or surfaced on `/activity`.

No parser or database code belongs in the collector adapter.

```
Evolution webhook
       │
       ▼
map_to_whatsapp_message(event) → WhatsAppMessage
       │
       ▼
collect_message(message) → IngestSummary
       │
       ▼
/activity (import_logs)
```

---

## Risks

### Account session disconnects

WhatsApp Web sessions can drop due to phone offline, logout, or WhatsApp forcing re-auth. **Impact:** missed offers until the session is restored.

**Mitigation:** health checks on Evolution API, alerting when disconnected, automatic retry of webhook delivery, documented reconnect procedure.

### QR login required

Initial setup and periodic re-linking require scanning a QR code with the broker’s phone. **Impact:** manual intervention; not suitable for fully unattended ops without monitoring.

**Mitigation:** run Evolution API on a stable host, document QR refresh steps, keep a backup phone logged into the same account if WhatsApp policy allows.

### WhatsApp protocol and UI changes

WhatsApp Web is unofficial for automation. Meta can change protocols, detection, or terms at any time. **Impact:** collector breakage or account restrictions.

**Mitigation:** isolate Evolution API behind the collector boundary, pin tested Evolution API versions, monitor upstream releases, avoid forking parser/ingest logic into the bridge layer.

### Rate limits and bans

High-volume automation, bulk forwarding, or suspicious patterns can trigger temporary blocks or permanent bans. **Impact:** loss of group access for the linked number.

**Mitigation:**

- Use a dedicated WhatsApp number for ingestion, not the broker’s primary personal account
- Do not send automated replies until outbound policy is defined
- Ingest only; no spammy outbound behavior in v1
- Log and deduplicate messages to avoid double-processing

---

## Next implementation steps

1. **Deploy Evolution API** — Self-hosted instance with persistent session storage and HTTPS webhook endpoint.

2. **Define monitored groups** — Configuration list of group JIDs/names to import; ignore everything else.

3. **Webhook receiver** — Small FastAPI route or worker (separate from search UI) that accepts Evolution API message events, verifies signatures if available, and returns 200 quickly.

4. **Event adapter** — Function `map_evolution_event_to_whatsapp_message(event) -> WhatsAppMessage` with unit tests using recorded webhook JSON fixtures.

5. **Deduplication** — Pass Evolution message ID into `insert_message(whatsapp_message_id=...)` to skip already-imported messages.

6. **Session monitoring** — Dashboard or health endpoint showing Evolution connection state (connected / QR required / disconnected).

7. **Error handling** — On ingest failure, log error status without crashing the webhook worker; surface failures on `/activity` where possible.

8. **Staging test** — Link a test WhatsApp account, join a test group, send sample offer lists, verify `/activity` and search results.

9. **Production rollout** — Dedicated ingestion number, broker onboarding doc for QR scan, allowlist of production groups.

10. **Future (out of v1 scope)** — Media parsing (images/PDFs), outbound match notifications via Business API, multi-account collectors.

---

## Out of scope (this sprint)

- Evolution API installation scripts
- Webhook endpoint code
- Production credentials or environment configuration
- Changes to `watch_parser.py`, `search.py`, or database schema

The existing `whatsapp_collector.py` simulation CLI remains the development entry point until step 4 is implemented.
