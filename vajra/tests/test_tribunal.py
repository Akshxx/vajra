import pytest

from vajra.agents.tribunal import (
    ArgumentRole,
    EvidenceType,
    get_tribunal_engine,
)
from vajra.core.database import close_db, init_db


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def tribunal(setup_db):
    engine = await get_tribunal_engine()
    yield engine
    engine.cases.clear()


@pytest.mark.asyncio
async def test_create_case(tribunal):
    chargeback_data = {
        "chargeback_id": "cb_test_001",
        "transaction_id": "txn_test_001",
        "amount": 5000,
        "currency": "INR",
        "reason_code": "fraudulent",
        "merchant_id": "merch_001",
        "customer_id": "cust_001",
        "shipping_proof": {"tracking_number": "TRACK123", "delivered": True},
        "avs_result": "match",
        "cvv_result": "match",
        "three_ds_result": "success",
        "device_fingerprint": {"match": True},
        "ip_geolocation": {"match": True},
        "customer_history": {"previous_orders": 10, "previous_chargebacks": 0},
    }

    case = await tribunal.create_case(chargeback_data)
    assert case.chargeback_id == "cb_test_001"
    assert case.transaction_id == "txn_test_001"
    assert case.amount == 5000
    assert len(case.evidence) > 0
    assert case.status == "pending"


@pytest.mark.asyncio
async def test_evidence_loading(tribunal):
    chargeback_data = {
        "chargeback_id": "cb_test_002",
        "transaction_id": "txn_test_002",
        "amount": 3000,
        "currency": "INR",
        "reason_code": "not_received",
        "merchant_id": "merch_001",
        "customer_id": "cust_002",
        "shipping_proof": {"tracking_number": "TRACK456", "delivered": True},
        "avs_result": "match",
        "cvv_result": "match",
    }

    case = await tribunal.create_case(chargeback_data)

    assert any(e.type == EvidenceType.SHIPPING_PROOF for e in case.evidence.values())
    assert any(e.type == EvidenceType.AVS_MATCH for e in case.evidence.values())
    assert any(e.type == EvidenceType.CVV_MATCH for e in case.evidence.values())

    shipping_ev = next(e for e in case.evidence.values() if e.type == EvidenceType.SHIPPING_PROOF)
    assert shipping_ev.strength > 0.5
    assert "TRACK456" in shipping_ev.description


@pytest.mark.asyncio
async def test_run_debate(tribunal):
    chargeback_data = {
        "chargeback_id": "cb_test_003",
        "transaction_id": "txn_test_003",
        "amount": 2000,
        "currency": "INR",
        "reason_code": "fraudulent",
        "merchant_id": "merch_001",
        "customer_id": "cust_003",
        "shipping_proof": {"tracking_number": "TRACK789", "delivered": True},
        "avs_result": "match",
        "cvv_result": "match",
        "three_ds_result": "success",
        "customer_history": {"previous_orders": 5, "previous_chargebacks": 0},
    }

    case = await tribunal.create_case(chargeback_data)
    ruling = await tribunal.run_debate(case.id)

    assert ruling.decision in ["SUBMIT_EVIDENCE", "ACCEPT_CHARGEBACK", "ESCALATE_TO_HUMAN"]
    assert 0 <= ruling.confidence <= 1
    assert len(ruling.reasoning) > 0
    assert ruling.cost_if_wrong > 0
    assert len(ruling.recommended_action) > 0
    assert case.status == "ruled"
    assert case.ruling == ruling


@pytest.mark.asyncio
async def test_tribunal_arguments_structure(tribunal):
    chargeback_data = {
        "chargeback_id": "cb_test_004",
        "transaction_id": "txn_test_004",
        "amount": 1000,
        "currency": "INR",
        "reason_code": "not_received",
        "merchant_id": "merch_001",
        "customer_id": "cust_004",
        "shipping_proof": {"tracking_number": "TRACK999", "delivered": False},
    }

    case = await tribunal.create_case(chargeback_data)
    await tribunal.run_debate(case.id)

    args = case.arguments
    assert len(args) >= 2

    prosecutor_args = [a for a in args if a.role == ArgumentRole.PROSECUTOR]
    defense_args = [a for a in args if a.role == ArgumentRole.DEFENSE]
    judge_args = [a for a in args if a.role == ArgumentRole.JUDGE]

    assert len(prosecutor_args) >= 1
    assert len(defense_args) >= 1
    assert len(judge_args) >= 1

    for arg in args:
        assert arg.content is not None
        assert len(arg.content) > 0
        assert arg.round_number >= 1


@pytest.mark.asyncio
async def test_get_case_by_chargeback(tribunal):
    chargeback_data = {
        "chargeback_id": "cb_lookup_001",
        "transaction_id": "txn_lookup_001",
        "amount": 1500,
        "currency": "INR",
        "reason_code": "fraudulent",
        "merchant_id": "merch_001",
        "customer_id": "cust_lookup",
    }

    case = await tribunal.create_case(chargeback_data)
    found = tribunal.get_case_by_chargeback("cb_lookup_001")
    assert found is not None
    assert found.id == case.id

    not_found = tribunal.get_case_by_chargeback("nonexistent")
    assert not_found is None
