#!/usr/bin/env python3
"""
VAJRA Demo Script — Simulates Razorpay Webhooks
Run this while `python3 -m vajra` is running in another terminal.
"""

import asyncio
import json
import httpx
from datetime import datetime

BASE_URL = "http://localhost:8000/api/v1"

# ============================================================
# RAZORPAY WEBHOOK PAYLOAD STRUCTURES (from their docs)
# ============================================================

PAYMENT_CAPTURED_WEBHOOK = {
    "event": "payment.captured",
    "payload": {
        "payment": {
            "id": "pay_demo_001",
            "entity": "payment",
            "amount": 500000,  # in paise (₹5000)
            "currency": "INR",
            "status": "captured",
            "method": "card",
            "card_id": "card_demo_001",
            "card": {
                "id": "card_demo_001",
                "network": "Visa",
                "last4": "4242",
                "issuer": "HDFC Bank",
                "international": False,
                "emi": False
            },
            "bank": None,
            "wallet": None,
            "vpa": None,
            "email": "customer@example.com",
            "contact": "+919876543210",
            "notes": {"order_id": "order_demo_001"},
            "fee": 10000,
            "tax": 1800,
            "error_code": None,
            "error_description": None,
            "created_at": 1699900000,
            "captured": True,
            "order_id": "order_demo_001"
        }
    }
}

DISPUTE_CREATED_WEBHOOK = {
    "event": "dispute.created",
    "payload": {
        "dispute": {
            "id": "disp_demo_001",
            "entity": "dispute",
            "amount": 500000,
            "currency": "INR",
            "payment_id": "pay_demo_001",
            "reason_code": "fraudulent",
            "phase": "chargeback",
            "status": "open",
            "created_at": 1699980000,
            "updated_at": 1699980000
        }
    }
}


# ============================================================
# VAJRA API CLIENT
# ============================================================

class VajraDemoClient:
    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        await self.client.aclose()

    async def health_check(self):
        resp = await self.client.get(f"{self.base_url}/health")
        return resp.json()

    async def ingest_transaction(self, payload: dict):
        """POST /transactions/ingest — accepts Razorpay payment.captured format"""
        # Transform Razorpay webhook → VAJRA transaction format
        p = payload["payload"]["payment"]
        txn = {
            "transaction_id": p["id"],
            "amount": p["amount"] / 100,  # paise → rupees
            "currency": p["currency"],
            "user_id": p["email"].split("@")[0],  # demo: use email prefix as user_id
            "device_id": f"dev_{p['id'][-6:]}",  # demo: derive from payment_id
            "ip_address": "192.168.1.100",  # demo: would come from your frontend
            "card_fingerprint": p.get("card_id", "card_unknown"),
            "card_bin": "411111",  # demo
            "merchant_id": "merch_demo_001",
            "merchant_category": "ecommerce",
            "email": p.get("email"),
            "phone": p.get("contact"),
            "shipping_pincode": "560001",
            "avs_result": "match",
            "cvv_result": "match",
            "three_ds_result": "success",
        }
        resp = await self.client.post(f"{self.base_url}/transactions/ingest", json=txn)
        return resp.json()

    async def ingest_raw_transaction(self, txn: dict):
        """POST /transactions/ingest — direct VAJRA transaction format"""
        resp = await self.client.post(f"{self.base_url}/transactions/ingest", json=txn)
        return resp.json()

    async def explain_fraud(self, transaction_id: str, **overrides):
        """POST /fraud/explain — causal fraud explanation"""
        payload = {
            "transaction_id": transaction_id,
            "user_id": transaction_id.replace("pay_", "user_"),
            "device_id": f"dev_{transaction_id[-6:]}",
            "ip_address": "192.168.1.100",
            "card_fingerprint": f"card_{transaction_id[-6:]}",
            "amount": 5000,
            "merchant_id": "merch_demo_001",
            "velocity_1h": overrides.get("velocity_1h", 5),
            "new_device": overrides.get("new_device", False),
            "new_ip": overrides.get("new_ip", False),
            "geo_mismatch": overrides.get("geo_mismatch", False),
        }
        resp = await self.client.post(f"{self.base_url}/fraud/explain", json=payload)
        return resp.json()

    async def defend_chargeback(self, chargeback_id: str, **evidence):
        """POST /chargebacks/defend — full tribunal pipeline"""
        payload = {
            "chargeback_id": chargeback_id,
            "transaction_id": chargeback_id.replace("cb_", "txn_"),
            "amount": evidence.get("amount", 3000),
            "currency": "INR",
            "reason_code": evidence.get("reason_code", "fraudulent"),
            "merchant_id": "merch_demo_001",
            "customer_id": evidence.get("customer_id", "cust_demo_001"),
            "shipping_proof": evidence.get("shipping_proof", {"tracking_number": "TRACK123", "delivered": True}),
            "avs_result": evidence.get("avs_result", "match"),
            "cvv_result": evidence.get("cvv_result", "match"),
            "three_ds_result": evidence.get("three_ds_result", "success"),
            "customer_history": evidence.get("customer_history", {"previous_orders": 10, "previous_chargebacks": 0}),
        }
        resp = await self.client.post(f"{self.base_url}/chargebacks/defend", json=payload)
        return resp.json()

    async def get_audit_trail(self, correlation_id: str):
        """GET /audit/trail/{correlation_id}"""
        resp = await self.client.get(f"{self.base_url}/audit/trail/{correlation_id}")
        return resp.json()

    async def detect_fraud_rings(self, min_size: int = 3, min_density: float = 0.2):
        """POST /fraud/detect-rings"""
        resp = await self.client.post(f"{self.base_url}/fraud/detect-rings", json={"min_size": min_size, "min_density": min_density})
        return resp.json()


