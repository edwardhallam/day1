"""Root conftest.py — environment bootstrap and shared fixtures.

Environment variables are set with ``os.environ.setdefault`` *before* any
``app.*`` import so that pydantic-settings reads them when ``app.config``
is first imported during test collection.  Real env vars take precedence —
CI can override by setting them in the environment.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

# ── Test env vars — set before any app import ─────────────────────────────
os.environ.setdefault("PARCEL_API_KEY", "test-api-key-for-testing-only")
os.environ.setdefault("JWT_SECRET_KEY", "exactly-32-chars-for-testing-12345678")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://test:test@localhost:5432/test_delivery",
)
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("BCRYPT_ROUNDS", "10")  # minimum valid; keeps tests fast-ish
os.environ.setdefault("DEMO_MODE", "false")

import pytest

from app.domain.entities.delivery import Delivery
from app.domain.entities.delivery_event import DeliveryEvent
from app.domain.entities.poll_log import PollLog, PollOutcome
from app.domain.entities.status_history import StatusHistory
from app.domain.entities.user import User
from app.domain.repositories.abstract_delivery_repository import (
    AbstractDeliveryRepository,
)
from app.domain.repositories.abstract_poll_log_repository import (
    AbstractPollLogRepository,
)
from app.domain.repositories.abstract_user_repository import AbstractUserRepository
from app.domain.value_objects.semantic_status import SemanticStatus
from app.application.dtos.delivery_dtos import DeliveryFilterParams


# ---------------------------------------------------------------------------
# In-memory mock repositories
# ---------------------------------------------------------------------------


class MockDeliveryRepository(AbstractDeliveryRepository):
    """Fully in-memory delivery repository for unit tests.

    All methods are ``async`` to match the ABC contract; none touch a DB.
    """

    def __init__(self) -> None:
        self.deliveries: dict[UUID, Delivery] = {}
        self.events: list[DeliveryEvent] = []
        self.history: list[StatusHistory] = []
        self._event_fingerprints: set[tuple] = set()

    async def get_snapshot(self) -> dict[tuple[str, str], UUID]:
        return {
            (d.tracking_number, d.carrier_code): d.id
            for d in self.deliveries.values()
        }

    async def get_by_id(self, delivery_id: UUID) -> Optional[Delivery]:
        return self.deliveries.get(delivery_id)

    async def list_filtered(
        self,
        filter_params: DeliveryFilterParams,
    ) -> tuple[list[Delivery], int]:
        items = list(self.deliveries.values())
        total = len(items)
        start = (filter_params.page - 1) * filter_params.page_size
        end = start + filter_params.page_size
        return items[start:end], total

    async def create(self, delivery: Delivery) -> Delivery:
        self.deliveries[delivery.id] = delivery
        return delivery

    async def update(self, delivery: Delivery) -> Delivery:
        self.deliveries[delivery.id] = delivery
        return delivery

    async def create_event(self, event: DeliveryEvent) -> Optional[DeliveryEvent]:
        fingerprint = (
            event.delivery_id,
            event.event_description,
            event.event_date_raw,
        )
        if fingerprint in self._event_fingerprints:
            return None  # duplicate — silently dropped (DM-BR-007)
        self._event_fingerprints.add(fingerprint)
        self.events.append(event)
        return event

    async def get_events_for_delivery(
        self, delivery_id: UUID
    ) -> list[DeliveryEvent]:
        return [e for e in self.events if e.delivery_id == delivery_id]

    async def create_status_history(self, entry: StatusHistory) -> StatusHistory:
        self.history.append(entry)
        return entry

    async def get_status_history_for_delivery(
        self, delivery_id: UUID
    ) -> list[StatusHistory]:
        return [h for h in self.history if h.delivery_id == delivery_id]


class MockUserRepository(AbstractUserRepository):
    """In-memory user repository for unit tests."""

    def __init__(self) -> None:
        self.users: dict[str, User] = {}  # username → User
        self.update_last_login_called: list[int] = []
        self.increment_token_version_called: list[int] = []

    async def get_by_username(self, username: str) -> Optional[User]:
        return self.users.get(username)

    async def get_by_id(self, user_id: int) -> Optional[User]:
        return next(
            (u for u in self.users.values() if u.id == user_id), None
        )

    async def update_last_login(self, user_id: int) -> None:
        self.update_last_login_called.append(user_id)
        for user in self.users.values():
            if user.id == user_id:
                user.last_login_at = datetime.now(tz=timezone.utc)

    async def increment_token_version(self, user_id: int) -> int:
        self.increment_token_version_called.append(user_id)
        for user in self.users.values():
            if user.id == user_id:
                user.token_version += 1
                return user.token_version
        return 0

    async def get_user_count(self) -> int:
        return len(self.users)

    async def create(self, user: User) -> User:
        self.users[user.username] = user
        return user


class MockPollLogRepository(AbstractPollLogRepository):
    """In-memory poll log repository for unit tests."""

    def __init__(self) -> None:
        self.logs: list[PollLog] = []
        self.consecutive_errors_override: Optional[int] = None

    async def create_in_progress(self, started_at: datetime) -> PollLog:
        log = PollLog(
            id=uuid4(),
            started_at=started_at,
            outcome=PollOutcome.IN_PROGRESS,
        )
        self.logs.append(log)
        return log

    async def complete(
        self,
        poll_id: UUID,
        outcome: PollOutcome,
        completed_at: datetime,
        deliveries_fetched: Optional[int],
        new_deliveries: Optional[int],
        status_changes: Optional[int],
        new_events: Optional[int],
        error_message: Optional[str],
    ) -> PollLog:
        for log in self.logs:
            if log.id == poll_id:
                log.outcome = outcome
                log.completed_at = completed_at
                log.deliveries_fetched = deliveries_fetched
                log.new_deliveries = new_deliveries
                log.status_changes = status_changes
                log.new_events = new_events
                log.error_message = error_message
                return log
        raise ValueError(f"PollLog {poll_id} not found")

    async def get_recent(self, limit: int = 10) -> list[PollLog]:
        completed = [
            l for l in self.logs if l.outcome != PollOutcome.IN_PROGRESS
        ]
        return sorted(
            completed, key=lambda l: l.started_at, reverse=True
        )[:limit]

    async def get_last_successful(self) -> Optional[PollLog]:
        successful = [
            l for l in self.logs if l.outcome == PollOutcome.SUCCESS
        ]
        return successful[-1] if successful else None

    async def count_consecutive_errors(self) -> int:
        if self.consecutive_errors_override is not None:
            return self.consecutive_errors_override
        count = 0
        completed = [
            l
            for l in self.logs
            if l.outcome != PollOutcome.IN_PROGRESS
        ]
        for log in reversed(completed):
            if log.outcome == PollOutcome.SUCCESS:
                break
            count += 1
        return count


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_delivery_repo() -> MockDeliveryRepository:
    """Fresh in-memory delivery repository per test."""
    return MockDeliveryRepository()


@pytest.fixture
def mock_user_repo() -> MockUserRepository:
    """Fresh in-memory user repository per test."""
    return MockUserRepository()


@pytest.fixture
def mock_poll_log_repo() -> MockPollLogRepository:
    """Fresh in-memory poll log repository per test."""
    return MockPollLogRepository()


# ---------------------------------------------------------------------------
# Entity factories (helpers, not fixtures — call directly in tests)
# ---------------------------------------------------------------------------


def make_user(
    *,
    username: str = "testuser",
    password_hash: str = "",  # set per-test for auth tests
    is_active: bool = True,
    token_version: int = 1,
    user_id: int = 1,
) -> User:
    """Factory for User entities in tests."""
    return User(
        id=user_id,
        username=username,
        password_hash=password_hash,
        created_at=datetime.now(tz=timezone.utc),
        is_active=is_active,
        token_version=token_version,
    )


def make_delivery(
    *,
    tracking_number: str = "TRACK123",
    carrier_code: str = "UPS",
    description: str = "Test Package",
    parcel_status_code: int = 2,
    semantic_status: SemanticStatus = SemanticStatus.IN_TRANSIT,
    delivery_id: Optional[UUID] = None,
) -> Delivery:
    """Factory for Delivery entities in tests."""
    now = datetime.now(tz=timezone.utc)
    return Delivery(
        id=delivery_id or uuid4(),
        tracking_number=tracking_number,
        carrier_code=carrier_code,
        description=description,
        extra_information=None,
        parcel_status_code=parcel_status_code,
        semantic_status=semantic_status,
        date_expected_raw=None,
        date_expected_end_raw=None,
        timestamp_expected=None,
        timestamp_expected_end=None,
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )


@pytest.fixture
def sample_delivery() -> Delivery:
    """A sample in-transit delivery entity."""
    return make_delivery()
