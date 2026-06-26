# MRV4ULT AI — Database Schema (v1)

Design document for the first version of the MRV4ULT AI database.

This schema supports the Version 1 workflow defined in the PRD:

1. Store raw WhatsApp messages
2. Classify message type (offer, offer_list, request, unknown)
3. Persist structured watch data from offers and requests
4. Link messages to dealers and WhatsApp groups
5. Enable duplicate offer detection and offer–request matching

**SQL implementation:** [`schema.sql`](schema.sql) — ready to paste into Supabase.

---

## Overview

| Table | Purpose |
|-------|---------|
| `groups` | WhatsApp groups where messages are received |
| `dealers` | WhatsApp contacts who send watch offers |
| `messages` | Raw inbound WhatsApp messages and classification |
| `watches` | Canonical watch identity (brand, reference, dial, etc.) |
| `offers` | A dealer listing for a watch within a message |
| `requests` | Active client buy requests (from messages or manual entry) |

---

## Entity Relationships

```
groups ──< messages ──< offers >── watches
              │              ↑
dealers ──────┘              │ every offer belongs to exactly one message
              │              │ every offer belongs to exactly one watch
              └──< requests (optional link when request came from a message)
```

- A **group** contains many **messages**.
- A **dealer** sends many **messages**.
- A **message** is the parent of zero or many **offers**.
- Every **offer** belongs to exactly one **message** (`message_id` is required).
- Every **offer** belongs to exactly one **watch** (`watch_id` is required).
- A **watch** can appear in many **offers** across different messages and dealers.
- A **message** may optionally produce one **request** when classified as a buy-side message.
- **Offers** and **requests** are matched in application logic (no join table in v1).

---

## Table: `groups`

Stores WhatsApp groups monitored by MRV4ULT AI.

| Column | Data Type | Description | Primary Key | Foreign Keys |
|--------|-----------|-------------|-------------|--------------|
| `id` | UUID | Unique group identifier | **Yes** | — |
| `name` | TEXT | Group display name | No | — |
| `country` | TEXT | Country or region of the group | No | — |
| `language` | TEXT | Primary language used in the group | No | — |
| `created_at` | TIMESTAMP | When the group was added | No | — |

**Primary key:** `id`

---

## Table: `dealers`

Stores dealer identity from WhatsApp. Used to attribute offers and track dealer activity.

| Column | Data Type | Description | Primary Key | Foreign Keys |
|--------|-----------|-------------|-------------|--------------|
| `id` | UUID | Unique dealer identifier | **Yes** | — |
| `whatsapp_id` | TEXT | WhatsApp user ID (unique per contact) | No | — |
| `display_name` | TEXT | Name as shown in WhatsApp | No | — |
| `phone_number` | TEXT | Normalized phone number, if available | No | — |
| `company_name` | TEXT | Optional business name | No | — |
| `country` | TEXT | Optional country or region | No | — |
| `notes` | TEXT | Internal broker notes about this dealer | No | — |
| `is_active` | BOOLEAN | Whether dealer is currently tracked | No | — |
| `created_at` | TIMESTAMP | When the dealer was first seen | No | — |
| `updated_at` | TIMESTAMP | When the dealer record was last updated | No | — |

**Primary key:** `id`

**Unique constraints:** `whatsapp_id`

---

## Table: `messages`

Stores every inbound WhatsApp message before and after parsing. Parent table for offers.

| Column | Data Type | Description | Primary Key | Foreign Keys |
|--------|-----------|-------------|-------------|--------------|
| `id` | UUID | Unique message identifier | **Yes** | — |
| `group_id` | UUID | WhatsApp group the message was received in | No | → `groups.id` |
| `dealer_id` | UUID | Dealer who sent the message | No | → `dealers.id` |
| `raw_text` | TEXT | Full original WhatsApp message body | No | — |
| `message_type` | TEXT | Classification: `offer`, `offer_list`, `request`, `unknown` | No | — |
| `source` | TEXT | Origin channel (e.g. `whatsapp`) | No | — |
| `whatsapp_message_id` | TEXT | External WhatsApp message ID, if available | No | — |
| `received_at` | TIMESTAMP | When the message was received | No | — |
| `parsed_at` | TIMESTAMP | When the parser finished processing | No | — |
| `parser_version` | TEXT | Parser version used (e.g. `watch_parser_v1`) | No | — |
| `parse_status` | TEXT | `success`, `partial`, `failed` | No | — |
| `parse_error` | TEXT | Error details if parsing failed | No | — |
| `created_at` | TIMESTAMP | When the record was created | No | — |

**Primary key:** `id`

**Foreign keys:**

| Column | References |
|--------|------------|
| `group_id` | `groups.id` |
| `dealer_id` | `dealers.id` |

---

## Table: `watches`

Stores the canonical identity of a watch model. Shared across multiple offers.

