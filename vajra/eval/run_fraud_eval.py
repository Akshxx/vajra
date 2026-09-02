import asyncio
from pathlib import Path

from vajra.core.causal_kg import get_fraud_graph
from vajra.eval.harness import (
    DatasetLoader,
    EvalHarness,
    FalsePositiveCostEvaluator,
    LatencyEvaluator,
    PrecisionRecallEvaluator,
)


class FraudSentinelAgent:
    def __init__(self):
        self.graph = None

    async def initialize(self):
        self.graph = await get_fraud_graph()

    def get_name(self) -> str:
        return "fraud_vajra"

    async def predict(self, input: dict) -> dict:
        explanation = self.graph.explain_transaction(input["transaction_id"], input)

        return {
            "is_fraud": explanation.is_fraud,
            "fraud_probability": explanation.counterfactual.get("fraud_probability", 0.0),
            "confidence": explanation.confidence,
            "causal_factors": len(explanation.causal_factors),
            "intervention": explanation.intervention,
        }


async def main():
    dataset_path = Path("/tmp/vajra_datasets/fraud_test.jsonl")
    if not dataset_path.exists():
        print("Dataset not found. Run generate_datasets.py first.")
        return

    cases = DatasetLoader.load_fraud_dataset(str(dataset_path))
    print(f"Loaded {len(cases)} test cases")

    agent = FraudSentinelAgent()
    await agent.initialize()

    evaluators = [
        PrecisionRecallEvaluator(positive_class=True, threshold=0.5),
        FalsePositiveCostEvaluator(cost_per_fp=5000, threshold=1500),
        LatencyEvaluator(p50_threshold=100, p99_threshold=300),
    ]

    harness = EvalHarness(agent, evaluators, cases[:2000], "fraud_vajra")
    result = await harness.run(parallel=True, max_concurrent=20)

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
