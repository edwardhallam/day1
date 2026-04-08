"""Database seed script — creates the initial admin or demo user on first boot.

Called by entrypoint.sh via ``python -m app.seed`` after ``alembic upgrade head``.
Safe to run on every container restart — exits cleanly when the users table
already contains at least one row (idempotent, DEPLOY-REQ-027).

Exit behaviour:
  - Users table non-empty  →  no-op, exits 0.
  - DEMO_MODE=true, users table empty  →  creates demo user + fixture deliveries, exits 0.
  - Users table empty, credentials not set  →  logs CRITICAL, exits 1.
    entrypoint.sh uses ``set -e``, so the container aborts before Uvicorn starts
    (DEPLOY-REQ-028: fail-fast prevents a running service with no usable login).
  - Users table empty, credentials set  →  creates user, exits 0.
    Logs a WARNING reminding the operator to remove ADMIN_PASSWORD from .env.

Security invariants:
  - The plaintext password is NEVER written to any log (SEC-REQ-061).
  - bcrypt cost is taken from settings.BCRYPT_ROUNDS (≥ 12, SEC-REQ-001–003).
  - Minimum password length: 12 characters (SEC-REQ-005) — not enforced for demo user.

Requirements: DM-BR-014–016, DM-MIG-004, DEPLOY-REQ-026–028, SEC-REQ-001–005,
              SEC-REQ-004 (warn operator to remove credential), SEC-REQ-061.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.infrastructure.database.engine import async_session_factory
from app.infrastructure.database.models.delivery_event_orm import DeliveryEventORM
from app.infrastructure.database.models.delivery_orm import DeliveryORM
from app.infrastructure.database.models.status_history_orm import StatusHistoryORM
from app.infrastructure.database.models.user_orm import UserORM

logger = logging.getLogger(__name__)

# SEC-REQ-005: passwords shorter than this are rejected at seed time.
_MIN_PASSWORD_LENGTH: int = 12


async def _seed_admin_user(
    session: AsyncSession, pwd_context: CryptContext
) -> None:
    """Create the initial admin user from environment credentials.

    Validates ADMIN_USERNAME and ADMIN_PASSWORD, enforces minimum password
    length, then persists the user.  Exits the process on validation failure.
    """
    # ── Credential retrieval ─────────────────────────────────────────────
    username: str | None = settings.ADMIN_USERNAME
    password: str | None = (
        settings.ADMIN_PASSWORD.get_secret_value()
        if settings.ADMIN_PASSWORD is not None
        else None
    )

    # Validate ADMIN_USERNAME
    if not username or not username.strip():
        logger.critical(
            "Seed: ADMIN_USERNAME is not set. "
            "Set it in .env and restart the container (DEPLOY-REQ-028)."
        )
        sys.exit(1)

    # Validate ADMIN_PASSWORD present
    if not password:
        logger.critical(
            "Seed: ADMIN_PASSWORD is not set. "
            "Set it in .env and restart the container (DEPLOY-REQ-028). "
            "Remove it again after the first successful start (SEC-REQ-004)."
        )
        sys.exit(1)

    # Validate ADMIN_PASSWORD length (SEC-REQ-005)
    if len(password) < _MIN_PASSWORD_LENGTH:
        logger.critical(
            "Seed: ADMIN_PASSWORD is too short (%d chars). "
            "Minimum required: %d characters (SEC-REQ-005).",
            len(password),
            _MIN_PASSWORD_LENGTH,
        )
        # Defensively clear the password from local scope before exiting.
        password = None  # noqa: F841
        sys.exit(1)

    password_hash = pwd_context.hash(password)
    # Clear plaintext password from scope (SEC-REQ-061).
    password = None  # noqa: F841

    now = datetime.now(timezone.utc)
    session.add(
        UserORM(
            username=username.strip(),
            password_hash=password_hash,
            is_active=True,
            token_version=1,  # initial token generation (SEC-REQ-020)
            created_at=now,
        )
    )

    # ── Post-seed operator warning (SEC-REQ-004) ─────────────────────────
    logger.warning(
        "Seed: Initial admin user '%s' created successfully. "
        "IMPORTANT — remove ADMIN_PASSWORD from your .env file now "
        "to prevent credential exposure on subsequent restarts.",
        username,
    )


async def _seed_demo_data(
    session: AsyncSession, pwd_context: CryptContext
) -> None:
    """Create a demo user and fixture deliveries for demonstration purposes."""
    now = datetime.now(timezone.utc)

    # ── Demo user (password length check intentionally skipped) ──────────
    session.add(
        UserORM(
            username="demo",
            password_hash=pwd_context.hash("demo"),
            is_active=True,
            token_version=1,
            created_at=now,
        )
    )

    # ── Fixture deliveries ───────────────────────────────────────────────
    _FIXTURES = [
        {
            "tracking_number": "DAY1-AMZN-001",
            "carrier_code": "amazon_logistics",
            "description": "Wireless Headphones",
            "parcel_status_code": 0,
            "semantic_status": "DELIVERED",
            "days_ago": 6,
            "expected_days_ago": 7,
            "events": [
                ("Shipment picked up", "Seattle, WA", 6),
                ("In transit — departed facility", "Portland, OR", 5),
                ("Out for delivery", "San Francisco, CA", 3),
                ("Delivered — left at front door", "San Francisco, CA", 2),
            ],
            "status_transitions": [
                # initial: INFO_RECEIVED
                (None, None, 8, "INFO_RECEIVED", 6),
                # transition: IN_TRANSIT -> DELIVERED
                (2, "IN_TRANSIT", 0, "DELIVERED", 2),
            ],
        },
        {
            "tracking_number": "DAY1-FEDX-002",
            "carrier_code": "fedex",
            "description": "Standing Desk Frame",
            "parcel_status_code": 2,
            "semantic_status": "IN_TRANSIT",
            "days_ago": 4,
            "expected_days_ago": -1,  # expected in the future
            "events": [
                ("Shipment information sent to FedEx", "Memphis, TN", 4),
                ("Picked up", "Memphis, TN", 3),
                ("In transit", "Nashville, TN", 2),
            ],
            "status_transitions": [
                (None, None, 8, "INFO_RECEIVED", 4),
                (8, "INFO_RECEIVED", 2, "IN_TRANSIT", 3),
            ],
        },
        {
            "tracking_number": "DAY1-UPS-003",
            "carrier_code": "ups",
            "description": "Mechanical Keyboard",
            "parcel_status_code": 4,
            "semantic_status": "OUT_FOR_DELIVERY",
            "days_ago": 3,
            "expected_days_ago": 0,  # expected today
            "events": [
                ("Label created", "Louisville, KY", 3),
                ("Departed UPS facility", "Louisville, KY", 2),
                ("Out for delivery", "Austin, TX", 0),
            ],
            "status_transitions": [
                (None, None, 8, "INFO_RECEIVED", 3),
                (8, "INFO_RECEIVED", 4, "OUT_FOR_DELIVERY", 0),
            ],
        },
        {
            "tracking_number": "DAY1-DHL-004",
            "carrier_code": "dhl",
            "description": "Camera Lens",
            "parcel_status_code": 8,
            "semantic_status": "INFO_RECEIVED",
            "days_ago": 1,
            "expected_days_ago": -3,  # expected in 3 days
            "events": [
                ("Shipment information received", "Frankfurt, Germany", 1),
                ("Processed at origin facility", "Frankfurt, Germany", 0),
            ],
            "status_transitions": [
                (None, None, 8, "INFO_RECEIVED", 1),
            ],
        },
        {
            "tracking_number": "DAY1-USPS-005",
            "carrier_code": "usps",
            "description": "Vintage Record",
            "parcel_status_code": 7,
            "semantic_status": "EXCEPTION",
            "days_ago": 5,
            "expected_days_ago": None,  # no expected date
            "events": [
                ("Shipping label created", "Chicago, IL", 5),
                ("Arrived at USPS facility", "Chicago, IL", 4),
                ("Alert: address issue, delivery attempted", "Denver, CO", 2),
            ],
            "status_transitions": [
                (None, None, 8, "INFO_RECEIVED", 5),
                (2, "IN_TRANSIT", 7, "EXCEPTION", 2),
            ],
        },
        {
            "tracking_number": "DAY1-ROYML-006",
            "carrier_code": "royal_mail",
            "description": "Tea Collection",
            "parcel_status_code": 1,
            "semantic_status": "FROZEN",
            "days_ago": 7,
            "expected_days_ago": None,  # no expected date
            "events": [
                ("Collected by Royal Mail", "London, United Kingdom", 7),
                ("Item received at sorting centre", "Heathrow, United Kingdom", 6),
            ],
            "status_transitions": [
                (None, None, 1, "FROZEN", 7),
            ],
        },
    ]

    for fix in _FIXTURES:
        delivery_id = uuid4()
        created = now - timedelta(days=fix["days_ago"])

        timestamp_expected = None
        date_expected_raw = None
        if fix["expected_days_ago"] is not None:
            expected_dt = now - timedelta(days=fix["expected_days_ago"])
            timestamp_expected = expected_dt
            date_expected_raw = expected_dt.strftime("%Y-%m-%d")

        session.add(
            DeliveryORM(
                id=delivery_id,
                tracking_number=fix["tracking_number"],
                carrier_code=fix["carrier_code"],
                description=fix["description"],
                extra_information=None,
                parcel_status_code=fix["parcel_status_code"],
                semantic_status=fix["semantic_status"],
                date_expected_raw=date_expected_raw,
                date_expected_end_raw=None,
                timestamp_expected=timestamp_expected,
                timestamp_expected_end=None,
                first_seen_at=created,
                last_seen_at=now,
                created_at=created,
                updated_at=now,
                last_raw_response=None,
            )
        )

        for seq, (desc, location, days_offset) in enumerate(fix["events"]):
            event_dt = now - timedelta(days=days_offset)
            session.add(
                DeliveryEventORM(
                    id=uuid4(),
                    delivery_id=delivery_id,
                    event_description=desc,
                    event_date_raw=event_dt.strftime("%Y-%m-%d"),
                    location=location,
                    additional_info=None,
                    sequence_number=seq,
                    recorded_at=event_dt,
                )
            )

        for prev_code, prev_sem, new_code, new_sem, days_offset in fix["status_transitions"]:
            session.add(
                StatusHistoryORM(
                    id=uuid4(),
                    delivery_id=delivery_id,
                    previous_status_code=prev_code,
                    previous_semantic_status=prev_sem,
                    new_status_code=new_code,
                    new_semantic_status=new_sem,
                    detected_at=now - timedelta(days=days_offset),
                    poll_log_id=None,
                )
            )

    logger.info("Seed: Demo user and %d fixture deliveries created.", len(_FIXTURES))


async def seed_initial_user() -> None:
    """Create the initial user when the users table is empty.

    In demo mode, seeds a demo user with fixture deliveries.
    In normal mode, seeds an admin user from environment credentials.

    This function is idempotent — it performs a row-count check before doing
    any write.  It is safe to call unconditionally on every container start.

    Raises:
        SystemExit(1): when the database is empty and required credentials
            are absent or too short (normal mode only).
    """
    async with async_session_factory() as session:

        # ── Idempotency guard (DM-MIG-004) ──────────────────────────────────
        count: int = await session.scalar(select(func.count(UserORM.id)))

        if count > 0:
            logger.info(
                "Seed: users table contains %d row(s) — initial seed skipped.",
                count,
            )
            return

        # ── Password hashing context (SEC-REQ-001–003) ──────────────────────
        pwd_context = CryptContext(
            schemes=["bcrypt"],
            deprecated="auto",
            bcrypt__rounds=settings.BCRYPT_ROUNDS,
        )

        if settings.DEMO_MODE:
            logger.info("Seed: DEMO_MODE enabled — seeding demo data …")
            await _seed_demo_data(session, pwd_context)
        else:
            logger.info("Seed: users table is empty — validating seed credentials …")
            await _seed_admin_user(session, pwd_context)

        await session.commit()


if __name__ == "__main__":
    # Configure minimal logging so the script is self-contained when invoked
    # directly (python -m app.seed) without the full Uvicorn logging stack.
    logging.basicConfig(
        level=settings.LOG_LEVEL.upper(),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(seed_initial_user())
