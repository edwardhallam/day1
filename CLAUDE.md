# Delivery Tracking Web Service

## Project Context

This is a **greenfield Python/FastAPI delivery tracking service** that wraps the Parcel App API (`api.parcel.app`). Requirements and architecture were produced by Agent C agents (Rita for requirements, Archie for architecture) and live in `docs/`.

**Stack**: Python 3.12, FastAPI, SQLAlchemy 2.0 async, PostgreSQL 16, APScheduler, httpx, Docker Compose v2

## Source of Truth

All implementation MUST follow these documents. Read them before writing code.

### Requirements (read-only — never modify)
- `docs/requirements/00-master-requirements.md` — Start here. 283 requirements, 26 business rules, 4 open questions
- `docs/requirements/01-architecture.md` through `08-deployment.md` — Domain-specific requirements

### Architecture (read-only — never modify)
- `docs/architecture/ARCH-OVERVIEW.md` — Master architecture: Clean Architecture layers, module structure, ADRs
- `docs/architecture/ARCH-DOMAIN.md` — Entities, value objects, repository ABCs
- `docs/architecture/ARCH-APPLICATION.md` — Use cases, DTOs
- `docs/architecture/ARCH-INFRASTRUCTURE.md` — SQLAlchemy models, Parcel API client, APScheduler, mappers
- `docs/architecture/ARCH-PRESENTATION.md` — FastAPI routers, schemas, DI wiring, middleware
- `docs/architecture/IMPL-PLAN.md` — Ordered implementation plan (if present)

### API Reference
- `parcel-api-view-deliveries.md` — Parcel API endpoint docs (rate limit: 20 req/hr)

## Clean Architecture Rules (Non-Negotiable)

**Dependencies flow inward only.**

| Layer | Package | May Import | Must NOT Import |
|-------|---------|------------|-----------------|
| Domain | `app.domain` | Nothing outside itself | SQLAlchemy, FastAPI, httpx, APScheduler |
| Application | `app.application` | Domain only | SQLAlchemy, FastAPI, httpx, APScheduler |
| Infrastructure | `app.infrastructure` | Application + Domain | FastAPI, presentation schemas |
| Presentation | `app.presentation` | Application + Domain | SQLAlchemy internals, httpx, APScheduler |

- Domain entities are **NOT** SQLAlchemy models. Dedicated mappers translate at the infrastructure boundary.
- Use cases receive repository interfaces (ABCs) via constructor injection.
- FastAPI `Depends()` wires concrete implementations in the presentation layer only.
- Every import that crosses a layer boundary must flow inward. Violations are bugs.

## Implementation Order

Follow the inward-to-outward sequence. Each phase should be a separate Claude Code session.

### Phase 1: Scaffolding
- Project directory structure matching ARCH-OVERVIEW §3
- `pyproject.toml` or `requirements.txt`
- `Dockerfile`, `docker-compose.yml`
- Alembic init
- `app/config.py` (pydantic-settings)
- `.env.example`
- Empty `__init__.py` for all packages

### Phase 2: Domain Layer
- Read `ARCH-DOMAIN.md` first
- Entities: `Delivery`, `DeliveryEvent`, `StatusHistory`, `User`, `PollLog` (pure dataclasses or Pydantic BaseModel — NO SQLAlchemy)
- Value objects: `SemanticStatus` enum + `normalize_status()`, `LifecycleGroup` enum
- Repository ABCs: `AbstractDeliveryRepository`, `AbstractUserRepository`, `AbstractPollLogRepository`
- Domain exceptions: `DeliveryNotFoundError`, `InvalidStatusError`, etc.
- Tests: Unit tests for value objects and any domain logic

### Phase 3: Application Layer
- Read `ARCH-APPLICATION.md` first
- Use cases: `AuthenticateUser`, `RefreshToken`, `LogoutUser`, `GetDeliveries`, `GetDeliveryDetail`, `PollAndSync`, `GetHealth`, `GetCarriers`
- DTOs: Input/output contracts for each use case
- Application exceptions
- Tests: Unit tests with mock repositories

### Phase 4: Infrastructure Layer
- Read `ARCH-INFRASTRUCTURE.md` first
- SQLAlchemy ORM models (separate from domain entities)
- Mappers: `to_domain()` / `to_orm()` for each entity
- Concrete repository implementations using async SQLAlchemy
- `ParcelAPIClient` wrapping httpx (respects 20 req/hr rate limit, ±30s jitter)
- APScheduler wiring for 15-minute polling cadence
- Database engine + async session factory
- Alembic migration: `0001_initial_schema.py`
- Tests: Integration tests against a test database

### Phase 5: Presentation Layer
- Read `ARCH-PRESENTATION.md` first
- FastAPI routers: `auth_router`, `deliveries_router`, `system_router`
- Pydantic HTTP schemas (separate from domain entities and DTOs)
- DI wiring via `dependencies.py`
- Middleware: security headers, CORS, rate limiter
- `main.py` app factory with lifespan (scheduler start/stop, DB init)
- Tests: API integration tests with TestClient

### Phase 6: Configuration & Deployment
- Read `docs/requirements/08-deployment.md`
- Docker Compose: `api`, `postgres`, `frontend` services
- Nginx config (reverse proxy)
- Health checks, `depends_on` with `condition: service_healthy`
- Seed script for initial admin user
- `.env.example` with all required vars

### Phase 7: Testing & Validation
- Verify all layers respect dependency rule (no inward imports)
- Run full test suite
- Docker Compose `up` and verify health endpoint
- Test against live Parcel API with a real API key

## Session Hygiene

- **One phase per session.** Start a new Claude Code session for each phase.
- **Read the architecture doc FIRST** in each session before writing any code.
- **Reference requirement IDs** in code comments for traceability (e.g., `# DM-BR-001: Upsert by tracking_number`).
- **Verify the dependency rule** after each phase: grep for forbidden imports.
- **Run tests** before claiming a phase is complete.

## Key Constraints

- Parcel API rate limit: 20 req/hr → 15-min polling uses ~4 req/hr max
- Polling jitter: ±30s mandatory
- `--workers 1` on Uvicorn (APScheduler singleton constraint)
- Cold start: poll immediately when database is empty
- Carrier names NOT stored in DB — frontend caches from Parcel's daily-updated JSON
- `filter_mode=recent` (not `active`) to capture deliveries that just went terminal

## Approved ADRs (Do Not Revisit)

- ADR-001: Monorepo, multi-container
- ADR-002: Python 3.12 + FastAPI backend
- ADR-003: PostgreSQL 16 database
- ADR-004: React 18 + TypeScript + Vite frontend
- ADR-005: Nginx reverse proxy
- ADR-006: Docker Compose v2 orchestration

## Production Deployment

| Property | Value |
|----------|-------|
| Host | `oci-edwardhallam-com` (Oracle Cloud ARM, San Jose) |
| Stack Path | `/opt/stacks/delivery-tracking/` |
| SSH | `ssh oci-edwardhallam-com-admin` |
| Deploy | `ssh oci-edwardhallam-com-admin "sudo systemctl start day1-compose-deploy.service"` |
| CF Tunnel | `oci-edwardhallam-com` (route: day1.edwardhallam.com) |
| Logs | `ssh oci-edwardhallam-com-admin "docker logs delivery-tracking-api-1"` |
| Frontend Port | 8080 (host) → 80 (container) |