# ============================================================
# DEMO SCENARIOS
# ============================================================

async def run_scenario_1_normal_transaction(client: VajraDemoClient):
    """Scenario 1: Normal transaction → low fraud score"""
    print("\n" + "="*60)
    print("SCENARIO 1: Normal Transaction (Low Risk)")
    print("="*60)

    # Simulate Razorpay payment.captured webhook
    print("\n📨 Simulating Razorpay webhook: payment.captured")
    result = await client.ingest_transaction(PAYMENT_CAPTURED_WEBHOOK)
    print(f"✅ Ingested: {result}")

    txn_id = PAYMENT_CAPTURED_WEBHOOK["payload"]["payment"]["id"]

    # Get fraud explanation
    print("\n🔍 Getting fraud explanation...")
    explanation = await client.explain_fraud(txn_id)
    print(json.dumps(explanation, indent=2, default=str))

    print(f"\n📊 Result: is_fraud={explanation['is_fraud']}, confidence={explanation['confidence']}")
    print(f"🛡️  Intervention: {explanation['intervention']}")


async def run_scenario_2_suspicious_transaction(client: VajraDemoClient):
    """Scenario 2: Suspicious transaction → high fraud score + causal proof"""
    print("\n" + "="*60)
    print("SCENARIO 2: Suspicious Transaction (High Risk — Card Testing Ring)")
    print("="*60)

    # Create a suspicious transaction
    txn_id = "pay_suspicious_ring_001"
    print("\n📨 Ingesting suspicious transaction (15 txns/hr, same device)...")
    await client.ingest_transaction({
        "event": "payment.captured",
        "payload": {"payment": {
            "id": txn_id, "amount": 500000, "currency": "INR",
            "status": "captured", "method": "card",
            "card_id": "card_ring_001",
            "email": "fraudster@test.com", "contact": "+919999999999",
            "order_id": "order_ring_001"
        }}
    })

    print("\n🔍 Getting fraud explanation (high velocity, new device, geo mismatch)...")
    explanation = await client.explain_fraud(txn_id, velocity_1h=15, new_device=True, geo_mismatch=True)
    print(json.dumps(explanation, indent=2, default=str))

    print(f"\n📊 Result: is_fraud={explanation['is_fraud']}, confidence={explanation['confidence']}")
    print(f"🛡️  Intervention: {explanation['intervention']}")
    print(f"🔗 Causal factors: {len(explanation['causal_factors'])}")
    if explanation['causal_factors']:
        for cf in explanation['causal_factors']:
            print(f"   → {cf}")


