# MRV4ULT AI Roadmap

Internal product roadmap for the MRV4ULT AI luxury watch broker assistant.

---

## Completed

- **Parser** — OpenAI-based WhatsApp message parser (`main.py`, `prompts/parser_prompt.md`)
- **Regex parser** — Fast local parser without API calls (`watch_parser.py`)
- **Currency conversion** — Original price, currency, USD price, and exchange rate fields
- **Database** — Supabase schema and helpers (`database.py`, `docs/schema.sql`)
- **Duplicate watch detection** — Normalized identity matching in `find_or_create_watch()`
- **Duplicate offer detection** — Active offer deduplication before insert
- **Request matching** — Match new offers to active client requests
- **Search engine** — CLI search with filters and cheapest mode (`search.py`)
- **Dashboard** — FastAPI + Bootstrap internal search UI (`app.py`)
- **Watch detail page** — Read-only `/watch/{watch_id}` with stats and offer list

---

## Next

1. **Dealer CRM** — Manage dealer contacts, notes, and activity
2. **Offer history** — Track sold, withdrawn, and expired offers over time
3. **AI assistant** — Conversational broker assistant on top of stored data
4. **WhatsApp automation** — Ingest messages and send notifications automatically

---

## Sprint tracker

Mark items complete as sprints ship.

### Foundation

- [x] Parser (OpenAI)
- [x] Regex parser
- [x] Currency conversion
- [x] Database design and Supabase integration
- [x] Message and offer ingest pipeline

### Data quality

- [x] Duplicate watch detection
- [x] Duplicate offer detection
- [x] Request matching

### Search and UI

- [x] Search engine (CLI)
- [x] Price filters (`under`, `below`, `max`)
- [x] Cheapest-only search mode
- [x] Internal dashboard (FastAPI)
- [x] Dashboard search UX (form fields, max price, cheapest checkbox)
- [x] Dealer / WhatsApp display in results
- [x] Watch detail page

### CRM and history

- [ ] Dealer CRM
- [ ] Offer history

### Automation and AI

- [ ] AI assistant
- [ ] WhatsApp automation
- [ ] Match notifications
- [ ] Client request management UI

### Future (from PRD)

- [ ] Image and PDF parsing
- [ ] Voice message support
- [ ] Dealer intelligence and pricing insights
- [ ] Broker dashboard analytics
