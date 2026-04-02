# Delivery Tracker

A self-hosted web dashboard for tracking parcels via the [Parcel App](https://parcelapp.net) API. Consolidates all your deliveries into a single view with persistent history, normalised statuses, and automatic background polling.

![Delivery List](docs/screenshots/delivery-list.png)

## What it does

| Feature | Description |
|---------|-------------|
| **Background polling** | Calls the Parcel API every 15 minutes (well within the 20 req/hr rate limit) |
| **Persistent history** | Retains every delivery, tracking event, and status change indefinitely |
| **Status normalisation** | Translates Parcel's integer status codes into semantic, human-readable states |
| **Web dashboard** | Delivery list with filtering, search, sorting, and detail views |
| **Single-user auth** | JWT-based authentication with instant session invalidation |
| **Docker deployment** | Single `docker compose up -d` to run the full stack |

## Screenshots

<details>
<summary>Login</summary>

![Login](docs/screenshots/login.png)
</details>

<details>
<summary>Delivery Detail</summary>

![Delivery Detail](docs/screenshots/delivery-detail.png)
</details>

## Architecture

```
Browser ──► Nginx (:80) ──► FastAPI (:8000) ──► PostgreSQL (:5432)
                                    │
                                    ├── APScheduler (15-min poll cycle)
                                    └── httpx ──► api.parcel.app
```

Clean Architecture with strict dependency rules:

| Layer | Package | Depends On |
|-------|---------|------------|
| Domain | `app.domain` | Nothing |
| Application | `app.application` | Domain |
| Infrastructure | `app.infrastructure` | Application, Domain |
| Presentation | `app.presentation` | Application, Domain |

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python 3.12, FastAPI, SQLAlchemy 2.0 (async), APScheduler |
| Database | PostgreSQL 16, Alembic migrations |
| Frontend | React 18, TypeScript, Vite, TanStack Query v5, Tailwind CSS |
| Auth | JWT (python-jose), bcrypt (passlib), httpOnly refresh cookies |
| HTTP Client | httpx (shared async client with connection reuse) |
| Proxy | Nginx (SPA fallback + API reverse proxy) |
| Deployment | Docker Compose v2, three services |

## Quick Start

### Prerequisites

- Docker and Docker Compose v2
- A Parcel App API key ([generate one here](https://web.parcelapp.net))

### 1. Clone and configure

```bash
git clone https://github.com/edwardhallam/delivery-tracking.git
cd delivery-tracking
cp .env.example .env
```

Edit `.env` and fill in the required values:

```env
POSTGRES_PASSWORD=<choose-a-strong-password>
DATABASE_URL=postgresql+psycopg://delivery_user:<your-password>@postgres:5432/delivery_tracker
PARCEL_API_KEY=<your-parcel-api-key>
JWT_SECRET_KEY=<generate-with: python3 -c "import secrets; print(secrets.token_hex(32))">
ADMIN_USERNAME=admin
ADMIN_PASSWORD=<min-12-characters>
```

### 2. Launch

```bash
docker compose up -d
```

This will:
1. Start PostgreSQL and wait for it to be healthy
2. Run Alembic migrations to create the schema
3. Seed the admin user from `ADMIN_USERNAME` / `ADMIN_PASSWORD`
4. Start the FastAPI backend (single worker for APScheduler singleton)
5. Build and serve the React frontend via Nginx

### 3. Access

Open **http://localhost** (or the port set in `FRONTEND_HTTP_PORT`) and log in with your admin credentials.

The first poll cycle runs immediately on startup. Your deliveries should appear within a few seconds.

### 4. Post-setup

Remove `ADMIN_PASSWORD` from `.env` after the first successful start to prevent credential exposure on subsequent restarts.

## Configuration

All configuration is via environment variables in `.env`. See [`.env.example`](.env.example) for the full list with documentation.

| Variable | Required | Default | Description |
|----------|:--------:|---------|-------------|
| `POSTGRES_PASSWORD` | Yes | | Database password |
| `PARCEL_API_KEY` | Yes | | Parcel App API key |
| `JWT_SECRET_KEY` | Yes | | JWT signing key (min 32 chars) |
| `ADMIN_USERNAME` | First run | `admin` | Initial admin username |
| `ADMIN_PASSWORD` | First run | | Initial admin password (min 12 chars) |
| `FRONTEND_HTTP_PORT` | No | `80` | Host port for the web UI |
| `POLL_INTERVAL_MINUTES` | No | `15` | Polling interval |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | No | `60` | JWT access token TTL |
| `ENVIRONMENT` | No | `production` | `development` enables CORS + API docs |

## API

When `ENVIRONMENT=development`, interactive API docs are available at `/api/docs`.

| Method | Path | Auth | Description |
|--------|------|:----:|-------------|
| `POST` | `/api/auth/login` | No | Authenticate, receive JWT |
| `POST` | `/api/auth/refresh` | Cookie | Refresh access token |
| `POST` | `/api/auth/logout` | Yes | Invalidate all tokens |
| `GET` | `/api/deliveries/` | Yes | Filtered, paginated delivery list |
| `GET` | `/api/deliveries/{id}` | Yes | Full delivery detail with events |
| `GET` | `/api/health` | No | Service health check |
| `GET` | `/api/carriers` | Yes | Cached carrier code/name list |

## Status Mapping

Parcel's integer status codes are normalised into semantic statuses grouped by lifecycle:

| Group | Statuses | Badge |
|-------|----------|-------|
| **Active** | Info Received, In Transit, Out for Delivery, Awaiting Pickup | Blue |
| **Attention** | Delivery Failed, Exception, Not Found, Unknown | Red |
| **Terminal** | Delivered (green), Stalled (grey) | Green/Grey |

## Project Structure

```
.
├── api/                          # FastAPI backend
│   ├── app/
│   │   ├── domain/               # Entities, value objects, repository ABCs
│   │   ├── application/          # Use cases, DTOs, service interfaces
│   │   ├── infrastructure/       # SQLAlchemy, httpx, APScheduler
│   │   └── presentation/         # FastAPI routers, schemas, middleware
│   ├── alembic/                  # Database migrations
│   └── tests/                    # Unit + integration tests
├── frontend/                     # React SPA
│   ├── src/
│   │   ├── components/           # Header, StatusBadge, DeliveryTable, etc.
│   │   ├── pages/                # Login, DeliveryList, DeliveryDetail
│   │   ├── hooks/                # TanStack Query hooks
│   │   ├── context/              # Auth context (in-memory token)
│   │   └── api/                  # Axios client with interceptors
│   └── nginx.conf                # Reverse proxy + SPA routing
├── docs/                         # Requirements + architecture specs
├── docker-compose.yml            # Three-service orchestration
└── .env.example                  # Configuration template
```

## Development

Set `ENVIRONMENT=development` in `.env` to enable:
- CORS for `localhost:3000` and `localhost:5173`
- Swagger UI at `/api/docs`
- SQLAlchemy echo mode (SQL logging)

### Running the frontend locally (outside Docker)

```bash
cd frontend
npm install
npm run dev    # Vite dev server on :5173 with API proxy
```

## License

MIT
