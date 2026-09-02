import pytest

from vajra.core.audit import (
    AuditActorType,
    AuditEventCreate,
    AuditEventType,
    AuditQuery,
    append_audit_event,
    get_audit_trail,
    query_audit_events,
    verify_audit_integrity,
)
from vajra.core.database import close_db, init_db


@pytest.fixture(scope="session")
def event_loop():
    import asyncio

    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    await init_db()
    yield
    await close_db()


@pytest.mark.asyncio
async def test_audit_event_append_and_query(setup_db):
    event = AuditEventCreate(
        event_type=AuditEventType.TRANSACTION_INGESTED,
        actor_type=AuditActorType.SYSTEM,
        actor_id="test_actor",
        correlation_id="test_corr_123",
        payload={"transaction_id": "txn_123", "amount": 1000},
        metadata={"source": "test"},
    )

    result = await append_audit_event(event)
    assert result.correlation_id == "test_corr_123"
    assert result.actor_id == "test_actor"
    assert result.merkle_root is not None
    assert result.sequence_number > 0

    query = AuditQuery(correlation_id="test_corr_123", limit=10)
    events = await query_audit_events(query)
    assert len(events) == 1
    assert events[0].id == result.id


@pytest.mark.asyncio
async def test_audit_integrity_verification(setup_db):
    event = AuditEventCreate(
        event_type=AuditEventType.CHARGEBACK_RECEIVED,
        actor_type=AuditActorType.SYSTEM,
        actor_id="test_actor",
        correlation_id="test_corr_456",
        payload={"chargeback_id": "cb_123", "amount": 5000},
    )

    await append_audit_event(event)
    integrity = await verify_audit_integrity("test_corr_456")
    assert integrity["all_valid"] is True
    assert integrity["total_events"] == 1


@pytest.mark.asyncio
async def test_audit_trail(setup_db):
    event = AuditEventCreate(
        event_type=AuditEventType.TRIBUNAL_DEBATE_STARTED,
        actor_type=AuditActorType.AGENT_PROSECUTOR,
        actor_id="prosecutor_1",
        correlation_id="test_corr_789",
        payload={"case_id": "case_123", "chargeback_id": "cb_456"},
    )

    await append_audit_event(event)
    trail = await get_audit_trail("test_corr_789")
    assert trail["correlation_id"] == "test_corr_789"
    assert trail["event_count"] == 1
    assert trail["integrity_verified"] is True
    assert len(trail["timeline"]) == 1


@pytest.mark.asyncio
async def test_multiple_events_same_correlation(setup_db):
    events = [
        AuditEventCreate(
            event_type=AuditEventType.TRANSACTION_INGESTED,
            actor_type=AuditActorType.SYSTEM,
            actor_id="ingestor",
            correlation_id="multi_corr_1",
            payload={"transaction_id": f"txn_{i}", "amount": 100 * i},
        )
        for i in range(3)
    ]

    for event in events:
        await append_audit_event(event)

    query = AuditQuery(correlation_id="multi_corr_1", limit=10)
    results = await query_audit_events(query)
    assert len(results) == 3

    integrity = await verify_audit_integrity("multi_corr_1")
    assert integrity["all_valid"] is True
    assert integrity["total_events"] == 3
