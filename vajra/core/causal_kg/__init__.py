import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

import networkx as nx
import numpy as np
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer
from sqlalchemy import JSON, Column, DateTime, Index, String, Text, select, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import AsyncSession

from vajra.config import settings
from vajra.core.audit import AuditActorType, AuditEventType, append_audit_event
from vajra.core.database import Base, get_async_session


class EntityType(str, Enum):
    USER = "user"
    DEVICE = "device"
    IP = "ip"
    CARD = "card"
    MERCHANT = "merchant"
    ADDRESS = "address"
    PHONE = "phone"
    EMAIL = "email"


class EdgeType(str, Enum):
    USED_BY = "used_by"
    SHIPPED_TO = "shipped_to"
    LOGGED_FROM = "logged_from"
    TRANSACTED_WITH = "transacted_with"
    OWNED_BY = "owned_by"
    ASSOCIATED_WITH = "associated_with"


@dataclass
class Entity:
    id: str
    type: EntityType
    properties: dict = field(default_factory=dict)
    embedding: np.ndarray | None = None
    first_seen: datetime = field(default_factory=datetime.utcnow)
    last_seen: datetime = field(default_factory=datetime.utcnow)
    risk_score: float = 0.0


@dataclass
class Edge:
    source: str
    target: str
    type: EdgeType
    timestamp: datetime = field(default_factory=datetime.utcnow)
    weight: float = 1.0
    properties: dict = field(default_factory=dict)


class FraudRing(BaseModel):
    id: str
    entities: list[str]
    edge_count: int
    density: float
    risk_score: float
    fraud_types: list[str]
    detected_at: datetime
    description: str
    evidence: dict = Field(default_factory=dict)


class CausalExplanation(BaseModel):
    transaction_id: str
    is_fraud: bool
    confidence: float
    causal_factors: list[dict]
    counterfactual: dict
    intervention: str
    subgraph: dict


class FraudGraphDB(Base):
    __tablename__ = "fraud_graph_entities"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    entity_id = Column(String(128), nullable=False, unique=True, index=True)
    entity_type = Column(String(32), nullable=False, index=True)
    properties = Column(JSON, nullable=False, default={})
    embedding = Column(Text, nullable=True)
    risk_score = Column(String(32), nullable=False, default="0.0")
    first_seen = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (Index("ix_entity_type_risk", "entity_type", "risk_score"),)


class FraudEdgeDB(Base):
    __tablename__ = "fraud_graph_edges"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    source_id = Column(String(128), nullable=False, index=True)
    target_id = Column(String(128), nullable=False, index=True)
    edge_type = Column(String(32), nullable=False)
    weight = Column(String(32), nullable=False, default="1.0")
    properties = Column(JSON, nullable=False, default={})
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_edge_source_target", "source_id", "target_id"),
        Index("ix_edge_type_time", "edge_type", "timestamp"),
        UniqueConstraint("source_id", "target_id", "edge_type", name="uq_edge_source_target_type"),
    )


class FraudRingDB(Base):
    __tablename__ = "fraud_rings"

    id = Column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    ring_id = Column(String(128), nullable=False, unique=True, index=True)
    entities = Column(JSON, nullable=False)
    edge_count = Column(String(32), nullable=False)
    density = Column(String(32), nullable=False)
    risk_score = Column(String(32), nullable=False)
    fraud_types = Column(JSON, nullable=False)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    description = Column(Text, nullable=False)
    evidence = Column(JSON, nullable=False, default={})


