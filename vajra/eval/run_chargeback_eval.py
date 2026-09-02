import asyncio
from pathlib import Path

from vajra.agents.tribunal import get_tribunal_engine
from vajra.core.synthesis import get_policy_executor, get_policy_synthesizer
from vajra.eval.harness import (
    DatasetLoader,
    EvalHarness,
    FalsePositiveCostEvaluator,
    PrecisionRecallEvaluator,
    WinRateEvaluator,
)


class ChargebackAgent:
    def __init__(self):
        self.tribunal = None
        self.synthesizer = None
        self.executor = None

    async def initialize(self):
        self.tribunal = await get_tribunal_engine()
        self.synthesizer = get_policy_synthesizer()
        self.executor = get_policy_executor()

    def get_name(self) -> str:
        return "chargeback_defender"

    async def predict(self, input: dict) -> dict:
        case = await self.tribunal.create_case(input)
        ruling = await self.tribunal.run_debate(case.id)
        policy = await self.synthesizer.synthesize(ruling, case, case.evidence)
        results = await self.executor.execute(policy)

        won = ruling.decision == "SUBMIT_EVIDENCE" and ruling.confidence > 0.7
        evidence_missing = sum(
            1 for r in results if r.status == "failed" and "evidence" in r.action_id
        )

        return {
            "win": won,
            "should_defend": not input.get("metadata", {}).get("is_fraud", True),
            "is_fraud": input.get("metadata", {}).get("is_fraud", True),
            "ruling_decision": ruling.decision,
            "ruling_confidence": ruling.confidence,
            "evidence_missing": evidence_missing,
            "policy_status": policy.status.value,
        }


async def main():
    dataset_path = Path("/tmp/vajra_datasets/chargeback_test.jsonl")
    if not dataset_path.exists():
        print("Dataset not found. Run generate_datasets.py first.")
        return

    cases = DatasetLoader.load_chargeback_dataset(str(dataset_path))
    print(f"Loaded {len(cases)} test cases")

    agent = ChargebackAgent()
    await agent.initialize()

    evaluators = [
        WinRateEvaluator(threshold=0.60),
        FalsePositiveCostEvaluator(cost_per_fp=2400, threshold=1500),
        PrecisionRecallEvaluator(positive_class=True, threshold=0.7),
    ]

    harness = EvalHarness(agent, evaluators, cases[:1000], "chargeback_defender")
    result = await harness.run(parallel=True, max_concurrent=10)

    print("\n=== EVAL SUMMARY ===")
    for k, v in result["summary"].items():
        print(f"  {k}: {v}")

    gate_results = harness.get_ci_gate_results()
    print("\n=== CI GATES ===")
    for k, v in gate_results.items():
        if k != "overall_passed":
            status = "PASS" if v["passed"] else "FAIL"
            print(f"  {k}: {v['value']:.4f} (threshold: {v['threshold']}) [{status}]")
    print(f"  OVERALL: {'PASS' if gate_results['overall_passed'] else 'FAIL'}")

    if not gate_results["overall_passed"]:
        exit(1)


if __name__ == "__main__":
    asyncio.run(main())