async def run_scenario_3_chargeback_defense(client: VajraDemoClient):
    """Scenario 3: Full chargeback defense pipeline"""
    print("\n" + "="*60)
    print("SCENARIO 3: Chargeback Defense (Full Tribunal Pipeline)")
    print("="*60)

    cb_id = "cb_demo_001"
    print(f"\n⚖️  Defending chargeback: {cb_id}")
    print("   Reason: fraudulent | Evidence: delivered + AVS/CVV/3DS match + 12 clean orders")

    result = await client.defend_chargeback(
        cb_id,
        amount=3000,
        reason_code="fraudulent",
        customer_id="cust_loyal_001",
        shipping_proof={"tracking_number": "TRACK123456", "delivered": True},
        avs_result="match",
        cvv_result="match",
        three_ds_result="success",
        customer_history={"previous_orders": 12, "previous_chargebacks": 0}
    )

    print(json.dumps(result, indent=2, default=str))

    print(f"\n⚖️  Ruling: {result['ruling']['decision']} (confidence: {result['ruling']['confidence']})")
    print(f"💰 Cost if wrong: ₹{result['ruling']['cost_if_wrong']:,}")
    print(f"📋 Policy: {result['policy']['status']} ({result['policy']['actions_executed']} actions)")
    for r in result['execution_results']:
        print(f"   ✅ {r['action_id']}: {r['status']}")

    # Get audit trail
    print("\n📜 Fetching audit trail...")
    audit = await client.get_audit_trail(cb_id)
    print(f"   Events: {audit['event_count']} | Integrity: {audit['integrity_verified']}")
    for event in audit['timeline'][:5]:
        print(f"   {event['timestamp']} | {event['event_type']} | {event['actor']}")


async def run_scenario_4_fraud_ring_detection(client: VajraDemoClient):
    """Scenario 4: Proactive fraud ring detection — Creates a card-testing ring then detects it"""
    print("\n" + "="*60)
    print("SCENARIO 4: Proactive Fraud Ring Detection (Card-Testing Ring)")
    print("="*60)

    print("\n📨 Creating card-testing ring: 5 users, 5 cards, 1 shared device...")

    # Create 5 transactions with SAME DEVICE but DIFFERENT USERS/CARDS
    # This forms a card-testing ring: 1 device → 5 cards
    ring_device = "shared_ring_device"
    ring_merchant = "merch_ring_001"

    for i in range(1, 6):
        txn_id = f"ring_txn_{i:03d}"
        user_id = f"ring_user_{i:03d}"
        card_id = f"ring_card_{i:03d}"
        ip = f"10.0.0.{i}"

        print(f"  📨 Ingesting transaction {i}/5: {txn_id} (user: {user_id}, card: {card_id}, device: {ring_device})")

        await client.ingest_raw_transaction({
            "transaction_id": txn_id,
            "amount": 1000,
            "currency": "INR",
            "user_id": user_id,
            "device_id": ring_device,
            "ip_address": f"10.0.0.{i}",
            "card_fingerprint": f"ring_card_{i:03d}",
            "card_bin": "411111",
            "merchant_id": ring_merchant,
            "merchant_category": "electronics",
            "email": f"ring_user_{i:03d}@test.com",
            "phone": f"+91999999999{i}",
            "shipping_pincode": "560001",
            "avs_result": "match",
            "cvv_result": "match",
            "three_ds_result": "success",
        })

    print(f"\n🔍 Scanning graph for fraud rings (min_size=3, min_density=0.2)...")
    result = await client.detect_fraud_rings(min_size=3, min_density=0.2)
    print(json.dumps(result, indent=2, default=str))

    if result.get("rings"):
        for ring in result["rings"]:
            print(f"\n🔴 Ring detected: {ring['id']}")
            print(f"   Type: {ring['fraud_types']}")
            print(f"   Entities: {len(ring['entities'])} (1 device + 5 users + 5 cards)")
            print(f"   Edges: {ring['edge_count']}")
            print(f"   Density: {ring['density']:.2f}")
            print(f"   Risk Score: {ring['risk_score']:.2f}")
            print(f"   Fraud Types: {ring['fraud_types']}")
            print(f"   Description: {ring['description']}")
    else:
        print("\n⚠️  No rings detected — check if min_size/min_density thresholds are met")


async def main():
    print("🚀 VAJRA Demo — Simulating Razorpay Webhooks")
    print("   Make sure `python3 -m vajra` is running on localhost:8000")

    client = VajraDemoClient()

    try:
        # Health check
        health = await client.health_check()
        print(f"✅ Server healthy: {health}")

        # Run all scenarios
        await run_scenario_1_normal_transaction(client)
        await run_scenario_2_suspicious_transaction(client)
        await run_scenario_3_chargeback_defense(client)
        await run_scenario_4_fraud_ring_detection(client)

        print("\n" + "="*60)
        print("✅ ALL DEMO SCENARIOS COMPLETED")
        print("="*60)

    except httpx.ConnectError:
        print("\n❌ Cannot connect to server. Is `python3 -m vajra` running?")
    except Exception as e:
        print(f"\n❌ Error: {e}")
    finally:
        await client.close()


if __name__ == "__main__":
    asyncio.run(main())