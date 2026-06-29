# Team Access Pilot — Tailscale

Sprint 37.1 prepares MRV4ULT AI for a short pilot where a teammate can use the dashboard from another laptop on the same Tailnet.

This guide covers **local pilot access only**. It does not replace production deployment, HTTPS, or full user onboarding.

---

## What you need

- MRV4ULT AI running on the **host laptop** (the machine that ingests WhatsApp and connects to Supabase)
- A [Tailscale](https://tailscale.com/) account shared by both laptops
- Teammate email added to the app users table (see login in the app)

---

## 1. Install Tailscale on both laptops

1. Install Tailscale on the **host laptop** (where the app runs).
2. Install Tailscale on the **teammate laptop**.
3. Sign in to the **same Tailnet** on both machines.
4. Confirm both devices show as connected in the Tailscale admin console.

---

## 2. Start the app on the host laptop

From the project root on the host machine:

```bash
uvicorn app:app --reload --host 0.0.0.0 --port 8000
```

Notes:

- `--host 0.0.0.0` binds on all interfaces so Tailscale can reach the app.
- Default port is **8000**; keep host and teammate on the same port unless you change both sides.
- Ensure `.env` is configured on the host (Supabase, session secret, Evolution, etc.) as for normal local development.

---

## 3. Find the host Tailscale IP

On the **host laptop**, open the Tailscale app or run:

**Windows (PowerShell):**

```powershell
tailscale ip -4
```

**macOS / Linux:**

```bash
tailscale ip -4
```

Use the **100.x.x.x** address shown for the host machine (not `127.0.0.1`).

---

## 4. Teammate opens the dashboard

On the **teammate laptop**, open a browser:

```text
http://TAILSCALE-IP:8000
```

Replace `TAILSCALE-IP` with the host’s Tailscale IPv4 address, for example:

```text
http://100.64.0.12:8000
```

Log in with the teammate’s registered email (passwordless pilot login).

---

## 5. Verify connectivity

Before sharing the URL, confirm the app responds on the host:

```text
http://127.0.0.1:8000/health
```

Expected JSON:

```json
{
  "status": "ok",
  "app": "MRV4ULT AI"
}
```

From the teammate laptop (after Tailscale is connected):

```text
http://TAILSCALE-IP:8000/health
```

You should see the same response. If `/health` works but login pages fail, check Tailscale connectivity and Windows firewall rules for port **8000**.

---

## Troubleshooting

| Symptom | Check |
|--------|--------|
| Teammate cannot reach the app | Both laptops on same Tailnet; host app started with `--host 0.0.0.0` |
| Connection refused | Uvicorn running on port 8000; firewall allows inbound 8000 on host |
| Redirect to login on every page | Normal for protected routes; use `/login` with a known user email |
| `/health` works, login fails | User email must exist in Supabase `users` table |
| Wrong time on imports | App displays Europe/Amsterdam; storage is UTC |

---

## Security notes for the pilot

- Tailscale encrypts traffic between devices; the app itself still uses HTTP on port 8000 during local pilot.
- Do not expose port 8000 to the public internet without a reverse proxy and HTTPS.
- Only invite trusted teammates to the Tailnet and the app user list.
- Rotate `SESSION_SECRET` if the pilot machine is shared or compromised.

---

## Related docs

- [`evolution_setup.md`](evolution_setup.md) — WhatsApp / Evolution stack
- [`evolution_webhook_setup.md`](evolution_webhook_setup.md) — webhook configuration