| Column | Data Type | Description | Primary Key | Foreign Keys |
|--------|-----------|-------------|-------------|--------------|
| `id` | UUID | Unique watch identifier | **Yes** | — |
| `brand` | TEXT | Watch brand (e.g. Rolex, Patek Philippe) | No | — |
| `reference` | TEXT | Reference number (e.g. 126610LN) | No | — |
| `model` | TEXT | Model name (e.g. Submariner) | No | — |
| `dial` | TEXT | Dial color or nickname (e.g. Black, Champagne) | No | — |
| `bracelet` | TEXT | Bracelet or strap type (e.g. jubilee, oyster) | No | — |
| `created_at` | TIMESTAMP | When the watch record was created | No | — |
| `updated_at` | TIMESTAMP | When the watch record was last updated | No | — |

**Primary key:** `id`

**Notes:**

- Look up or create a **`watches`** row before inserting an **`offers`** row.
- Watch identity fields live here — not duplicated on **`offers`**.

---

## Table: `offers`

Stores one dealer listing per row, linked to a watch and a source message.

**Constraints:**

- every offer must belong to exactly one message (`message_id` is NOT NULL)
- every offer must belong to exactly one watch (`watch_id` is NOT NULL)

| Column | Data Type | Description | Primary Key | Foreign Keys |
|--------|-----------|-------------|-------------|--------------|
| `id` | UUID | Unique offer identifier | **Yes** | — |
| `message_id` | UUID | Parent message (required) | No | → `messages.id` |
| `watch_id` | UUID | Watch being offered (required) | No | → `watches.id` |
| `dealer_id` | UUID | Dealer who listed the watch (denormalized for queries) | No | → `dealers.id` |
| `condition` | TEXT | Condition (e.g. New, Used, unworn, full set) | No | — |
| `production_year` | INTEGER | Production year, if stated | No | — |
| `card_date` | TEXT | Warranty card date in MM/YYYY format | No | — |
| `notes` | TEXT | Additional details not captured in other fields | No | — |
| `original_price` | INTEGER | Price in original currency (whole units) | No | — |
| `original_currency` | TEXT | Original currency code (USD, HKD, EUR, etc.) | No | — |
| `usd_price` | INTEGER | Price normalized to USD | No | — |
| `exchange_rate_to_usd` | DECIMAL | Exchange rate used for USD conversion | No | — |
| `source_line` | TEXT | Original line text this offer was parsed from | No | — |
| `line_index` | INTEGER | Position of this watch within the message (0-based) | No | — |
| `is_duplicate` | BOOLEAN | Whether this offer is flagged as a duplicate | No | — |
| `duplicate_of_id` | UUID | Original offer this duplicates, if known | No | → `offers.id` |
| `status` | TEXT | Lifecycle: `active`, `sold`, `withdrawn`, `expired` | No | — |
| `created_at` | TIMESTAMP | When the offer was stored | No | — |
| `updated_at` | TIMESTAMP | When the offer was last updated | No | — |

**Primary key:** `id`

**Foreign keys:**

| Column | References |
|--------|------------|
| `message_id` | `messages.id` (NOT NULL) |
| `watch_id` | `watches.id` (NOT NULL) |
| `dealer_id` | `dealers.id` |
| `duplicate_of_id` | `offers.id` (self-reference, nullable) |

**Unique constraints:** `(message_id, line_index)` — one row per watch position within a message.

**Notes:**

- Listing-specific fields (price, condition, card date) stay on **`offers`**.
- Watch identity (brand, reference, dial) is resolved via **`watch_id`** → **`watches`**.

---

## Table: `requests`

Stores active client buy requests. Used for matching against incoming offers.

| Column | Data Type | Description | Primary Key | Foreign Keys |
|--------|-----------|-------------|-------------|--------------|
| `id` | UUID | Unique request identifier | **Yes** | — |
| `message_id` | UUID | Source message, if request came from WhatsApp | No | → `messages.id` |
| `client_name` | TEXT | Client name or identifier | No | — |
| `client_phone` | TEXT | Client phone or WhatsApp number | No | — |
| `brand` | TEXT | Requested brand | No | — |
| `reference` | TEXT | Requested reference number | No | — |
| `model` | TEXT | Requested model | No | — |
| `dial` | TEXT | Preferred dial | No | — |
| `bracelet` | TEXT | Preferred bracelet | No | — |
| `condition` | TEXT | Acceptable condition | No | — |
| `production_year` | INTEGER | Preferred production year | No | — |
| `card_date` | TEXT | Preferred card date | No | — |
| `notes` | TEXT | Additional client requirements | No | — |
| `max_price` | INTEGER | Maximum budget in original currency | No | — |
| `max_currency` | TEXT | Currency of the budget | No | — |
| `max_usd_price` | INTEGER | Budget normalized to USD | No | — |
| `exchange_rate_to_usd` | DECIMAL | Exchange rate used for USD conversion | No | — |
| `source` | TEXT | Origin: `whatsapp`, `manual`, `import` | No | — |
| `status` | TEXT | `active`, `matched`, `fulfilled`, `cancelled` | No | — |
| `created_at` | TIMESTAMP | When the request was created | No | — |
| `updated_at` | TIMESTAMP | When the request was last updated | No | — |
| `expires_at` | TIMESTAMP | Optional expiry for active matching | No | — |

