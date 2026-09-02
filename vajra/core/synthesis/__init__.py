import ast
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from vajra.config import settings
from vajra.core.audit import AuditActorType, AuditEventCreate, AuditEventType, append_audit_event
from vajra.core.llm import get_llm_client


class PolicyStatus(str, Enum):
    PENDING = "pending"
    COMPILED = "compiled"
    VALIDATED = "validated"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATED = "escalated"
    EXPIRED = "expired"


class ActionType(str, Enum):
    COLLECT_EVIDENCE = "collect_evidence"
    SUBMIT_EVIDENCE = "submit_evidence"
    CALL_API = "call_api"
    WAIT = "wait"
    ESCALATE = "escalate"
    LOG = "log"
    CONDITION = "condition"
    LOOP = "loop"


@dataclass
class PolicyAction:
    id: str
    type: ActionType
    params: dict = field(default_factory=dict)
    timeout_seconds: int = 60
    retry_count: int = 0
    max_retries: int = 3
    condition: str | None = None
    on_success: str | None = None
    on_failure: str | None = None


@dataclass
class Policy:
    id: str
    name: str
    description: str
    chargeback_id: str
    case_id: str
    actions: list[PolicyAction] = field(default_factory=list)
    status: PolicyStatus = PolicyStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    current_action_index: int = 0
    context: dict = field(default_factory=dict)
    error: str | None = None