class CausalFraudGraph:
    def __init__(self):
        self.graph = nx.MultiDiGraph()
        self.entities: dict[str, Entity] = {}
        self.embedding_model = SentenceTransformer(settings.EMBEDDING_MODEL)
        self._entity_cache: dict[str, Entity] = {}
        self._dirty = False

    async def initialize(self):
        await self._load_from_db()

    async def _load_from_db(self):
        async with get_async_session() as session:
            result = await session.execute(select(FraudGraphDB))
            for row in result.scalars():
                entity = Entity(
                    id=row.entity_id,
                    type=EntityType(row.entity_type),
                    properties=row.properties,
                    risk_score=float(row.risk_score),
                    first_seen=row.first_seen,
                    last_seen=row.last_seen,
                )
                if row.embedding:
                    entity.embedding = np.frombuffer(bytes.fromhex(row.embedding), dtype=np.float32)
                self.entities[entity.id] = entity
                self.graph.add_node(entity.id, **entity.properties, type=entity.type.value)

            result = await session.execute(select(FraudEdgeDB))
            for row in result.scalars():
                self.graph.add_edge(
                    row.source_id,
                    row.target_id,
                    key=row.edge_type,
                    type=row.edge_type,
                    weight=float(row.weight),
                    properties=row.properties,
                    timestamp=row.timestamp,
                )

    async def upsert_entity(self, entity: Entity) -> Entity:
        existing = self.entities.get(entity.id)
        if existing:
            existing.properties.update(entity.properties)
            existing.last_seen = datetime.now(timezone.utc)
            existing.risk_score = max(existing.risk_score, entity.risk_score)
            if entity.embedding is not None:
                existing.embedding = entity.embedding
            entity = existing
        else:
            self.entities[entity.id] = entity
            self.graph.add_node(entity.id, **entity.properties, type=entity.type.value)

        await self._persist_entity(entity)
        self._dirty = True
        return entity

    async def _persist_entity(self, entity: Entity):
        async with get_async_session() as session:
            from sqlalchemy import select
            stmt = select(FraudGraphDB).where(FraudGraphDB.entity_id == entity.id)
            result = await session.execute(stmt)
            db_entity = result.scalar_one_or_none()
            embedding_hex = (
                entity.embedding.tobytes().hex() if entity.embedding is not None else None
            )
            # Convert to naive UTC for TIMESTAMP WITHOUT TIME ZONE column
            def to_naive(dt):
                if dt.tzinfo is not None:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt

            if db_entity:
                db_entity.properties = entity.properties
                db_entity.risk_score = str(entity.risk_score)
                db_entity.last_seen = to_naive(entity.last_seen)
                db_entity.embedding = embedding_hex
            else:
                db_entity = FraudGraphDB(
                    entity_id=entity.id,
                    entity_type=entity.type.value,
                    properties=entity.properties,
                    risk_score=str(entity.risk_score),
                    first_seen=to_naive(entity.first_seen),
                    last_seen=to_naive(entity.last_seen),
                    embedding=embedding_hex,
                )
                session.add(db_entity)
            await session.commit()

    async def upsert_edge(self, edge: Edge):
        self.graph.add_edge(
            edge.source,
            edge.target,
            key=edge.type.value,
            type=edge.type.value,
            weight=edge.weight,
            properties=edge.properties,
            timestamp=edge.timestamp,
        )

        async with get_async_session() as session:
            from sqlalchemy.dialects.postgresql import insert

            def to_naive(dt):
                if dt.tzinfo is not None:
                    return dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt

            stmt = insert(FraudEdgeDB).values(
                source_id=edge.source,
                target_id=edge.target,
                edge_type=edge.type.value,
                weight=str(edge.weight),
                properties=edge.properties,
                timestamp=to_naive(edge.timestamp),
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["source_id", "target_id", "edge_type"],
                set_=dict(
                    weight=str(edge.weight), properties=edge.properties, timestamp=to_naive(edge.timestamp)
                ),
            )
            await session.execute(stmt)
            await session.commit()

    async def ingest_transaction(self, txn: dict) -> list[Entity]:
        entities = []

        user = Entity(
            id=txn["user_id"],
            type=EntityType.USER,
            properties={"email": txn.get("email"), "phone": txn.get("phone")},
        )
        entities.append(user)

        device = Entity(
            id=txn["device_id"],
            type=EntityType.DEVICE,
            properties={"fingerprint": txn.get("device_fingerprint")},
        )
        entities.append(device)

        ip = Entity(
            id=txn["ip_address"],
            type=EntityType.IP,
            properties={"geo": txn.get("ip_geo")},
        )
        entities.append(ip)

        card = Entity(
            id=txn["card_fingerprint"],
            type=EntityType.CARD,
            properties={"bin": txn.get("card_bin"), "last4": txn.get("card_last4")},
        )
        entities.append(card)

        merchant = Entity(
            id=txn["merchant_id"],
            type=EntityType.MERCHANT,
            properties={"category": txn.get("merchant_category")},
        )
        entities.append(merchant)

        for entity in entities:
            await self.upsert_entity(entity)

        await self.upsert_edge(
            Edge(
                source=txn["user_id"],
                target=txn["device_id"],
                type=EdgeType.USED_BY,
                properties={"transaction_id": txn["transaction_id"]},
            )
        )

        await self.upsert_edge(
            Edge(
                source=txn["user_id"],
                target=txn["ip_address"],
                type=EdgeType.LOGGED_FROM,
                properties={"transaction_id": txn["transaction_id"]},
            )
        )

        await self.upsert_edge(
            Edge(
                source=txn["user_id"],
                target=txn["card_fingerprint"],
                type=EdgeType.OWNED_BY,
                properties={"transaction_id": txn["transaction_id"]},
            )
        )

        await self.upsert_edge(
            Edge(
                source=txn["user_id"],
                target=txn["merchant_id"],
                type=EdgeType.TRANSACTED_WITH,
                properties={"transaction_id": txn["transaction_id"], "amount": txn["amount"]},
            )
        )

        if txn.get("shipping_pincode"):
            addr = Entity(
                id=f"addr_{txn['shipping_pincode']}",
                type=EntityType.ADDRESS,
                properties={"pincode": txn["shipping_pincode"]},
            )
            await self.upsert_entity(addr)
            await self.upsert_edge(
                Edge(
                    source=txn["user_id"],
                    target=addr.id,
                    type=EdgeType.SHIPPED_TO,
                    properties={"transaction_id": txn["transaction_id"]},
                )
            )

        return entities

    def detect_rings(self, min_size: int = 3, min_density: float = 0.3) -> list[FraudRing]:
        rings = []
        undirected = self.graph.to_undirected()

        for component in nx.connected_components(undirected):
            if len(component) < min_size:
                continue

            subgraph = undirected.subgraph(component)
            density = nx.density(subgraph)
            if density < min_density:
                continue

            entity_ids = list(component)
            entity_types = [self.entities[eid].type for eid in entity_ids if eid in self.entities]

            fraud_types = self._infer_fraud_types(entity_ids, subgraph)
            risk_score = self._calculate_ring_risk(entity_ids, subgraph, fraud_types)

            if risk_score > 0.5:
                ring = FraudRing(
                    id=f"ring_{uuid4().hex[:12]}",
                    entities=entity_ids,
                    edge_count=subgraph.number_of_edges(),
                    density=density,
                    risk_score=risk_score,
                    fraud_types=fraud_types,
                    detected_at=datetime.now(timezone.utc),
                    description=self._generate_ring_description(entity_ids, fraud_types),
                    evidence=self._collect_ring_evidence(entity_ids),
                )
                rings.append(ring)

        return rings

    def _infer_fraud_types(self, entity_ids: list[str], subgraph: nx.Graph) -> list[str]:
        types = []
        user_count = sum(
            1
            for eid in entity_ids
            if self.entities.get(eid, Entity(id="", type=EntityType.USER)).type == EntityType.USER
        )
        device_count = sum(
            1
            for eid in entity_ids
            if self.entities.get(eid, Entity(id="", type=EntityType.USER)).type == EntityType.DEVICE
        )
        card_count = sum(
            1
            for eid in entity_ids
            if self.entities.get(eid, Entity(id="", type=EntityType.USER)).type == EntityType.CARD
        )
        ip_count = sum(
            1
            for eid in entity_ids
            if self.entities.get(eid, Entity(id="", type=EntityType.USER)).type == EntityType.IP
        )

        if device_count == 1 and card_count > 3:
            types.append("card_testing")
        if user_count > 1 and device_count == 1:
            types.append("account_takeover")
        if card_count > 1 and ip_count == 1:
            types.append("identity_theft")
        if len(entity_ids) > 10 and density > 0.5:
            types.append("organized_ring")

        return types or ["suspicious_cluster"]

    def _calculate_ring_risk(
        self, entity_ids: list[str], subgraph: nx.Graph, fraud_types: list[str]
    ) -> float:
        base_score = 0.0
        for eid in entity_ids:
            entity = self.entities.get(eid)
            if entity:
                base_score += entity.risk_score
        base_score /= max(len(entity_ids), 1)

        type_multiplier = {
            "card_testing": 1.5,
            "account_takeover": 1.8,
            "identity_theft": 1.6,
            "organized_ring": 2.0,
        }
        multiplier = max([type_multiplier.get(t, 1.0) for t in fraud_types], default=1.0)

        density_bonus = min(subgraph.number_of_edges() / max(len(entity_ids), 1) * 0.1, 0.3)

        return min(base_score * multiplier + density_bonus, 1.0)

    def _generate_ring_description(self, entity_ids: list[str], fraud_types: list[str]) -> str:
        type_counts = defaultdict(int)
        for eid in entity_ids:
            entity = self.entities.get(eid)
            if entity:
                type_counts[entity.type.value] += 1
        parts = [f"{count} {etype}(s)" for etype, count in type_counts.items()]
        return f"Fraud ring ({', '.join(fraud_types)}) with {', '.join(parts)}"

    def _collect_ring_evidence(self, entity_ids: list[str]) -> dict:
        evidence = {"entities": {}, "edges": []}
        for eid in entity_ids:
            entity = self.entities.get(eid)
            if entity:
                evidence["entities"][eid] = {
                    "type": entity.type.value,
                    "risk_score": entity.risk_score,
                    "properties": entity.properties,
                }
        for source, target, key, data in self.graph.edges(entity_ids, keys=True, data=True):
            if source in entity_ids and target in entity_ids:
                evidence["edges"].append(
                    {
                        "source": source,
                        "target": target,
                        "type": data.get("type"),
                        "weight": data.get("weight"),
                        "timestamp": data.get("timestamp", "").isoformat()
                        if hasattr(data.get("timestamp"), "isoformat")
                        else str(data.get("timestamp")),
                    }
                )
        return evidence

    async def persist_ring(self, ring: FraudRing):
        async with get_async_session() as session:
            db_ring = FraudRingDB(
                ring_id=ring.id,
                entities=ring.entities,
                edge_count=str(ring.edge_count),
                density=str(ring.density),
                risk_score=str(ring.risk_score),
                fraud_types=ring.fraud_types,
                detected_at=ring.detected_at,
                description=ring.description,
                evidence=ring.evidence,
            )
            session.add(db_ring)
            await session.commit()

        for eid in ring.entities:
            entity = self.entities.get(eid)
            if entity:
                entity.risk_score = max(entity.risk_score, ring.risk_score * 0.8)
                await self._persist_entity(entity)

    def explain_transaction(self, transaction_id: str, txn_data: dict) -> CausalExplanation:
        user_id = txn_data.get("user_id")
        device_id = txn_data.get("device_id")
        ip = txn_data.get("ip_address")
        card = txn_data.get("card_fingerprint")

        causal_factors = []
        subgraph_entities = set()
        subgraph_edges = []

        for entity_id in [user_id, device_id, ip, card]:
            if entity_id and entity_id in self.entities:
                entity = self.entities[entity_id]
                subgraph_entities.add(entity_id)
                if entity.risk_score > 0.3:
                    causal_factors.append(
                        {
                            "entity": entity_id,
                            "type": entity.type.value,
                            "risk_score": entity.risk_score,
                            "reason": f"Entity has elevated risk score: {entity.risk_score:.2f}",
                        }
                    )

                neighbors = list(self.graph.neighbors(entity_id))
                for neighbor in neighbors[:10]:
                    subgraph_entities.add(neighbor)
                    edge_data = self.graph.get_edge_data(entity_id, neighbor)
                    if edge_data:
                        for key, data in edge_data.items():
                            subgraph_edges.append(
                                {
                                    "source": entity_id,
                                    "target": neighbor,
                                    "type": data.get("type"),
                                    "weight": data.get("weight"),
                                }
                            )

        rings = self.detect_rings()
        for ring in rings:
            if any(e in ring.entities for e in [user_id, device_id, ip, card]):
                causal_factors.append(
                    {
                        "entity": ring.id,
                        "type": "fraud_ring",
                        "risk_score": ring.risk_score,
                        "reason": f"Entity participates in detected fraud ring: {ring.description}",
                    }
                )
                subgraph_entities.update(ring.entities)

        counterfactual = self._compute_counterfactual(txn_data, causal_factors)
        is_fraud = counterfactual["fraud_probability"] > 0.5
        confidence = abs(counterfactual["fraud_probability"] - 0.5) * 2

        intervention = self._recommend_intervention(is_fraud, causal_factors, counterfactual)

        return CausalExplanation(
            transaction_id=transaction_id,
            is_fraud=is_fraud,
            confidence=confidence,
            causal_factors=causal_factors,
            counterfactual=counterfactual,
            intervention=intervention,
            subgraph={
                "entities": list(subgraph_entities),
                "edges": subgraph_edges,
            },
        )

    def _compute_counterfactual(self, txn_data: dict, causal_factors: list[dict]) -> dict:
        base_fraud_prob = 0.05

        for factor in causal_factors:
            if factor["type"] == "fraud_ring":
                base_fraud_prob += 0.4
            elif factor["risk_score"] > 0.7:
                base_fraud_prob += 0.2
            elif factor["risk_score"] > 0.4:
                base_fraud_prob += 0.1

        if txn_data.get("new_device"):
            base_fraud_prob += 0.15
        if txn_data.get("new_ip"):
            base_fraud_prob += 0.1
        if txn_data.get("geo_mismatch"):
            base_fraud_prob += 0.2
        if txn_data.get("velocity_1h", 0) > 5:
            base_fraud_prob += 0.15

        cf_without_device = base_fraud_prob
        if any(f["entity"] == txn_data.get("device_id") for f in causal_factors):
            cf_without_device = max(0.05, base_fraud_prob - 0.25)

        cf_without_ip = base_fraud_prob
        if any(f["entity"] == txn_data.get("ip_address") for f in causal_factors):
            cf_without_ip = max(0.05, base_fraud_prob - 0.15)

        return {
            "fraud_probability": min(base_fraud_prob, 0.95),
            "without_device": cf_without_device,
            "without_ip": cf_without_ip,
            "base_rate": 0.05,
        }

    def _recommend_intervention(
        self, is_fraud: bool, causal_factors: list[dict], counterfactual: dict
    ) -> str:
        if not is_fraud:
            return "APPROVE: Transaction appears legitimate"

        has_ring = any(f["type"] == "fraud_ring" for f in causal_factors)
        high_risk_entity = any(f["risk_score"] > 0.7 for f in causal_factors)

        if has_ring:
            return "BLOCK: Entity participates in known fraud ring. Immediate block and ring investigation."
        if high_risk_entity:
            return "CHALLENGE: High-risk entity detected. Require step-up authentication (3DS/OTP)."
        if counterfactual["without_device"] < 0.2:
            return "CHALLENGE: Device is primary risk factor. Require device verification."
        return "REVIEW: Elevated risk. Queue for manual review with causal explanation."


_fraud_graph: CausalFraudGraph | None = None


async def get_fraud_graph() -> CausalFraudGraph:
    global _fraud_graph
    if _fraud_graph is None:
        _fraud_graph = CausalFraudGraph()
        await _fraud_graph.initialize()
    return _fraud_graph
