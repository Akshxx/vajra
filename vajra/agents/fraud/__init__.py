import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from vajra.config import settings
from vajra.core.audit import AuditActorType, AuditEventType, append_audit_event
from vajra.core.causal_kg import CausalExplanation, FraudRing, get_fraud_graph


@dataclass
class FraudAlert:
    id: str
    transaction_id: str
    fraud_probability: float
    confidence: float
    causal_factors: list[dict]
    intervention: str
    detected_at: datetime
    status: str = "new"


class FraudSentinelAgent:
    def __init__(self):
        self.graph = None
        self.alerts: dict[str, FraudAlert] = {}
        self._running = False

    async def initialize(self):
        self.graph = await get_fraud_graph()

    async def process_transaction(self, txn: dict) -> FraudAlert | None:
        explanation = self.graph.explain_transaction(txn["transaction_id"], txn)

        if explanation.is_fraud and explanation.confidence > 0.6:
            alert = FraudAlert(
                id=f"alert_{uuid4().hex[:12]}",
                transaction_id=txn["transaction_id"],
                fraud_probability=explanation.counterfactual.get("fraud_probability", 0.0),
                confidence=explanation.confidence,
                causal_factors=explanation.causal_factors,
                intervention=explanation.intervention,
                detected_at=datetime.now(timezone.utc),
            )
            self.alerts[alert.id] = alert

            await append_audit_event(
                {
                    "event_type": AuditEventType.FRAUD_DETECTED,
                    "actor_type": AuditActorType.AGENT_VAJRA,
                    "actor_id": "fraud_vajra",
                    "correlation_id": txn["transaction_id"],
                    "payload": {
                        "alert_id": alert.id,
                        "transaction_id": alert.transaction_id,
                        "fraud_probability": alert.fraud_probability,
                        "confidence": alert.confidence,
                        "intervention": alert.intervention,
                    },
                }
            )

            return alert

        return None

    async def scan_for_rings(self, min_size: int = 3, min_density: float = 0.3) -> list[FraudRing]:
        rings = self.graph.detect_rings(min_size=min_size, min_density=min_density)

        for ring in rings:
            await self.graph.persist_ring(ring)
            await append_audit_event(
                {
                    "event_type": AuditEventType.FRAUD_RING_IDENTIFIED,
                    "actor_type": AuditActorType.AGENT_VAJRA,
                    "actor_id": "fraud_vajra",
                    "correlation_id": ring.id,
                    "payload": ring.model_dump(),
                }
            )

        return rings

    async def start_streaming(self, kafka_consumer):
        self._running = True
        async for message in kafka_consumer:
            if not self._running:
                break
            try:
                txn = message.value
                await self.process_transaction(txn)
            except Exception as e:
                print(f"Error processing transaction: {e}")

    def stop_streaming(self):
        self._running = False

    def get_alert(self, alert_id: str) -> FraudAlert | None:
        return self.alerts.get(alert_id)

    def get_recent_alerts(self, limit: int = 100) -> list[FraudAlert]:
        sorted_alerts = sorted(self.alerts.values(), key=lambda a: a.detected_at, reverse=True)
        return sorted_alerts[:limit]


_fraud_vajra: FraudSentinelAgent | None = None


async def get_fraud_vajra() -> FraudSentinelAgent:
    global _fraud_vajra
    if _fraud_vajra is None:
        _fraud_vajra = FraudSentinelAgent()
        await _fraud_vajra.initialize()
    return _fraud_vajra