@dataclass
class PolicyExecutionResult:
    policy_id: str
    action_id: str
    status: str
    output: dict = field(default_factory=dict)
    error: str | None = None
    latency_ms: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class PolicyDSL:
    GRAMMAR = """
    POLICY ::= "DEFEND" chargeback_id "=" STRING ":" NEWLINE INDENT actions DEDENT
    actions ::= action (NEWLINE action)*
    action ::= COLLECT evidence_list
             | SUBMIT portal_deadline
             | CALL api_spec
             | WAIT duration
             | ESCALATE target_priority
             | LOG message
             | IF condition THEN actions ELSE actions
             | REPEAT count TIMES actions
    evidence_list ::= "evidence" ":" "[" STRING ("," STRING)* "]"
    portal_deadline ::= "to" ":" STRING "deadline" ":" STRING
    api_spec ::= "api" ":" STRING "params" ":" "{" key_value_pairs "}"
    duration ::= "for" ":" NUMBER "seconds" | "minutes" | "hours"
    target_priority ::= "to" ":" STRING "priority" ":" STRING
    condition ::= STRING
    key_value_pairs ::= STRING ":" value ("," STRING ":" value)*
    value ::= STRING | NUMBER | BOOLEAN | "null"
    """

    @classmethod
    def parse(cls, dsl_text: str) -> Policy:
        lines = [
            line.rstrip()
            for line in dsl_text.strip().split("\n")
            if line.strip() and not line.strip().startswith("#")
        ]
        if not lines:
            raise ValueError("Empty policy")

        header = lines[0]
        match = re.match(r'DEFEND\s+chargeback_id\s*=\s*["\']([^"\']+)["\']\s*:', header)
        if not match:
            raise ValueError(f"Invalid policy header: {header}")

        chargeback_id = match.group(1)
        policy = Policy(
            id=f"pol_{uuid4().hex[:12]}",
            name=f"Defense for {chargeback_id}",
            description="Auto-synthesized defense policy",
            chargeback_id=chargeback_id,
            case_id="",
        )

        actions = cls._parse_actions(lines[1:], 0)
        policy.actions = actions
        return policy

    @classmethod
    def _parse_actions(cls, lines: list[str], base_indent: int) -> list[PolicyAction]:
        actions = []
        i = 0
        while i < len(lines):
            line = lines[i]
            indent = len(line) - len(line.lstrip())

            if indent < base_indent:
                break

            stripped = line.strip()
            if stripped.startswith("#"):
                i += 1
                continue

            if stripped.startswith("COLLECT evidence:"):
                evidence_ids = re.findall(r'["\']([^"\']+)["\']', stripped)
                actions.append(
                    PolicyAction(
                        id=f"act_{uuid4().hex[:8]}",
                        type=ActionType.COLLECT_EVIDENCE,
                        params={"evidence_ids": evidence_ids},
                    )
                )

            elif stripped.startswith("SUBMIT to:"):
                portal_match = re.search(r'to\s*:\s*["\']([^"\']+)["\']', stripped)
                deadline_match = re.search(r'deadline\s*:\s*["\']([^"\']+)["\']', stripped)
                actions.append(
                    PolicyAction(
                        id=f"act_{uuid4().hex[:8]}",
                        type=ActionType.SUBMIT_EVIDENCE,
                        params={
                            "portal": portal_match.group(1) if portal_match else "visa_arbitration",
                            "deadline": deadline_match.group(1) if deadline_match else None,
                        },
                    )
                )

            elif stripped.startswith("CALL api:"):
                api_match = re.search(r'api\s*:\s*["\']([^"\']+)["\']', stripped)
                params_match = re.search(r"params\s*:\s*(\{.*\})", stripped)
                params = {}
                if params_match:
                    try:
                        params = json.loads(params_match.group(1))
                    except Exception:
                        params = {}
                actions.append(
                    PolicyAction(
                        id=f"act_{uuid4().hex[:8]}",
                        type=ActionType.CALL_API,
                        params={
                            "endpoint": api_match.group(1) if api_match else "",
                            "params": params,
                        },
                    )
                )

            elif stripped.startswith("WAIT for:"):
                duration_match = re.search(r"for\s*:\s*(\d+)\s*(\w+)", stripped)
                if duration_match:
                    value = int(duration_match.group(1))
                    unit = duration_match.group(2)
                    seconds = value * (
                        60 if unit.startswith("minute") else 3600 if unit.startswith("hour") else 1
                    )
                    actions.append(
                        PolicyAction(
                            id=f"act_{uuid4().hex[:8]}",
                            type=ActionType.WAIT,
                            params={"seconds": seconds},
                        )
                    )

            elif stripped.startswith("ESCALATE to:"):
                target_match = re.search(r'to\s*:\s*["\']([^"\']+)["\']', stripped)
                priority_match = re.search(r'priority\s*:\s*["\']([^"\']+)["\']', stripped)
                actions.append(
                    PolicyAction(
                        id=f"act_{uuid4().hex[:8]}",
                        type=ActionType.ESCALATE,
                        params={
                            "target": target_match.group(1)
                            if target_match
                            else "human_review_queue",
                            "priority": priority_match.group(1) if priority_match else "HIGH",
                        },
                    )
                )

            elif stripped.startswith("LOG"):
                msg_match = re.search(r'LOG\s+["\']([^"\']+)["\']', stripped)
                actions.append(
                    PolicyAction(
                        id=f"act_{uuid4().hex[:8]}",
                        type=ActionType.LOG,
                        params={"message": msg_match.group(1) if msg_match else stripped},
                    )
                )

            elif stripped.startswith("IF"):
                condition_match = re.search(r"IF\s+(.+?)\s+THEN", stripped)
                condition = condition_match.group(1) if condition_match else "true"
                then_actions = []
                else_actions = []
                i += 1
                while i < len(lines) and (len(lines[i]) - len(lines[i].lstrip())) > base_indent:
                    if lines[i].strip().startswith("ELSE"):
                        i += 1
                        break
                    then_actions.extend(cls._parse_actions([lines[i]], base_indent + 2))
                    i += 1
                while i < len(lines) and (len(lines[i]) - len(lines[i].lstrip())) > base_indent:
                    else_actions.extend(cls._parse_actions([lines[i]], base_indent + 2))
                    i += 1
                i -= 1
                actions.append(
                    PolicyAction(
                        id=f"act_{uuid4().hex[:8]}",
                        type=ActionType.CONDITION,
                        params={
                            "condition": condition,
                            "then_actions": then_actions,
                            "else_actions": else_actions,
                        },
                    )
                )

            i += 1

        return actions

    @classmethod
    def to_dsl(cls, policy: Policy) -> str:
        lines = [f'DEFEND chargeback_id = "{policy.chargeback_id}":']
        for action in policy.actions:
            lines.append(f"  {cls._action_to_dsl(action)}")
        return "\n".join(lines)

    @classmethod
    def _action_to_dsl(cls, action: PolicyAction) -> str:
        if action.type == ActionType.COLLECT_EVIDENCE:
            ev_ids = ", ".join(f'"{e}"' for e in action.params.get("evidence_ids", []))
            return f"COLLECT evidence: [{ev_ids}]"
        if action.type == ActionType.SUBMIT_EVIDENCE:
            return f'SUBMIT to: "{action.params.get("portal", "visa_arbitration")}" deadline: "{action.params.get("deadline", "")}"'
        if action.type == ActionType.CALL_API:
            return f'CALL api: "{action.params.get("endpoint", "")}" params: {json.dumps(action.params.get("params", {}))}'
        if action.type == ActionType.WAIT:
            return f"WAIT for: {action.params.get('seconds', 60)} seconds"
        if action.type == ActionType.ESCALATE:
            return f'ESCALATE to: "{action.params.get("target", "human_review_queue")}" priority: "{action.params.get("priority", "HIGH")}"'
        if action.type == ActionType.LOG:
            return f'LOG "{action.params.get("message", "")}"'
        if action.type == ActionType.CONDITION:
            return f"IF {action.params.get('condition', 'true')} THEN ... ELSE ..."
        return f"# Unknown action: {action.type}"


class PolicySynthesizer:
    def __init__(self):
        self.llm = get_llm_client()

    async def synthesize(self, ruling, case, evidence: dict) -> Policy:
        prompt = self._build_synthesis_prompt(ruling, case, evidence)
        response = await self.llm.chat(prompt, temperature=0.1, max_tokens=2048)
        policy = self._parse_policy(response, ruling, case)
        return policy

    def _build_synthesis_prompt(self, ruling, case, evidence: dict) -> str:
        ev_lines = []
        for ev in evidence.values():
            ev_lines.append(
                f"  [{ev.id}] {ev.type.value}: {ev.description} (strength: {ev.strength:.2f})"
            )
        evidence_text = "\n".join(ev_lines) if ev_lines else "  No evidence available"

        return f"""You are a POLICY SYNTHESIZER. Convert the tribunal ruling into an executable DSL policy.

TRIBUNAL RULING:
- Decision: {ruling.decision}
- Confidence: {ruling.confidence}
- Reasoning: {ruling.reasoning}
- Cost if wrong: ₹{ruling.cost_if_wrong:,.0f}
- Recommended action: {ruling.recommended_action}
- Deadline: {ruling.deadline}

CHARGEBACK CASE:
- Chargeback ID: {case.chargeback_id}
- Transaction ID: {case.transaction_id}
- Amount: {case.amount} {case.currency}
- Reason Code: {case.reason_code}

AVAILABLE EVIDENCE:
{evidence_text}

Generate a DSL policy that:
1. Collects the strongest evidence first
2. Submits to the appropriate portal (visa_arbitration, mastercard_arbitration, amex_dispute)
3. Includes deadline from ruling
4. Has escalation path if evidence collection fails
5. Logs key steps for audit

DSL Format:
DEFEND chargeback_id = "CH_123":
  COLLECT evidence: ["ev_abc12345", "ev_def67890"]
  SUBMIT to: "visa_arbitration" deadline: "2024-01-15T23:59:59Z"
  IF evidence_missing > 2 THEN
    ESCALATE to: "human_review_queue" priority: "HIGH"
  ELSE
    LOG "Evidence package submitted successfully"

Return ONLY the DSL policy, no explanation."""

    def _parse_policy(self, response: str, ruling, case) -> Policy:
        try:
            policy = PolicyDSL.parse(response)
            policy.case_id = case.id
            return policy
        except Exception:
            return self._fallback_policy(ruling, case)

    def _fallback_policy(self, ruling, case) -> Policy:
        policy = Policy(
            id=f"pol_{uuid4().hex[:12]}",
            name=f"Fallback defense for {case.chargeback_id}",
            description="Fallback policy due to synthesis error",
            chargeback_id=case.chargeback_id,
            case_id=case.id,
        )

        strong_evidence = [eid for eid, ev in case.evidence.items() if ev.strength > 0.5]

        policy.actions = [
            PolicyAction(
                id=f"act_{uuid4().hex[:8]}",
                type=ActionType.COLLECT_EVIDENCE,
                params={"evidence_ids": strong_evidence[:5]},
            ),
            PolicyAction(
                id=f"act_{uuid4().hex[:8]}",
                type=ActionType.SUBMIT_EVIDENCE,
                params={
                    "portal": "visa_arbitration",
                    "deadline": ruling.deadline.isoformat()
                    if ruling.deadline
                    else (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                },
            ),
            PolicyAction(
                id=f"act_{uuid4().hex[:8]}",
                type=ActionType.CONDITION,
                params={
                    "condition": "evidence_missing > 2",
                    "then_actions": [
                        PolicyAction(
                            id=f"act_{uuid4().hex[:8]}",
                            type=ActionType.ESCALATE,
                            params={"target": "human_review_queue", "priority": "HIGH"},
                        )
                    ],
                    "else_actions": [
                        PolicyAction(
                            id=f"act_{uuid4().hex[:8]}",
                            type=ActionType.LOG,
                            params={"message": "Evidence package submitted successfully"},
                        )
                    ],
                },
            ),
        ]
        return policy


class PolicyVerifier:
    @staticmethod
    def verify(policy: Policy) -> tuple[bool, list[str]]:
        errors = []

        if not policy.actions:
            errors.append("Policy has no actions")

        has_collect = any(a.type == ActionType.COLLECT_EVIDENCE for a in policy.actions)
        if not has_collect:
            errors.append("Policy must have at least one COLLECT_EVIDENCE action")

        has_submit = any(a.type == ActionType.SUBMIT_EVIDENCE for a in policy.actions)
        if not has_submit:
            errors.append("Policy must have at least one SUBMIT_EVIDENCE action")

        for action in policy.actions:
            if action.type == ActionType.COLLECT_EVIDENCE:
                if not action.params.get("evidence_ids"):
                    errors.append(f"Action {action.id}: COLLECT_EVIDENCE requires evidence_ids")

            if action.type == ActionType.SUBMIT_EVIDENCE:
                if not action.params.get("deadline"):
                    errors.append(f"Action {action.id}: SUBMIT_EVIDENCE requires deadline")

            if action.timeout_seconds > settings.POLICY_MAX_EXECUTION_TIME_SECONDS:
                errors.append(f"Action {action.id}: timeout exceeds maximum allowed")

            if action.max_retries > settings.POLICY_MAX_RETRIES:
                errors.append(f"Action {action.id}: max_retries exceeds maximum allowed")

        return len(errors) == 0, errors

    @staticmethod
    def verify_bounds(policy: Policy) -> tuple[bool, list[str]]:
        errors = []

        total_timeout = sum(a.timeout_seconds for a in policy.actions)
        if total_timeout > settings.POLICY_MAX_EXECUTION_TIME_SECONDS * 2:
            errors.append(f"Total policy timeout ({total_timeout}s) exceeds 2x maximum")

        submit_actions = [a for a in policy.actions if a.type == ActionType.SUBMIT_EVIDENCE]
        for action in submit_actions:
            deadline_str = action.params.get("deadline")
            if deadline_str:
                try:
                    deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
                    if deadline < datetime.now(timezone.utc):
                        errors.append(f"Action {action.id}: deadline is in the past")
                    if deadline > datetime.now(timezone.utc) + timedelta(days=30):
                        errors.append(f"Action {action.id}: deadline exceeds 30 days")
                except Exception:
                    errors.append(f"Action {action.id}: invalid deadline format")

        return len(errors) == 0, errors


class PolicyExecutor:
    def __init__(self):
        self.running_policies: dict[str, Policy] = {}
        self.execution_history: dict[str, list[PolicyExecutionResult]] = {}

    async def execute(self, policy: Policy) -> list[PolicyExecutionResult]:
        policy.status = PolicyStatus.EXECUTING
        policy.started_at = datetime.now(timezone.utc)
        self.running_policies[policy.id] = policy

        results = []

        try:
            for i, action in enumerate(policy.actions):
                policy.current_action_index = i
                result = await self._execute_action(policy, action)
                results.append(result)

                if result.status == "failed" and action.on_failure:
                    await self._handle_failure(policy, action, result)
                elif result.status == "completed" and action.on_success:
                    pass

            policy.status = PolicyStatus.COMPLETED
            policy.completed_at = datetime.now(timezone.utc)

        except Exception as e:
            policy.status = PolicyStatus.FAILED
            policy.error = str(e)
            results.append(
                PolicyExecutionResult(
                    policy_id=policy.id,
                    action_id="",
                    status="failed",
                    error=str(e),
                )
            )

        finally:
            self.execution_history[policy.id] = results
            if policy.id in self.running_policies:
                del self.running_policies[policy.id]

            await append_audit_event(
                AuditEventCreate(
                    event_type=AuditEventType.POLICY_EXECUTED,
                    actor_type=AuditActorType.AGENT_EXECUTOR,
                    actor_id="policy_executor",
                    correlation_id=policy.chargeback_id,
                    payload={
                        "policy_id": policy.id,
                        "status": policy.status.value,
                        "actions_executed": len(results),
                        "error": policy.error,
                    },
                )
            )

        return results

    async def _execute_action(self, policy: Policy, action: PolicyAction) -> PolicyExecutionResult:
        start = time.perf_counter()

        try:
            if action.type == ActionType.COLLECT_EVIDENCE:
                output = await self._collect_evidence(policy, action)
            elif action.type == ActionType.SUBMIT_EVIDENCE:
                output = await self._submit_evidence(policy, action)
            elif action.type == ActionType.CALL_API:
                output = await self._call_api(policy, action)
            elif action.type == ActionType.WAIT:
                output = await self._wait(action)
            elif action.type == ActionType.ESCALATE:
                output = await self._escalate(policy, action)
            elif action.type == ActionType.LOG:
                output = await self._log(action)
            elif action.type == ActionType.CONDITION:
                output = await self._evaluate_condition(policy, action)
            else:
                raise ValueError(f"Unknown action type: {action.type}")

            latency_ms = (time.perf_counter() - start) * 1000
            return PolicyExecutionResult(
                policy_id=policy.id,
                action_id=action.id,
                status="completed",
                output=output,
                latency_ms=latency_ms,
            )

        except Exception as e:
            latency_ms = (time.perf_counter() - start) * 1000
            return PolicyExecutionResult(
                policy_id=policy.id,
                action_id=action.id,
                status="failed",
                error=str(e),
                latency_ms=latency_ms,
            )

    async def _collect_evidence(self, policy: Policy, action: PolicyAction) -> dict:
        evidence_ids = action.params.get("evidence_ids", [])
        collected = []
        missing = []

        for eid in evidence_ids:
            evidence = None
            case = None
            for c in policy.__dict__.get("_cases", {}).values():
                if eid in c.evidence:
                    evidence = c.evidence[eid]
                    case = c
                    break

            if evidence and evidence.verified:
                collected.append(eid)
            else:
                missing.append(eid)

        policy.context["collected_evidence"] = collected
        policy.context["missing_evidence"] = missing
        policy.context["evidence_missing"] = len(missing)

        return {"collected": collected, "missing": missing, "evidence_missing": len(missing)}

    async def _submit_evidence(self, policy: Policy, action: PolicyAction) -> dict:
        portal = action.params.get("portal", "visa_arbitration")
        deadline = action.params.get("deadline")

        evidence_package = {
            "chargeback_id": policy.chargeback_id,
            "portal": portal,
            "deadline": deadline,
            "evidence_ids": policy.context.get("collected_evidence", []),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

        policy.context["submission"] = evidence_package
        return {"submitted": True, "portal": portal, "package": evidence_package}

    async def _call_api(self, policy: Policy, action: PolicyAction) -> dict:
        endpoint = action.params.get("endpoint", "")
        params = action.params.get("params", {})
        return {"called": endpoint, "params": params, "response": {"status": "mock_success"}}

    async def _wait(self, action: PolicyAction) -> dict:
        seconds = action.params.get("seconds", 60)
        import asyncio

        await asyncio.sleep(min(seconds, 5))
        return {"waited_seconds": seconds}

    async def _escalate(self, policy: Policy, action: PolicyAction) -> dict:
        target = action.params.get("target", "human_review_queue")
        priority = action.params.get("priority", "HIGH")
        return {"escalated_to": target, "priority": priority, "policy_id": policy.id}

    async def _log(self, action: PolicyAction) -> dict:
        message = action.params.get("message", "")
        return {"logged": message}

    async def _evaluate_condition(self, policy: Policy, action: PolicyAction) -> dict:
        condition = action.params.get("condition", "true")
        context = policy.context

        try:
            result = eval(condition, {"__builtins__": {}}, context)
        except Exception:
            result = False

        if result:
            for sub_action in action.params.get("then_actions", []):
                await self._execute_action(policy, sub_action)
        else:
            for sub_action in action.params.get("else_actions", []):
                await self._execute_action(policy, sub_action)

        return {"condition": condition, "result": result, "branch": "then" if result else "else"}

    async def _handle_failure(
        self, policy: Policy, action: PolicyAction, result: PolicyExecutionResult
    ):
        if action.retry_count < action.max_retries:
            action.retry_count += 1
            await self._execute_action(policy, action)
        elif action.on_failure:
            policy.status = PolicyStatus.ESCALATED
            await append_audit_event(
                AuditEventCreate(
                    event_type=AuditEventType.POLICY_ESCALATED,
                    actor_type=AuditActorType.AGENT_EXECUTOR,
                    actor_id="policy_executor",
                    correlation_id=policy.chargeback_id,
                    payload={
                        "policy_id": policy.id,
                        "failed_action": action.id,
                        "error": result.error,
                        "retry_count": action.retry_count,
                    },
                )
            )


_policy_synthesizer: PolicySynthesizer | None = None
_policy_executor: PolicyExecutor | None = None


def get_policy_synthesizer() -> PolicySynthesizer:
    global _policy_synthesizer
    if _policy_synthesizer is None:
        _policy_synthesizer = PolicySynthesizer()
    return _policy_synthesizer


def get_policy_executor() -> PolicyExecutor:
    global _policy_executor
    if _policy_executor is None:
        _policy_executor = PolicyExecutor()
    return _policy_executor