**Primary key:** `id`

**Foreign keys:**

| Column | References |
|--------|------------|
| `message_id` | `messages.id` (nullable — manual requests have no message) |

---

## Indexes

### `watches`

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_watches_brand` | `brand` | Brand filtering |
| `idx_watches_reference` | `reference` | Reference lookup |
| `idx_watches_brand_reference` | `brand, reference` | Watch lookup and matching |

### `offers`

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_offers_message_id` | `message_id` | Load all offers for a message |
| `idx_offers_watch_id` | `watch_id` | All offers for a watch |
| `idx_offers_dealer_id` | `dealer_id` | Dealer inventory queries |
| `idx_offers_usd_price` | `usd_price` | Price range queries |
| `idx_offers_duplicate_of_id` | `duplicate_of_id` | Duplicate chain traversal |
| `idx_offers_status` | `status` | Active offer queries |
| `idx_offers_is_duplicate` | `is_duplicate` | Filter duplicates |

### `messages`

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_messages_group_id` | `group_id` | Messages per group |
| `idx_messages_dealer_id` | `dealer_id` | Messages per dealer |
| `idx_messages_message_type` | `message_type` | Filter by classification |
| `idx_messages_received_at` | `received_at DESC` | Recent messages |

### `requests`

| Index | Columns | Purpose |
|-------|---------|---------|
| `idx_requests_brand_reference` | `brand, reference` | Offer–request matching |
| `idx_requests_status` | `status` | Active request queries |

---

## How the Tables Work Together

### 1. Message ingestion

A WhatsApp message arrives in a monitored group.

1. Look up or create a row in **`groups`**.
2. Look up or create a row in **`dealers`** using `whatsapp_id`.
3. Insert the raw text into **`messages`** with `group_id`, `dealer_id`, `received_at`, and `raw_text`.
4. Run the parser and update **`messages.message_type`**, **`parsed_at`**, and **`parse_status`**.

### 2. Storing offers

If `message_type` is `offer` or `offer_list`:

1. Insert one row into **`messages`** first (the parent).
2. For each parsed watch:
   - Look up or create a row in **`watches`** (brand, reference, model, dial, bracelet).
   - Insert one row into **`offers`** with required **`message_id`** and **`watch_id`**.
3. Set **`offers.dealer_id`** (denormalized from the message).
4. Assign **`line_index`** (0, 1, 2, …) to preserve order within the message.

### 3. Storing requests

If `message_type` is `request`:

1. Insert one row into **`requests`** with **`message_id`** set.
2. Copy watch criteria and budget fields from the parser.
3. Set **`status`** to `active`.

### 4. Duplicate detection

Search **`offers`** joined with **`watches`** by `brand`, `reference`, and `usd_price`. Set **`is_duplicate`** and **`duplicate_of_id`** when a match is found.

### 5. Offer–request matching

Query active **`requests`**, compare against new **`offers`**, notify the broker.

---

## Relationship Summary

| From | To | Cardinality | Description |
|------|----|-------------|-------------|
| `groups` | `messages` | One-to-many | A group contains many messages |
| `dealers` | `messages` | One-to-many | A dealer sends many messages |
| `dealers` | `offers` | One-to-many | A dealer lists many watches |
| `messages` | `offers` | One-to-many | One message contains many offers |
| `watches` | `offers` | One-to-many | One watch can have many offers |
| `messages` | `requests` | One-to-zero-or-one | A request message produces at most one request |
| `offers` | `offers` | One-to-many (self) | An offer may duplicate an earlier offer |

**Key rules:**

- `offers.message_id` is NOT NULL — every offer must have exactly one parent message.
- `offers.watch_id` is NOT NULL — every offer must belong to exactly one watch.

---

## Design Decisions (v1)

| Decision | Rationale |
|----------|-----------|
| `groups` table | Messages arrive in WhatsApp groups with distinct country/language context |
| `watches` table | Normalizes watch identity; same watch referenced across many offers |
| Required `watch_id` on offers | Every listing points to a canonical watch record |
| Required `message_id` on offers | Enforces messages as the parent; offers cannot exist without source message |
| `(message_id, line_index)` unique | Prevents duplicate rows for the same watch in a list |
| Denormalize `dealer_id` on `offers` | Faster dealer inventory queries without joining through `messages` |
| Partial indexes | Smaller, faster indexes on nullable columns |
| `ON DELETE RESTRICT` on offers → messages | Prevents deleting a message that still has offers |
| `ON DELETE RESTRICT` on offers → watches | Prevents deleting a watch that still has offers |
| Self-referencing `duplicate_of_id` | Simple duplicate chain without a separate table |

---

## Out of Scope (v1)

- `clients` table
- `matches` / `notifications` tables
- Media attachments (images, PDFs, voice notes)
- Broker user accounts
- Row Level Security policies (add in Supabase after schema creation)
