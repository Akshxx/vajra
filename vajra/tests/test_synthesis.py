from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest

from vajra.agents.tribunal import TribunalCase, TribunalRuling
from vajra.core.database import close_db, init_db
from vajra.core.synthesis import (
    ActionType,
    Policy,
    PolicyAction,
    PolicyDSL,
    PolicyStatus,
    PolicyVerifier,
    get_policy_executor,
    get_policy_synthesizer,
)


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    await init_db()
    yield
    await close_db()


@pytest.fixture
def sample_policy():
    return Policy(
        id=f"pol_test_{uuid4().hex[:8]}",
        name="Test Policy",
        description="Test",
        chargeback_id="cb_test",
        case_id="case_test",
        actions=[
            PolicyAction(
                id="act_1",
                type=ActionType.COLLECT_EVIDENCE,
                params={"evidence_ids": ["ev_1", "ev_2"]},
            ),
            PolicyAction(
                id="act_2",
                type=ActionType.SUBMIT_EVIDENCE,
                params={
                    "portal": "visa_arbitration",
                    "deadline": (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
                },
            ),
        ],
    )


@pytest.fixture
def sample_ruling():
    return TribunalRuling(
        decision="SUBMIT_EVIDENCE",
        confidence=0.85,
        reasoning="Strong evidence",
        evidence_summary={"strongest": ["ev_1"], "weakest": []},
        cost_if_wrong=5000,
        recommended_action="Submit to Visa",
        deadline=datetime.now(timezone.utc) + timedelta(days=5),
    )


@pytest.fixture
def sample_case():
    return TribunalCase(
        id="case_test",
        chargeback_id="cb_test",
        transaction_id="txn_test",
        amount=5000,
        currency="INR",
        reason_code="fraudulent",
        merchant_id="merch_1",
        customer_id="cust_1",
    )


def test_policy_dsl_parsing():
    dsl = '''DEFEND chargeback_id = "CH_123":
  COLLECT evidence: ["ev_1", "ev_2"]
  SUBMIT to: "visa_arbitration" deadline: "2024-01-15T23:59:59Z"
  WAIT for: 60 seconds
  ESCALATE to: "human_review_queue" priority: "HIGH"
  LOG "Policy completed"'''

    policy = PolicyDSL.parse(dsl)
    assert policy.chargeback_id == "CH_123"
    assert len(policy.actions) == 5
    assert policy.actions[0].type == ActionType.COLLECT_EVIDENCE
    assert policy.actions[1].type == ActionType.SUBMIT_EVIDENCE
    assert policy.actions[2].type == ActionType.WAIT
    assert policy.actions[3].type == ActionType.ESCALATE
    assert policy.actions[4].type == ActionType.LOG


def test_policy_dsl_serialization(sample_policy):
    dsl = PolicyDSL.to_dsl(sample_policy)
    assert "DEFEND chargeback_id" in dsl
    assert "COLLECT evidence" in dsl
    assert "SUBMIT to" in dsl
    assert "visa_arbitration" in dsl


def test_policy_verifier_valid(sample_policy):
    valid, errors = PolicyVerifier.verify(sample_policy)
    assert valid is True
    assert len(errors) == 0


def test_policy_verifier_missing_collect():
    policy = Policy(
        id="pol_test",
        name="Test",
        description="Test",
        chargeback_id="cb_test",
        case_id="case_test",
        actions=[
            PolicyAction(
                id="act_1",
                type=ActionType.SUBMIT_EVIDENCE,
                params={"portal": "visa", "deadline": "2024-01-15T23:59:59Z"},
            ),
        ],
    )
    valid, errors = PolicyVerifier.verify(policy)
    assert valid is False
    assert any("COLLECT_EVIDENCE" in e for e in errors)


def test_policy_verifier_missing_submit():
    policy = Policy(
        id="pol_test",
        name="Test",
        description="Test",
        chargeback_id="cb_test",
        case_id="case_test",
        actions=[
            PolicyAction(
                id="act_1",
                type=ActionType.COLLECT_EVIDENCE,
                params={"evidence_ids": ["ev_1"]},
            ),
        ],
    )
    valid, errors = PolicyVerifier.verify(policy)
    assert valid is False
    assert any("SUBMIT_EVIDENCE" in e for e in errors)


def test_policy_verifier_bounds(sample_policy):
    valid, errors = PolicyVerifier.verify_bounds(sample_policy)
    assert valid is True
    assert len(errors) == 0


def test_policy_verifier_past_deadline():
    policy = Policy(
        id="pol_test",
        name="Test",
        description="Test",
        chargeback_id="cb_test",
        case_id="case_test",
        actions=[
            PolicyAction(
                id="act_1",
                type=ActionType.COLLECT_EVIDENCE,
                params={"evidence_ids": ["ev_1"]},
            ),
            PolicyAction(
                id="act_2",
                type=ActionType.SUBMIT_EVIDENCE,
                params={"portal": "visa", "deadline": "2020-01-01T00:00:00Z"},
            ),
        ],
    )
    valid, errors = PolicyVerifier.verify_bounds(policy)
    assert valid is False
    assert any("past" in e for e in errors)


@pytest.mark.asyncio
async def test_policy_executor(sample_policy):
    executor = get_policy_executor()
    results = await executor.execute(sample_policy)

    assert len(results) == 2
    assert all(r.status in ["completed", "failed"] for r in results)
    assert sample_policy.status == PolicyStatus.COMPLETED
    assert sample_policy.completed_at is not None


@pytest.mark.asyncio
async def test_policy_executor_with_condition():
    policy = Policy(
        id=f"pol_cond_{uuid4().hex[:8]}",
        name="Conditional Policy",
        description="Test",
        chargeback_id="cb_test",
        case_id="case_test",
        actions=[
            PolicyAction(
                id="act_1",
                type=ActionType.COLLECT_EVIDENCE,
                params={"evidence_ids": ["ev_1"]},
            ),
            PolicyAction(
                id="act_2",
                type=ActionType.CONDITION,
                params={
                    "condition": "evidence_missing > 0",
                    "then_actions": [
                        PolicyAction(
                            id="act_3",
                            type=ActionType.ESCALATE,
                            params={"target": "human_review", "priority": "HIGH"},
                        )
                    ],
                    "else_actions": [
                        PolicyAction(
                            id="act_4",
                            type=ActionType.LOG,
                            params={"message": "All evidence collected"},
                        )
                    ],
                },
            ),
        ],
    )

    executor = get_policy_executor()
    results = await executor.execute(policy)

    assert policy.status == PolicyStatus.COMPLETED
    assert len(results) >= 2


@pytest.mark.asyncio
async def test_policy_synthesizer(sample_ruling, sample_case):
    synthesizer = get_policy_synthesizer()
    policy = await synthesizer.synthesize(sample_ruling, sample_case, {})

    assert policy.chargeback_id == "cb_test"
    assert policy.case_id == "case_test"
    assert len(policy.actions) >= 2

    has_collect = any(a.type == ActionType.COLLECT_EVIDENCE for a in policy.actions)
    has_submit = any(a.type == ActionType.SUBMIT_EVIDENCE for a in policy.actions)
    assert has_collect
    assert has_submit

    valid, errors = PolicyVerifier.verify(policy)
    assert valid, f"Synthesized policy invalid: {errors}"
