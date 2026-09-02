import pytest

from vajra.core.causal_kg import (
    EntityType,
    get_fraud_graph,
)
from vajra.core.database import close_db, init_db


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    await init_db()
    yield
    await close_db()


@pytest.fixture
async def fraud_graph(setup_db):
    graph = await get_fraud_graph()
    yield graph
    graph.graph.clear()
    graph.entities.clear()


@pytest.mark.asyncio
async def test_ingest_transaction(fraud_graph):
    txn = {
        "transaction_id": "txn_001",
        "user_id": "user_001",
        "device_id": "dev_001",
        "ip_address": "192.168.1.1",
        "card_fingerprint": "card_001",
        "card_bin": "411111",
        "card_last4": "1111",
        "merchant_id": "merch_001",
        "merchant_category": "retail",
        "amount": 1000,
        "email": "user@example.com",
        "phone": "9999999999",
        "shipping_pincode": "560001",
    }

    entities = await fraud_graph.ingest_transaction(txn)
    assert len(entities) >= 5

    user = fraud_graph.entities.get("user_001")
    assert user is not None
    assert user.type == EntityType.USER

    device = fraud_graph.entities.get("dev_001")
    assert device is not None
    assert device.type == EntityType.DEVICE


@pytest.mark.asyncio
async def test_fraud_ring_detection(fraud_graph):
    for i in range(5):
        txn = {
            "transaction_id": f"txn_ring_{i}",
            "user_id": f"user_ring_{i}",
            "device_id": "shared_device",
            "ip_address": f"10.0.0.{i}",
            "card_fingerprint": f"card_ring_{i}",
            "card_bin": "411111",
            "merchant_id": "merch_001",
            "amount": 1000,
        }
        await fraud_graph.ingest_transaction(txn)

    await fraud_graph.ingest_transaction(
        {
            "transaction_id": "txn_extra",
            "user_id": "user_extra",
            "device_id": "shared_device",
            "ip_address": "10.0.0.100",
            "card_fingerprint": "card_extra",
            "card_bin": "411111",
            "merchant_id": "merch_001",
            "amount": 1000,
        }
    )

    rings = fraud_graph.detect_rings(min_size=3, min_density=0.1)
    assert len(rings) >= 1

    ring = rings[0]
    assert ring.edge_count > 0
    assert ring.density > 0
    assert "card_testing" in ring.fraud_types or "suspicious_cluster" in ring.fraud_types


@pytest.mark.asyncio
async def test_causal_explanation(fraud_graph):
    await fraud_graph.ingest_transaction(
        {
            "transaction_id": "txn_explain",
            "user_id": "user_explain",
            "device_id": "dev_explain",
            "ip_address": "192.168.1.100",
            "card_fingerprint": "card_explain",
            "card_bin": "411111",
            "merchant_id": "merch_001",
            "amount": 5000,
            "velocity_1h": 10,
            "new_device": True,
            "geo_mismatch": True,
        }
    )

    explanation = fraud_graph.explain_transaction(
        "txn_explain",
        {
            "transaction_id": "txn_explain",
            "user_id": "user_explain",
            "device_id": "dev_explain",
            "ip_address": "192.168.1.100",
            "card_fingerprint": "card_explain",
            "amount": 5000,
            "merchant_id": "merch_001",
            "velocity_1h": 10,
            "new_device": True,
            "geo_mismatch": True,
        },
    )

    assert explanation.transaction_id == "txn_explain"
    assert isinstance(explanation.is_fraud, bool)
    assert 0 <= explanation.confidence <= 1
    assert isinstance(explanation.causal_factors, list)
    assert "fraud_probability" in explanation.counterfactual
    assert isinstance(explanation.intervention, str)
    assert "entities" in explanation.subgraph
    assert "edges" in explanation.subgraph


@pytest.mark.asyncio
async def test_counterfactual_reasoning(fraud_graph):
    await fraud_graph.ingest_transaction(
        {
            "transaction_id": "txn_cf",
            "user_id": "user_cf",
            "device_id": "dev_cf",
            "ip_address": "192.168.1.200",
            "card_fingerprint": "card_cf",
            "card_bin": "411111",
            "merchant_id": "merch_001",
            "amount": 1000,
        }
    )

    explanation = fraud_graph.explain_transaction(
        "txn_cf",
        {
            "transaction_id": "txn_cf",
            "user_id": "user_cf",
            "device_id": "dev_cf",
            "ip_address": "192.168.1.200",
            "card_fingerprint": "card_cf",
            "amount": 1000,
            "merchant_id": "merch_001",
        },
    )

    cf = explanation.counterfactual
    assert cf["fraud_probability"] >= cf["without_device"]
    assert cf["fraud_probability"] >= cf["without_ip"]
    assert cf["base_rate"] == 0.05
