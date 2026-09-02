import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from vajra.config import settings
from vajra.core.audit import AuditActorType, AuditEventCreate, AuditEventType, append_audit_event


class ArgumentRole(str, Enum):
    PROSECUTOR = "prosecutor"
    DEFENSE = "defense"
    JUDGE = "judge"


class ArgumentType(str, Enum):
    OPENING = "opening"
    REBUTTAL = "rebuttal"
    CLOSING = "closing"
    RULING = "ruling"


class EvidenceType(str, Enum):
    SHIPPING_PROOF = "shipping_proof"
    AVS_MATCH = "avs_match"
    CVV_MATCH = "cvv_match"
    THREE_DS = "three_ds"
    DEVICE_FINGERPRINT = "device_fingerprint"
    IP_GEOLOCATION = "ip_geolocation"
    CUSTOMER_HISTORY = "customer_history"
    MERCHANT_REPUTATION = "merchant_reputation"
    POLICE_REPORT = "police_report"
    CUSTOMER_COMMUNICATION = "customer_communication"
    REFUND_PROOF = "refund_proof"
    DELIVERY_CONFIRMATION = "delivery_confirmation"
    DIGITAL_GOODS_ACCESS = "digital_goods_access"
    SUBSCRIPTION_USAGE = "subscription_usage"


@dataclass
class Evidence:
    id: str
    type: EvidenceType
    description: str
    source: str
    strength: float
    metadata: dict = field(default_factory=dict)
    verified: bool = False
    citation_url: str | None = None


@dataclass
class Argument:
    id: str
    role: ArgumentRole
    type: ArgumentType
    content: str
    evidence_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0
    round_number: int = 1
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TribunalCase:
    id: str
    chargeback_id: str
    transaction_id: str
    amount: float
    currency: str
    reason_code: str
    merchant_id: str
    customer_id: str
    evidence: dict[str, Evidence] = field(default_factory=dict)
    arguments: list[Argument] = field(default_factory=list)
    ruling: Optional["TribunalRuling"] = None
    current_round: int = 1
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class TribunalRuling:
    decision: str
    confidence: float
    reasoning: str
    evidence_summary: dict
    cost_if_wrong: float
    recommended_action: str
    deadline: datetime | None = None


class TribunalEngine:
    def __init__(self):
        self.cases: dict[str, TribunalCase] = {}
        self._llm_client = None

    async def initialize(self):
        pass

    def _get_llm_client(self):
        if self._llm_client is None:
            from vajra.core.llm import get_llm_client

            self._llm_client = get_llm_client()
        return self._llm_client

    async def create_case(self, chargeback_data: dict) -> TribunalCase:
        case = TribunalCase(
            id=f"trib_{uuid4().hex[:12]}",
            chargeback_id=chargeback_data["chargeback_id"],
            transaction_id=chargeback_data["transaction_id"],
            amount=chargeback_data["amount"],
            currency=chargeback_data["currency"],
            reason_code=chargeback_data["reason_code"],
            merchant_id=chargeback_data["merchant_id"],
            customer_id=chargeback_data["customer_id"],
        )

        await self._load_evidence(case, chargeback_data)
        self.cases[case.id] = case

        await append_audit_event(
            AuditEventCreate(
                event_type=AuditEventType.TRIBUNAL_DEBATE_STARTED,
                actor_type=AuditActorType.SYSTEM,
                actor_id="tribunal_engine",
                correlation_id=case.chargeback_id,
                payload={"case_id": case.id, "chargeback_id": case.chargeback_id},
            )
        )

        return case

    async def _load_evidence(self, case: TribunalCase, data: dict):
        evidence_map = {
            "shipping_proof": EvidenceType.SHIPPING_PROOF,
            "avs_result": EvidenceType.AVS_MATCH,
            "cvv_result": EvidenceType.CVV_MATCH,
            "three_ds_result": EvidenceType.THREE_DS,
            "device_fingerprint": EvidenceType.DEVICE_FINGERPRINT,
            "ip_geolocation": EvidenceType.IP_GEOLOCATION,
            "customer_history": EvidenceType.CUSTOMER_HISTORY,
        }

        for key, ev_type in evidence_map.items():
            if key in data and data[key]:
                strength = self._calculate_evidence_strength(ev_type, data[key])
                evidence = Evidence(
                    id=f"ev_{uuid4().hex[:8]}",
                    type=ev_type,
                    description=self._describe_evidence(ev_type, data[key]),
                    source="merchant_data",
                    strength=strength,
                    metadata={"raw": data[key]},
                )
                case.evidence[evidence.id] = evidence

    def _calculate_evidence_strength(self, ev_type: EvidenceType, value: Any) -> float:
        if isinstance(value, dict):
            match_val = value.get("match")
            result_val = value.get("result")
            status_val = value.get("status") or value.get("delivered")
            prev_orders = value.get("previous_orders", 0)
        else:
            match_val = value
            result_val = value
            status_val = value
            prev_orders = 0

        strengths = {
            EvidenceType.SHIPPING_PROOF: 0.9 if status_val else 0.3,
            EvidenceType.AVS_MATCH: {
                "match": 0.8,
                "partial": 0.4,
                "mismatch": 0.1,
                "unavailable": 0.0,
            }.get(match_val, 0.0),
            EvidenceType.CVV_MATCH: {"match": 0.7, "mismatch": 0.1, "unavailable": 0.0}.get(
                match_val, 0.0
            ),
            EvidenceType.THREE_DS: {
                "success": 0.85,
                "attempted": 0.4,
                "failed": 0.1,
                "unavailable": 0.0,
            }.get(result_val, 0.0),
            EvidenceType.DEVICE_FINGERPRINT: 0.6 if match_val else 0.2,
            EvidenceType.IP_GEOLOCATION: 0.5 if match_val else 0.1,
            EvidenceType.CUSTOMER_HISTORY: min(0.3 + prev_orders * 0.02, 0.6),
        }
        return strengths.get(ev_type, 0.3)

    def _describe_evidence(self, ev_type: EvidenceType, value: Any) -> str:
        if isinstance(value, dict):
            tracking = value.get('tracking_number', 'N/A')
            delivered = value.get('delivered', False)
            match_val = value.get('match', False)
            prev_orders = value.get('previous_orders', 0)
            prev_chargebacks = value.get('previous_chargebacks', 0)
        else:
            tracking = 'N/A'
            delivered = False
            match_val = value
            prev_orders = 0
            prev_chargebacks = 0

        descriptions = {
            EvidenceType.SHIPPING_PROOF: f"Shipping tracking: {tracking} - {'Delivered' if delivered else 'In transit'}",
            EvidenceType.AVS_MATCH: f"AVS result: {match_val}",
            EvidenceType.CVV_MATCH: f"CVV result: {match_val}",
            EvidenceType.THREE_DS: f"3DS result: {value}",
            EvidenceType.DEVICE_FINGERPRINT: f"Device fingerprint: {'Match' if match_val else 'Mismatch'}",
            EvidenceType.IP_GEOLOCATION: f"IP geolocation: {'Match billing' if match_val else 'Mismatch'}",
            EvidenceType.CUSTOMER_HISTORY: f"Customer history: {prev_orders} previous orders, {prev_chargebacks} chargebacks",
        }
        return descriptions.get(ev_type, str(value))

    async def run_debate(self, case_id: str) -> TribunalRuling:
        case = self.cases.get(case_id)
        if not case:
            raise ValueError(f"Case {case_id} not found")

        prosecutor_args = await self._prosecutor_opening(case)
        case.arguments.extend(prosecutor_args)

        defense_args = await self._defense_opening(case, prosecutor_args)
        case.arguments.extend(defense_args)

        for round_num in range(2, settings.TRIBUNAL_MAX_ROUNDS + 1):
            case.current_round = round_num
            prosecutor_rebuttal = await self._prosecutor_rebuttal(case, defense_args)
            case.arguments.extend(prosecutor_rebuttal)

            defense_rebuttal = await self._defense_rebuttal(case, prosecutor_rebuttal)
            case.arguments.extend(defense_rebuttal)

            if await self._should_conclude(case):
                break

        ruling = await self._judge_ruling(case)
        case.ruling = ruling
        case.status = "ruled"
        case.updated_at = datetime.now(timezone.utc)

        await append_audit_event(
            AuditEventCreate(
                event_type=AuditEventType.TRIBUNAL_RULING,
                actor_type=AuditActorType.AGENT_JUDGE,
                actor_id="judge_agent",
                correlation_id=case.chargeback_id,
                payload={
                    "case_id": case.id,
                    "decision": ruling.decision,
                    "confidence": ruling.confidence,
                    "cost_if_wrong": ruling.cost_if_wrong,
                },
            )
        )

        return ruling

    async def _prosecutor_opening(self, case: TribunalCase) -> list[Argument]:
        evidence_summary = self._format_evidence_for_llm(case.evidence)
        prompt = f"""You are the PROSECUTOR in a chargeback tribunal. Build the strongest case for the MERCHANT.

CHARGEBACK DETAILS:
- Chargeback ID: {case.chargeback_id}
- Transaction ID: {case.transaction_id}
- Amount: {case.amount} {case.currency}
- Reason Code: {case.reason_code}
- Merchant: {case.merchant_id}
- Customer: {case.customer_id}

AVAILABLE EVIDENCE:
{evidence_summary}

Your task: Present an OPENING ARGUMENT citing specific evidence by ID. Focus on facts that prove the transaction was legitimate and the customer received value. Be concise, factual, and reference evidence IDs."""

        response = await self._call_llm(prompt, "prosecutor_opening")
        return self._parse_arguments(response, ArgumentRole.PROSECUTOR, ArgumentType.OPENING, 1)

    async def _defense_opening(
        self, case: TribunalCase, prosecutor_args: list[Argument]
    ) -> list[Argument]:
        evidence_summary = self._format_evidence_for_llm(case.evidence)
        prosecutor_summary = "\n".join([f"- {a.content[:200]}" for a in prosecutor_args])

        prompt = f"""You are the DEFENSE in a chargeback tribunal. Build the strongest case for the CARDHOLDER.

CHARGEBACK DETAILS:
- Chargeback ID: {case.chargeback_id}
- Transaction ID: {case.transaction_id}
- Amount: {case.amount} {case.currency}
- Reason Code: {case.reason_code}

PROSECUTOR'S OPENING:
{prosecutor_summary}

AVAILABLE EVIDENCE:
{evidence_summary}

Your task: Present an OPENING ARGUMENT for the cardholder. Identify weaknesses in prosecutor's case. Cite evidence IDs. Focus on: delivery issues, unauthorized use, merchant errors, or valid disputes. Be concise and reference evidence IDs."""

        response = await self._call_llm(prompt, "defense_opening")
        return self._parse_arguments(response, ArgumentRole.DEFENSE, ArgumentType.OPENING, 1)

    async def _prosecutor_rebuttal(
        self, case: TribunalCase, defense_args: list[Argument]
    ) -> list[Argument]:
        defense_summary = "\n".join([f"- {a.content[:200]}" for a in defense_args])
        evidence_summary = self._format_evidence_for_llm(case.evidence)

        prompt = f"""You are the PROSECUTOR. Rebut the defense's arguments.

DEFENSE ARGUMENTS:
{defense_summary}

EVIDENCE:
{evidence_summary}

Your task: REBUTTAL. Address each defense point with evidence. Cite evidence IDs. Strengthen merchant's position. Be concise."""

        response = await self._call_llm(prompt, "prosecutor_rebuttal")
        return self._parse_arguments(
            response, ArgumentRole.PROSECUTOR, ArgumentType.REBUTTAL, case.current_round
        )

    async def _defense_rebuttal(
        self, case: TribunalCase, prosecutor_args: list[Argument]
    ) -> list[Argument]:
        prosecutor_summary = "\n".join([f"- {a.content[:200]}" for a in prosecutor_args])
        evidence_summary = self._format_evidence_for_llm(case.evidence)

        prompt = f"""You are the DEFENSE. Rebut the prosecutor's latest arguments.

PROSECUTOR REBUTTAL:
{prosecutor_summary}

EVIDENCE:
{evidence_summary}

Your task: REBUTTAL. Address prosecutor's points. Maintain cardholder's position. Cite evidence IDs. Be concise."""

        response = await self._call_llm(prompt, "defense_rebuttal")
        return self._parse_arguments(
            response, ArgumentRole.DEFENSE, ArgumentType.REBUTTAL, case.current_round
        )

    async def _judge_ruling(self, case: TribunalCase) -> TribunalRuling:
        all_args = "\n\n".join(
            [
                f"[{a.role.value.upper()} - {a.type.value.upper()}] {a.content}"
                for a in case.arguments
            ]
        )
        evidence_summary = self._format_evidence_for_llm(case.evidence)

        prompt = f"""You are the JUDGE in a chargeback tribunal. Render a binding ruling.

CASE: {case.chargeback_id} | {case.amount} {case.currency} | Reason: {case.reason_code}

FULL DEBATE:
{all_args}

EVIDENCE:
{evidence_summary}

Your task: Render a RULING in JSON format:
{{
  "decision": "SUBMIT_EVIDENCE" | "ACCEPT_CHARGEBACK" | "ESCALATE_TO_HUMAN",
  "confidence": 0.0-1.0,
  "reasoning": "Detailed reasoning citing specific evidence and arguments",
  "evidence_summary": {{"strongest": ["ev_id1", "ev_id2"], "weakest": ["ev_id3"]}},
  "cost_if_wrong": estimated_cost_in_inr,
  "recommended_action": "Specific next steps",
  "deadline": "ISO8601 datetime or null"
}}

Rules:
- SUBMIT_EVIDENCE: Strong merchant case, high confidence (>0.7)
- ACCEPT_CHARGEBACK: Weak merchant case, low confidence (<0.4)
- ESCALATE_TO_HUMAN: Ambiguous, confidence 0.4-0.7, or high value (>₹50,000)
- cost_if_wrong = chargeback_fee + goods_value if SUBMIT_EVIDENCE but lose
- Be precise, cite evidence IDs, consider reason code"""

        response = await self._call_llm(prompt, "judge_ruling")
        return self._parse_ruling(response, case)

    def _format_evidence_for_llm(self, evidence: dict[str, Evidence]) -> str:
        lines = []
        for ev in evidence.values():
            lines.append(
                f"  [{ev.id}] {ev.type.value}: {ev.description} (strength: {ev.strength:.2f})"
            )
        return "\n".join(lines) if lines else "  No evidence available"

    async def _call_llm(self, prompt: str, context: str) -> str:
        client = self._get_llm_client()
        return await client.chat(
            prompt, temperature=settings.LLM_TEMPERATURE, max_tokens=settings.LLM_MAX_TOKENS
        )

    def _parse_arguments(
        self, response: str, role: ArgumentRole, arg_type: ArgumentType, round_num: int
    ) -> list[Argument]:
        arguments = []
        for block in response.split("\n\n"):
            if not block.strip():
                continue
            evidence_refs = []
            import re

            for match in re.finditer(r"\[(ev_[a-f0-9]{8})\]", block):
                evidence_refs.append(match.group(1))

            arguments.append(
                Argument(
                    id=f"arg_{uuid4().hex[:8]}",
                    role=role,
                    type=arg_type,
                    content=block.strip(),
                    evidence_refs=evidence_refs,
                    confidence=0.8,
                    round_number=round_num,
                )
            )
        return arguments

    def _parse_ruling(self, response: str, case: TribunalCase) -> TribunalRuling:
        try:
            # Strip markdown code fences if present
            cleaned = response.strip()
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
            if cleaned.startswith("```"):
                cleaned = cleaned[3:]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3]
            cleaned = cleaned.strip()

            # Find JSON object
            start = cleaned.find("{")
            end = cleaned.rfind("}") + 1
            if start >= 0 and end > start:
                data = json.loads(cleaned[start:end])
            else:
                raise ValueError("No JSON object found")
        except Exception:
            data = {
                "decision": "ESCALATE_TO_HUMAN",
                "confidence": 0.5,
                "reasoning": "Failed to parse LLM ruling",
                "evidence_summary": {"strongest": [], "weakest": []},
                "cost_if_wrong": case.amount + 2000,
                "recommended_action": "Manual review required",
                "deadline": None,
            }

        return TribunalRuling(
            decision=data.get("decision", "ESCALATE_TO_HUMAN"),
            confidence=data.get("confidence", 0.5),
            reasoning=data.get("reasoning", ""),
            evidence_summary=data.get("evidence_summary", {}),
            cost_if_wrong=data.get("cost_if_wrong", case.amount + 2000),
            recommended_action=data.get("recommended_action", "Manual review"),
            deadline=datetime.fromisoformat(data["deadline"].replace("Z", "+00:00")) if data.get("deadline") else None,
        )

    async def _should_conclude(self, case: TribunalCase) -> bool:
        recent_args = [a for a in case.arguments if a.round_number == case.current_round]
        if not recent_args:
            return True

        total_evidence_refs = set()
        for arg in recent_args:
            total_evidence_refs.update(arg.evidence_refs)

        return len(total_evidence_refs) < 2

    def get_case(self, case_id: str) -> TribunalCase | None:
        return self.cases.get(case_id)

    def get_case_by_chargeback(self, chargeback_id: str) -> TribunalCase | None:
        for case in self.cases.values():
            if case.chargeback_id == chargeback_id:
                return case
        return None


_tribunal_engine: TribunalEngine | None = None


async def get_tribunal_engine() -> TribunalEngine:
    global _tribunal_engine
    if _tribunal_engine is None:
        _tribunal_engine = TribunalEngine()
        await _tribunal_engine.initialize()
    return _tribunal_engine
