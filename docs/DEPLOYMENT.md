# Deployment runbook

The frontend and the API are one service — the dashboard is served by the same
FastAPI process — so there is a single deploy, a single URL and no CORS setup.

Total time: about 15 minutes.

---

## 0. Before you start

You need:

- A GitHub account.
- A [Render](https://render.com) account (the free tier is sufficient — no card
  required). Railway, Koyeb or Fly.io work equally well; the Dockerfile is
  portable and step 3 is the only platform-specific part.
- An Anthropic API key from
  <https://console.anthropic.com/settings/keys>.

Confirm the repository has no secrets in it:

```bash
git grep -nE "sk-ant-|ANTHROPIC_API_KEY=[^\"']" -- . ':!.env.example' || echo "clean"
```

`.env` is in `.gitignore` and must never be committed. `.env.example` contains
names only, no values.

---

## 1. Push to a public GitHub repository

Already done for this project — the code lives at
<https://github.com/ZiyaSadik/document-intelligence-platform>.

To reproduce from scratch:

```bash
git init
git add .
git commit -m "Document intelligence platform"
git branch -M main
git remote add origin https://github.com/ZiyaSadik/document-intelligence-platform.git
git push -u origin main
```

Make sure the repository visibility is **Public** — the brief requires it.

---

## 2. Sanity-check locally first

A deploy that fails on something a local run would have caught wastes a cycle.

```bash
cd backend && pytest          # expect 142 passed
cd backend && uvicorn app.main:app --port 8000
curl localhost:8000/api/v1/health
```

If Docker is available, build the image too — this is what Render will run:

```bash
docker build -t docintel .
docker run -p 8000:8000 -e ANTHROPIC_API_KEY=sk-ant-... docintel
curl localhost:8000/api/v1/health
```

---

## 3. Deploy on Render

### Option A — Blueprint (recommended)

`render.yaml` provisions the web service **and** a free PostgreSQL database,
and wires `DATABASE_URL` between them automatically.

1. Go to <https://dashboard.render.com/blueprints> → **New Blueprint Instance**.
2. Select your repository. Render reads `render.yaml`.
3. It will prompt for the one value marked `sync: false`:
   **`ANTHROPIC_API_KEY`** — paste your key.
4. **Apply**. The first build takes 3–6 minutes.

### Option B — Manual

1. **New → PostgreSQL**, free plan. Copy its *Internal Database URL*.
2. **New → Web Service**, connect the repository.
   - Runtime: **Docker** (root `Dockerfile`)
   - Plan: Free
   - Health check path: `/api/v1/health`
3. Environment variables:

   | Key | Value |
   |---|---|
   | `DATABASE_URL` | the internal PostgreSQL URL from step 1 |
   | `ANTHROPIC_API_KEY` | your key |
   | `ANTHROPIC_MODEL` | `claude-opus-5` |
   | `LOG_JSON` | `true` |
   | `ENVIRONMENT` | `production` |

4. **Create Web Service**.

> **Use PostgreSQL, not the SQLite default.** Free-tier container disks are
> ephemeral. A deployed SQLite file is wiped on every redeploy and on cold
> start, so the dashboard would silently empty itself part-way through an
> evaluation. The application handles both; only `DATABASE_URL` changes.

> Render's free web services **sleep after 15 minutes idle** and take
> 30–60 seconds to wake. Hit the URL once shortly before anyone evaluates it.

---

## 4. Verify the deployment

With `BASE=https://document-intelligence-api-lffm.onrender.com`:

```bash
# 1. Health — expect status ok, database connected, extraction_configured true
curl -s "$BASE/api/v1/health"

# 2. Swagger UI loads
curl -s -o /dev/null -w "%{http_code}\n" "$BASE/docs"

# 3. Process a document
curl -s -X POST "$BASE/api/v1/documents/process" \
  -F "file=@data/samples/New Dataset/Balance Sheet/Consolidated Balance Sheet 2024.pdf" \
  -F "document_type=balance_sheet"

# 4. Retrieve it by name
curl -s "$BASE/api/v1/documents/Consolidated%20Balance%20Sheet%202024.pdf"

# 5. Dashboard list
curl -s "$BASE/api/v1/documents"

# 6. Rejection path — expect 400 UNSUPPORTED_FILE_TYPE
echo "not a document" > /tmp/notes.txt
curl -s -X POST "$BASE/api/v1/documents/process" \
  -F "file=@/tmp/notes.txt" -F "document_type=invoice"
```

Then open `$BASE/` in a browser and confirm the dashboard lists what you
uploaded and the result page renders fields, line items, validation checks and
the raw JSON.

If `extraction_configured` is `false`, the key was not saved — set it under
**Environment** and redeploy.

---

## 5. Fill in the URLs

Live deployment URLs (also recorded in [`README.md`](../README.md)):

| What | URL |
|---|---|
| Frontend (dashboard) | <https://document-intelligence-api-lffm.onrender.com> |
| Backend API base | <https://document-intelligence-api-lffm.onrender.com> |
| Swagger / OpenAPI | <https://document-intelligence-api-lffm.onrender.com/docs> |
| Health endpoint | <https://document-intelligence-api-lffm.onrender.com/api/v1/health> |

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| Build fails installing dependencies | Confirm the Docker runtime is selected, not a Python native runtime. Every dependency is a wheel; no system packages are required. |
| `503 MODEL_NOT_CONFIGURED` | `ANTHROPIC_API_KEY` is unset or was rejected. Check the Environment tab; `/api/v1/health` reports `extraction_configured`. |
| `503 MODEL_UNAVAILABLE` with `Connection error.` in the log, while health looks fine | Classically an API key with stray whitespace or quotes around it — a value with a leading space makes an illegal HTTP header, and the SDK reports it as a connection failure. The application strips this defensively, but check the value if you see it. |
| Dashboard empties after a redeploy | `DATABASE_URL` still points at SQLite. Attach the PostgreSQL instance. |
| First request after a pause times out | Free-tier cold start. Retry once. |
| `502 EXTRACTION_FAILED` on a dense statement | The output ceiling was hit. Raise `ANTHROPIC_MAX_TOKENS`. |
| Health shows `database: unavailable` | The database is still provisioning, or `DATABASE_URL` is wrong. Logs give the driver error. |

Server logs are single-line JSON with an `x-request-id` on every record. The
same id is returned in the response header and inside any error body, so a
failing request can be traced end to end from what the caller saw.
