from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from vajra.agents.tribunal import get_tribunal_engine
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
from vajra.core.causal_kg import get_fraud_graph
from vajra.core.database import close_db, init_db
from vajra.core.synthesis import (
    get_policy_executor,
    get_policy_synthesizer,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await get_fraud_graph()
    await get_tribunal_engine()
    get_policy_synthesizer()
    get_policy_executor()
    yield
    await close_db()


app = FastAPI(
    title="VAJRA - Multi-Agent Defense System for Payment Risk",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def audit_middleware(request: Request, call_next):
    response = await call_next(request)
    return response


class TransactionIngestRequest(BaseModel):
    transaction_id: str
    amount: float
    currency: str = "INR"
    user_id: str
    device_id: str
    ip_address: str
    card_fingerprint: str
    card_bin: str | None = None
    card_last4: str | None = None
    merchant_id: str
    merchant_category: str | None = None
    email: str | None = None
    phone: str | None = None
    device_fingerprint: str | None = None
    ip_geo: dict | None = None
    shipping_pincode: str | None = None
    avs_result: str | None = None
    cvv_result: str | None = None
    three_ds_result: str | None = None
    velocity_1h: int = 0
    velocity_24h: int = 0
    new_device: bool = False
    new_ip: bool = False
    geo_mismatch: bool = False


class ChargebackRequest(BaseModel):
    chargeback_id: str
    transaction_id: str
    amount: float
    currency: str = "INR"
    reason_code: str
    merchant_id: str
    customer_id: str
    shipping_proof: dict | None = None
    avs_result: str | None = None
    cvv_result: str | None = None
    three_ds_result: str | None = None
    device_fingerprint: dict | None = None
    ip_geolocation: dict | None = None
    customer_history: dict | None = None


class FraudExplainRequest(BaseModel):
    transaction_id: str
    user_id: str
    device_id: str
    ip_address: str
    card_fingerprint: str
    amount: float
    merchant_id: str
    velocity_1h: int = 0
    velocity_24h: int = 0
    new_device: bool = False
    new_ip: bool = False
    geo_mismatch: bool = False


class AuditQueryRequest(BaseModel):
    correlation_id: str | None = None
    actor_id: str | None = None
    event_type: str | None = None
    actor_type: str | None = None
    start_time: str | None = None
    end_time: str | None = None
    limit: int = 100
    offset: int = 0


@app.post("/api/v1/transactions/ingest")
async def ingest_transaction(txn: TransactionIngestRequest):
    graph = await get_fraud_graph()
    entities = await graph.ingest_transaction(txn.model_dump())

    await append_audit_event(
        AuditEventCreate(
            event_type=AuditEventType.TRANSACTION_INGESTED,
            actor_type=AuditActorType.SYSTEM,
            actor_id="transaction_ingestor",
            correlation_id=txn.transaction_id,
            payload=txn.model_dump(),
        )
    )

    return {
        "status": "ingested",
        "transaction_id": txn.transaction_id,
        "entities_created": len(entities),
    }


@app.post("/api/v1/chargebacks/defend")
async def defend_chargeback(request: ChargebackRequest):
    tribunal = await get_tribunal_engine()
    case = await tribunal.create_case(request.model_dump())

    ruling = await tribunal.run_debate(case.id)

    synthesizer = get_policy_synthesizer()
    policy = await synthesizer.synthesize(ruling, case, case.evidence)

    executor = get_policy_executor()
    results = await executor.execute(policy)

    return {
        "case_id": case.id,
        "ruling": {
            "decision": ruling.decision,
            "confidence": ruling.confidence,
            "reasoning": ruling.reasoning,
            "cost_if_wrong": ruling.cost_if_wrong,
            "recommended_action": ruling.recommended_action,
        },
        "policy": {
            "id": policy.id,
            "status": policy.status.value,
            "actions_executed": len(results),
        },
        "execution_results": [
            {"action_id": r.action_id, "status": r.status, "output": r.output, "error": r.error}
            for r in results
        ],
    }


@app.post("/api/v1/fraud/explain")
async def explain_fraud(request: FraudExplainRequest):
    graph = await get_fraud_graph()
    explanation = graph.explain_transaction(request.transaction_id, request.model_dump())
    return explanation.model_dump()


@app.post("/api/v1/fraud/detect-rings")
async def detect_fraud_rings(min_size: int = 3, min_density: float = 0.1):
    graph = await get_fraud_graph()
    rings = graph.detect_rings(min_size=min_size, min_density=min_density)

    for ring in rings:
        await graph.persist_ring(ring)
        await append_audit_event(
            AuditEventCreate(
                event_type=AuditEventType.FRAUD_RING_IDENTIFIED,
                actor_type=AuditActorType.AGENT_VAJRA,
                actor_id="fraud_vajra",
                correlation_id=ring.id,
                payload=ring.model_dump(),
            )
        )

    return {"rings_detected": len(rings), "rings": [r.model_dump() for r in rings]}


@app.post("/api/v1/audit/query")
async def audit_query(query: AuditQueryRequest):
    from datetime import datetime

    from vajra.core.audit import AuditActorType as AAT
    from vajra.core.audit import AuditEventType as AET

    aq = AuditQuery(
        correlation_id=query.correlation_id,
        actor_id=query.actor_id,
        event_type=AET(query.event_type) if query.event_type else None,
        actor_type=AAT(query.actor_type) if query.actor_type else None,
        start_time=datetime.fromisoformat(query.start_time) if query.start_time else None,
        end_time=datetime.fromisoformat(query.end_time) if query.end_time else None,
        limit=query.limit,
        offset=query.offset,
    )
    events = await query_audit_events(aq)
    return {"events": [e.model_dump() for e in events], "count": len(events)}


@app.get("/api/v1/audit/trail/{correlation_id}")
async def audit_trail(correlation_id: str):
    trail = await get_audit_trail(correlation_id)
    return trail


@app.get("/api/v1/audit/verify/{correlation_id}")
async def audit_verify(correlation_id: str):
    result = await verify_audit_integrity(correlation_id)
    return result


@app.get("/api/v1/health")
async def health():
    return {"status": "healthy", "service": "vajra"}


@app.get("/api/v1/metrics")
async def metrics():
    from fastapi.responses import Response
    from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )
