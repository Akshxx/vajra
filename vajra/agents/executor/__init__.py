import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import uuid4

from vajra.agents.tribunal import get_tribunal_engine
from vajra.config import settings
from vajra.core.audit import AuditActorType, AuditEventType, append_audit_event
from vajra.core.synthesis import (
    Policy,
    PolicyExecutor,
    PolicyStatus,
    PolicySynthesizer,
    get_policy_executor,
    get_policy_synthesizer,
)


@dataclass
class DefenseJob:
    id: str
    chargeback_id: str
    case_id: str
    policy_id: str | None = None
    status: str = "pending"
    created_at: datetime = field(default_factory=datetime.utcnow)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    result: dict = field(default_factory=dict)


class DefenseExecutor:
    def __init__(self):
        self.jobs: dict[str, DefenseJob] = {}
        self._running = False

    async def execute_defense(self, chargeback_data: dict) -> DefenseJob:
        job = DefenseJob(
            id=f"job_{uuid4().hex[:12]}",
            chargeback_id=chargeback_data["chargeback_id"],
            case_id="",
        )
        self.jobs[job.id] = job

        try:
            tribunal = await get_tribunal_engine()
            case = await tribunal.create_case(chargeback_data)
            job.case_id = case.id

            ruling = await tribunal.run_debate(case.id)

            synthesizer = get_policy_synthesizer()
            policy = await synthesizer.synthesize(ruling, case, case.evidence)
            job.policy_id = policy.id

            executor = get_policy_executor()
            results = await executor.execute(policy)

            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.result = {
                "ruling": {
                    "decision": ruling.decision,
                    "confidence": ruling.confidence,
                    "cost_if_wrong": ruling.cost_if_wrong,
                },
                "policy_status": policy.status.value,
                "actions_executed": len(results),
                "success": all(r.status == "completed" for r in results),
            }

            await append_audit_event(
                {
                    "event_type": AuditEventType.POLICY_EXECUTED,
                    "actor_type": AuditActorType.AGENT_EXECUTOR,
                    "actor_id": "defense_executor",
                    "correlation_id": chargeback_data["chargeback_id"],
                    "payload": {
                        "job_id": job.id,
                        "case_id": case.id,
                        "policy_id": policy.id,
                        "status": job.status,
                        "result": job.result,
                    },
                }
            )

        except Exception as e:
            job.status = "failed"
            job.completed_at = datetime.now(timezone.utc)
            job.error = str(e)

            await append_audit_event(
                {
                    "event_type": AuditEventType.POLICY_ESCALATED,
                    "actor_type": AuditActorType.AGENT_EXECUTOR,
                    "actor_id": "defense_executor",
                    "correlation_id": chargeback_data["chargeback_id"],
                    "payload": {
                        "job_id": job.id,
                        "error": str(e),
                    },
                }
            )

        return job

    def get_job(self, job_id: str) -> DefenseJob | None:
        return self.jobs.get(job_id)

    def get_jobs_by_chargeback(self, chargeback_id: str) -> list[DefenseJob]:
        return [j for j in self.jobs.values() if j.chargeback_id == chargeback_id]


_defense_executor: DefenseExecutor | None = None


def get_defense_executor() -> DefenseExecutor:
    global _defense_executor
    if _defense_executor is None:
        _defense_executor = DefenseExecutor()
    return _defense_executor
