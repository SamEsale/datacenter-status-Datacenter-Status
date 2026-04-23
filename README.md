# Datacenter Status

Datacenter Status is a small full-stack monitoring application for network and service incidents affecting datacenter providers. It combines Microsoft Outlook mailbox ingestion with optional Cachet data, stores normalized provider and incident records in a database, and exposes a FastAPI API consumed by a React dashboard.

## Project Structure

- `backend/`: FastAPI API, background refresh loop, SQLAlchemy models/repository layer, Outlook and Cachet integrations, and Alembic migrations.
- `frontend/`: Vite + React dashboard for viewing provider status, active incidents, and postal-code-related impact.
- `LICENSE`: project license.

## How It Works

The backend treats Outlook as the primary signal source. It periodically fetches recent incident emails from Microsoft Graph, extracts provider names and Swedish postal codes, and upserts those incidents into the database. Cachet support is available as a secondary source for provider metadata and optional incident ingestion.

The API keeps an in-memory cached payload that refreshes on a timer. The frontend polls `/status`, renders provider cards, and lets users inspect incident details or filter providers by Swedish postal code.

## Backend Stack

- FastAPI
- SQLAlchemy 2.x
- Alembic
- `psycopg` for PostgreSQL, with SQLite fallback for local development
- `msal` + Microsoft Graph for Outlook ingestion
- `requests` for external HTTP calls

## Frontend Stack

- React 19
- Vite 7
- React Router
- Chart.js / `react-chartjs-2`

## Main API Endpoints

- `GET /`: service metadata and useful links
- `GET /version`: runtime and cache metadata
- `GET /healthz`: database and cache health information
- `GET /status`: aggregated provider status and active incidents
- `GET /providers/{provider_id}`: provider-specific incident history
- `GET /search/postal-code/{postal_code}`: active incident lookup for a Swedish postal code

## Local Development

### Backend

Install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

Run migrations and start the API:

```bash
bash backend/start.sh
```

By default, the backend listens on port `8000`. If `DATABASE_URL` is not set, it falls back to a local SQLite database at `backend/app.db`.

### Frontend

Install dependencies and start Vite:

```bash
cd frontend
npm install
npm run dev
```

Set `VITE_API_BASE_URL` so the frontend can reach the backend, for example:

```bash
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## Environment Variables

### Required for Outlook ingestion

- `AZURE_TENANT_ID`
- `AZURE_CLIENT_ID`
- `AZURE_CLIENT_SECRET`
- `OUTLOOK_MAILBOX_USER`

### Optional Outlook-related settings

- `OUTLOOK_FETCH_TOP`
- `OUTLOOK_MAX_PAGES`
- `OUTLOOK_MAX_AGE_HOURS`
- `KNOWN_PROVIDERS`
- `POSTAL_CODE_REGEX`

### Optional Cachet settings

- `CACHET_ENABLED`
- `CACHET_BASE_URL`
- `CACHET_API_TOKEN`
- `CACHET_INGEST_INCIDENTS`
- `CACHET_CAN_RENAME_PROVIDERS`

### General runtime settings

- `DATABASE_URL`
- `CACHE_REFRESH_SECONDS`
- `FRONTEND_ORIGINS`
- `LOG_LEVEL`
- `ENV` / `ENVIRONMENT`
- `RENDER`
- `PORT`

## Testing

The repository currently includes backend API coverage in `backend/tests/test_app.py`.

## Notes

- Local `.env` loading is intentionally disabled in production-like environments.
- The backend refresh loop continuously ingests and rebuilds the cached status payload.
- Provider status is derived deterministically, with Outlook treated as the primary source of truth.
