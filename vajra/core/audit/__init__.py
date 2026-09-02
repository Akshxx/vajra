import hashlib
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field
from sqlalchemy import (
    BIGINT,
    JSON,
    Column,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from vajra.config import settings
from vajra.core.database import Base, get_async_session


class AuditEventType(str, Enum):
    TRANSACTION_INGESTED = "transaction_ingested"
    CHARGEBACK_RECEIVED = "chargeback_received"
    TRIBUNAL_DEBATE_STARTED = "tribunal_debate_started"
    TRIBUNAL_ARGUMENT_SUBMITTED = "tribunal_argument_submitted"
    TRIBUNAL_RULING = "tribunal_ruling"
    POLICY_SYNTHESIZED = "policy_synthesized"
    POLICY_EXECUTED = "policy_executed"
    POLICY_ESCALATED = "policy_escalated"
    FRAUD_DETECTED = "fraud_detected"
    FRAUD_RING_IDENTIFIED = "fraud_ring_identified"
    EVIDENCE_COLLECTED = "evidence_collected"
    EVIDENCE_SUBMITTED = "evidence_submitted"
    HUMAN_REVIEW_REQUESTED = "human_review_requested"
    DECISION_OVERRIDDEN = "decision_overridden"


class AuditActorType(str, Enum):
    SYSTEM = "system"
    AGENT_PROSECUTOR = "agent_prosecutor"
    AGENT_DEFENSE = "agent_defense"
    AGENT_JUDGE = "agent_judge"
    AGENT_VAJRA = "agent_vajra"
    AGENT_EXECUTOR = "agent_executor"
    HUMAN_ANALYST = "human_analyst"
    HUMAN_APPROVER = "human_approver"
    EXTERNAL_API = "external_api"


@dataclass
class MerkleNode:
    hash: str
    left: Optional["MerkleNode"] = None
    right: Optional["MerkleNode"] = None
    data: dict | None = None
    index: int = 0


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    event_type = Column(String(64), nullable=False, index=True)
    actor_type = Column(String(32), nullable=False, index=True)
    actor_id = Column(String(128), nullable=False, index=True)
    correlation_id = Column(String(128), nullable=False, index=True)
    causation_id = Column(String(128), nullable=True, index=True)
    payload = Column(JSON, nullable=False)
    event_metadata = Column("metadata", JSON, nullable=True, default={})
    merkle_root = Column(String(64), nullable=False)
    merkle_proof = Column(JSON, nullable=True)
    sequence_number = Column(BIGINT, nullable=False, unique=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_audit_correlation_created", "correlation_id", "created_at"),
        Index("ix_audit_actor_created", "actor_id", "created_at"),
        UniqueConstraint("sequence_number", name="uq_audit_sequence"),
    )


class AuditEventCreate(BaseModel):
    event_type: AuditEventType
    actor_type: AuditActorType
    actor_id: str
    correlation_id: str
    causation_id: str | None = None
    payload: dict
    event_metadata: dict = Field(default_factory=dict)


class AuditEventResponse(BaseModel):
    id: UUID
    event_type: AuditEventType
    actor_type: AuditActorType
    actor_id: str
    correlation_id: str
    causation_id: str | None
    payload: dict
    event_metadata: dict
    merkle_root: str
    merkle_proof: list | None
    sequence_number: int
    created_at: datetime

    class Config:
        from_attributes = True


class AuditQuery(BaseModel):
    correlation_id: str | None = None
    actor_id: str | None = None
    event_type: AuditEventType | None = None
    actor_type: AuditActorType | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    limit: int = 100
    offset: int = 0


class MerkleTree:
    def __init__(self, depth: int = 20):
        self.depth = depth
        self.max_leaves = 2**depth
        self.leaves: list[MerkleNode] = []
        self._sequence = 0
        self._initialized = False

    async def initialize(self):
        if self._initialized:
            return
        from vajra.core.database import get_async_session
        from sqlalchemy import select, func
        async with get_async_session() as session:
            result = await session.execute(select(func.max(AuditEvent.sequence_number)))
            max_seq = result.scalar()
            self._sequence = (max_seq or 0) + 1
        self._initialized = True

    def _hash(self, data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    def _hash_node(self, node: MerkleNode) -> str:
        if node.data is not None:
            return self._hash(json.dumps(node.data, sort_keys=True).encode())
        left_hash = node.left.hash if node.left else self._hash(b"0")
        right_hash = node.right.hash if node.right else self._hash(b"0")
        return self._hash((left_hash + right_hash).encode())

    def add_leaf(self, data: dict) -> tuple[str, list[dict]]:
        leaf = MerkleNode(
            hash="",
            data=data,
            index=self._sequence,
        )
        leaf.hash = self._hash_node(leaf)
        self.leaves.append(leaf)

        proof = self._generate_proof(self._sequence)
        root = self._compute_root()
        self._sequence += 1
        return root, proof

    def _generate_proof(self, index: int) -> list[dict]:
        proof = []
        level_nodes = self.leaves[:]
        current_index = index

        while len(level_nodes) > 1:
            next_level = []
            for i in range(0, len(level_nodes), 2):
                left = level_nodes[i]
                right = level_nodes[i + 1] if i + 1 < len(level_nodes) else None

                parent = MerkleNode(
                    hash="",
                    left=left,
                    right=right,
                )
                parent.hash = self._hash_node(parent)
                next_level.append(parent)

                if left.index == current_index or (right and right.index == current_index):
                    sibling = right if left.index == current_index else left
                    proof.append(
                        {
                            "position": "right" if left.index == current_index else "left",
                            "hash": sibling.hash if sibling else self._hash(b"0"),
                        }
                    )
                    current_index = len(next_level) - 1

            level_nodes = next_level

        return proof

    def _compute_root(self) -> str:
        if not self.leaves:
            return self._hash(b"empty")
        level_nodes = self.leaves[:]
        while len(level_nodes) > 1:
            next_level = []
            for i in range(0, len(level_nodes), 2):
                left = level_nodes[i]
                right = level_nodes[i + 1] if i + 1 < len(level_nodes) else None
                parent = MerkleNode(hash="", left=left, right=right)
                parent.hash = self._hash_node(parent)
                next_level.append(parent)
            level_nodes = next_level
        return level_nodes[0].hash

    def verify_proof(self, data: dict, proof: list[dict], root: str) -> bool:
        current_hash = self._hash(json.dumps(data, sort_keys=True).encode())
        for step in proof:
            sibling_hash = step["hash"]
            if step["position"] == "right":
                current_hash = self._hash((current_hash + sibling_hash).encode())
            else:
                current_hash = self._hash((sibling_hash + current_hash).encode())
        return current_hash == root


_merkle_tree: MerkleTree | None = None


def get_merkle_tree() -> MerkleTree:
    global _merkle_tree
    if _merkle_tree is None:
        _merkle_tree = MerkleTree(depth=settings.AUDIT_MERKLE_TREE_DEPTH)
    return _merkle_tree


async def append_audit_event(event: AuditEventCreate) -> AuditEventResponse:
    merkle_tree = get_merkle_tree()
    await merkle_tree.initialize()
    merkle_root, merkle_proof = merkle_tree.add_leaf(event.payload)

    async with get_async_session() as session:
        db_event = AuditEvent(
            event_type=event.event_type.value,
            actor_type=event.actor_type.value,
            actor_id=event.actor_id,
            correlation_id=event.correlation_id,
            causation_id=event.causation_id,
            payload=event.payload,
            event_metadata=event.event_metadata,
            merkle_root=merkle_root,
            merkle_proof=merkle_proof,
            sequence_number=merkle_tree._sequence,
        )
        session.add(db_event)
        await session.commit()
        await session.refresh(db_event)

        return AuditEventResponse.model_validate(db_event)


async def query_audit_events(query: AuditQuery) -> list[AuditEventResponse]:
    async with get_async_session() as session:
        stmt = select(AuditEvent).order_by(AuditEvent.created_at.desc())

        if query.correlation_id:
            stmt = stmt.where(AuditEvent.correlation_id == query.correlation_id)
        if query.actor_id:
            stmt = stmt.where(AuditEvent.actor_id == query.actor_id)
        if query.event_type:
            stmt = stmt.where(AuditEvent.event_type == query.event_type.value)
        if query.actor_type:
            stmt = stmt.where(AuditEvent.actor_type == query.actor_type.value)
        if query.start_time:
            stmt = stmt.where(AuditEvent.created_at >= query.start_time)
        if query.end_time:
            stmt = stmt.where(AuditEvent.created_at <= query.end_time)

        stmt = stmt.limit(query.limit).offset(query.offset)
        result = await session.execute(stmt)
        events = result.scalars().all()
        return [AuditEventResponse.model_validate(e) for e in events]


async def verify_audit_integrity(correlation_id: str) -> dict:
    events = await query_audit_events(AuditQuery(correlation_id=correlation_id, limit=1000))
    merkle_tree = get_merkle_tree()

    results = []
    for event in events:
        valid = merkle_tree.verify_proof(
            event.payload,
            event.merkle_proof or [],
            event.merkle_root,
        )
        results.append(
            {
                "event_id": str(event.id),
                "event_type": event.event_type,
                "valid": valid,
                "sequence": event.sequence_number,
            }
        )

    all_valid = all(r["valid"] for r in results)
    return {
        "correlation_id": correlation_id,
        "total_events": len(results),
        "all_valid": all_valid,
        "details": results,
    }


async def get_audit_trail(correlation_id: str) -> dict:
    events = await query_audit_events(AuditQuery(correlation_id=correlation_id, limit=1000))
    integrity = await verify_audit_integrity(correlation_id)

    return {
        "correlation_id": correlation_id,
        "event_count": len(events),
        "integrity_verified": integrity["all_valid"],
        "timeline": [
            {
                "sequence": e.sequence_number,
                "timestamp": e.created_at.isoformat(),
                "event_type": e.event_type,
                "actor": f"{e.actor_type}:{e.actor_id}",
                "summary": _summarize_event(e),
            }
            for e in sorted(events, key=lambda x: x.sequence_number)
        ],
    }


def _summarize_event(event: AuditEventResponse) -> str:
    payload = event.payload
    et = event.event_type
    if et == AuditEventType.TRANSACTION_INGESTED:
        return f"Transaction {payload.get('transaction_id')} ingested: ₹{payload.get('amount')}"
    if et == AuditEventType.CHARGEBACK_RECEIVED:
        return f"Chargeback {payload.get('chargeback_id')} received for txn {payload.get('transaction_id')}"
    if et == AuditEventType.TRIBUNAL_RULING:
        return (
            f"Tribunal ruling: {payload.get('decision')} (confidence: {payload.get('confidence')})"
        )
    if et == AuditEventType.POLICY_EXECUTED:
        return f"Policy {payload.get('policy_id')} executed: {payload.get('status')}"
    if et == AuditEventType.FRAUD_DETECTED:
        return f"Fraud detected: {payload.get('fraud_type')} (score: {payload.get('score')})"
    if et == AuditEventType.FRAUD_RING_IDENTIFIED:
        return f"Fraud ring identified: {payload.get('ring_id')} ({payload.get('entity_count')} entities)"
    return f"{et.value}: {list(payload.keys())[:3]}"
