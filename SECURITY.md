# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| `main` branch | ✅ |

Report issues for the latest commit on `main`.

---

## Threat model

**openai-local-chat** is a **single-user, self-hosted proxy** to OpenAI. It is **not** multi-tenant software.

| Asset | Risk if exposed |
|-------|-----------------|
| `OPENAI_API_KEY` | Full API access, financial loss |
| `OPENAI_ADMIN_API_KEY` | Organization usage/cost data |
| Chat history (`data/chat.sqlite`) | Confidential conversations |
| Unauthenticated HTTP endpoint | Anyone can spend your API quota |

---

## Default security posture

- API keys load from `.env` on the **server only** — never sent to the browser.
- `.env` and `data/` are gitignored.
- No built-in login, RBAC, or CSRF tokens.
- CORS is not explicitly restricted (same-origin UI by default).
- File uploads validated by extension and size (chat: `.md`/`.txt` 512 KB; images: model-specific limits).

**Binding to `127.0.0.1` is the recommended default** for personal use.

---

## If you expose the app to a network

Before making the service reachable outside localhost:

1. **Add authentication** — reverse proxy (Basic Auth, OAuth2 Proxy, Cloudflare Access, VPN).
2. **Use HTTPS** — terminate TLS at the proxy.
3. **Restrict firewall** — allow only trusted IPs if possible.
4. **Separate keys** — use a dedicated OpenAI project/key with spending limits.
5. **Monitor billing** — enable usage alerts in OpenAI dashboard.
6. **Do not run as root** — dedicated system user (see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)).

---

## Secrets handling

- Never commit `.env`, API keys, or `data/chat.sqlite` to public repositories.
- Rotate keys immediately if leaked.
- Admin billing keys should have **minimum scope** (`api.usage.read` only).
- Pre-commit: scan for `sk-` patterns before pushing.

---

## Reporting a vulnerability

If you discover a security issue in this project:

1. **Do not** open a public GitHub issue for exploitable vulnerabilities.
2. Email **nikolaj.dudin.90@gmail.com** with:
   - Description and impact
   - Steps to reproduce
   - Affected version/commit
3. Expect a response within **7 days**.

We will coordinate disclosure and credit reporters when appropriate.

---

## Known limitations (not bugs)

- Cooperative job cancellation cannot abort in-flight OpenAI requests.
- SQLite is not encrypted at rest — protect filesystem permissions (`chmod 600` on `.env` and `data/`).
- Auto model routing sends a classifier request (extra API call and data to OpenAI).
- Error messages may include truncated raw API text in `error.raw` for debugging.

---

## Hardening checklist

- [ ] App listens on `127.0.0.1` or behind authenticated proxy
- [ ] `.env` permissions `600`, owned by service user
- [ ] OpenAI key has org/project spending limits
- [ ] Backups of `data/` encrypted or access-controlled
- [ ] Dependencies updated (`pip install -U -r requirements.txt`)
- [ ] Admin key optional and scoped

See also [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) and [docs/CONFIGURATION.md](docs/CONFIGURATION.md).
