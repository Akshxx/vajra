import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generic, TypeVar
from uuid import uuid4

import mlflow
import numpy as np
from pydantic import BaseModel, Field

from vajra.config import settings


class MetricType(str, Enum):
    PRECISION = "precision"
    RECALL = "recall"
    F1 = "f1"
    WIN_RATE = "win_rate"
    FALSE_POSITIVE_COST = "false_positive_cost"
    LATENCY_P50 = "latency_p50"
    LATENCY_P99 = "latency_p99"
    THROUGHPUT = "throughput"
    CUSTOM = "custom"


@dataclass
class MetricResult:
    name: str
    type: MetricType
    value: float
    threshold: float | None = None
    passed: bool | None = None
    details: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.threshold is not None:
            if self.type in (
                MetricType.FALSE_POSITIVE_COST,
                MetricType.LATENCY_P50,
                MetricType.LATENCY_P99,
            ):
                self.passed = self.value <= self.threshold
            else:
                self.passed = self.value >= self.threshold


@dataclass
class EvalCase:
    id: str
    input: dict
    expected: dict
    metadata: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    case_id: str
    agent_name: str
    output: dict
    metrics: list[MetricResult]
    latency_ms: float
    cost_usd: float = 0.0
    error: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


T = TypeVar("T")


class AgentUnderTest(ABC, Generic[T]):
    @abstractmethod
    async def predict(self, input: dict) -> T:
        pass

    @abstractmethod
    def get_name(self) -> str:
        pass


class Evaluator(ABC):
    @abstractmethod
    def evaluate(self, case: EvalCase, output: dict) -> list[MetricResult]:
        pass

    @abstractmethod
    def get_metric_types(self) -> list[MetricType]:
        pass


class EvalHarness:
    def __init__(
        self,
        agent: AgentUnderTest,
        evaluators: list[Evaluator],
        dataset: list[EvalCase],
        name: str,
        mlflow_experiment: str | None = None,
    ):
        self.agent = agent
        self.evaluators = evaluators
        self.dataset = dataset
        self.name = name
        self.mlflow_experiment = mlflow_experiment or f"vajra/{name}"
        self.results: list[EvalResult] = []

    async def run(self, parallel: bool = False, max_concurrent: int = 5) -> dict:
        mlflow.set_experiment(self.mlflow_experiment)

        with mlflow.start_run(run_name=f"{self.name}_{uuid4().hex[:8]}") as run:
            mlflow.log_params(
                {
                    "agent": self.agent.get_name(),
                    "dataset_size": len(self.dataset),
                    "evaluators": [type(e).__name__ for e in self.evaluators],
                }
            )

            if parallel:
                import asyncio

                semaphore = asyncio.Semaphore(max_concurrent)

                async def run_case(case: EvalCase) -> EvalResult:
                    async with semaphore:
                        return await self._run_case(case)

                tasks = [run_case(case) for case in self.dataset]
                self.results = await asyncio.gather(*tasks)
            else:
                for case in self.dataset:
                    self.results.append(await self._run_case(case))

            summary = self._compute_summary()
            mlflow.log_metrics(summary)
            self._log_failed_cases()

            return {
                "run_id": run.info.run_id,
                "summary": summary,
                "results": self.results,
            }

    async def _run_case(self, case: EvalCase) -> EvalResult:
        start = time.perf_counter()
        try:
            output = await self.agent.predict(case.input)
            latency_ms = (time.perf_counter() - start) * 1000
            error = None
        except Exception as e:
            output = {}
            latency_ms = (time.perf_counter() - start) * 1000
            error = str(e)

        metrics = []
        for evaluator in self.evaluators:
            try:
                metrics.extend(evaluator.evaluate(case, output))
            except Exception as e:
                metrics.append(
                    MetricResult(
                        name=f"{type(evaluator).__name__}_error",
                        type=MetricType.CUSTOM,
                        value=1.0,
                        details={"error": str(e)},
                    )
                )

        result = EvalResult(
            case_id=case.id,
            agent_name=self.agent.get_name(),
            output=output,
            metrics=metrics,
            latency_ms=latency_ms,
            error=error,
        )
        return result

    def _compute_summary(self) -> dict:
        if not self.results:
            return {}

        all_metrics: dict[str, list[float]] = {}
        for r in self.results:
            for m in r.metrics:
                all_metrics.setdefault(m.name, []).append(m.value)

        summary = {}
        for name, values in all_metrics.items():
            summary[f"{name}_mean"] = float(np.mean(values))
            summary[f"{name}_std"] = float(np.std(values))
            summary[f"{name}_min"] = float(np.min(values))
            summary[f"{name}_max"] = float(np.max(values))

        passed = sum(
            1 for r in self.results if all(m.passed for m in r.metrics if m.passed is not None)
        )
        summary["pass_rate"] = passed / len(self.results) if self.results else 0
        summary["avg_latency_ms"] = float(np.mean([r.latency_ms for r in self.results]))

        return summary

    def _log_failed_cases(self):
        failed = [r for r in self.results if any(m.passed is False for m in r.metrics)]
        if failed:
            artifacts_dir = Path(f"/tmp/vajra_eval_{self.name}")
            artifacts_dir.mkdir(exist_ok=True)
            failed_file = artifacts_dir / "failed_cases.json"
            failed_file.write_text(
                json.dumps(
                    [
                        {
                            "case_id": r.case_id,
                            "input": self._get_case_input(r.case_id),
                            "output": r.output,
                            "metrics": [
                                {
                                    "name": m.name,
                                    "value": m.value,
                                    "threshold": m.threshold,
                                    "passed": m.passed,
                                }
                                for m in r.metrics
                            ],
                            "error": r.error,
                        }
                        for r in failed
                    ],
                    indent=2,
                )
            )
            mlflow.log_artifact(str(failed_file))

    def _get_case_input(self, case_id: str) -> dict:
        for case in self.dataset:
            if case.id == case_id:
                return case.input
        return {}

    def get_ci_gate_results(self) -> dict:
        thresholds = settings.EVAL_CI_THRESHOLDS
        gate_results = {}

        for metric_name, threshold in thresholds.items():
            values = [m.value for r in self.results for m in r.metrics if m.name == metric_name]
            if values:
                mean_val = float(np.mean(values))
                if "cost" in metric_name or "latency" in metric_name:
                    passed = mean_val <= threshold
                else:
                    passed = mean_val >= threshold
                gate_results[metric_name] = {
                    "value": mean_val,
                    "threshold": threshold,
                    "passed": passed,
                }

        gate_results["overall_passed"] = all(r["passed"] for r in gate_results.values())
        return gate_results


class DatasetLoader:
    @staticmethod
    def load_chargeback_dataset(path: str) -> list[EvalCase]:
        cases = []
        with open(path) as f:
            for line in f:
                data = json.loads(line)
                cases.append(
                    EvalCase(
                        id=data["id"],
                        input=data["input"],
                        expected=data["expected"],
                        metadata=data.get("metadata", {}),
                        tags=data.get("tags", []),
                    )
                )
        return cases

    @staticmethod
    def load_fraud_dataset(path: str) -> list[EvalCase]:
        return DatasetLoader.load_chargeback_dataset(path)

    @staticmethod
    def generate_synthetic_chargeback_cases(count: int = 1000) -> list[EvalCase]:
        cases = []
        for i in range(count):
            is_fraud = np.random.random() < 0.3
            cases.append(
                EvalCase(
                    id=f"synth_cb_{i}",
                    input={
                        "transaction_id": f"txn_{i}",
                        "amount": np.random.uniform(100, 50000),
                        "currency": "INR",
                        "card_fingerprint": f"fp_{np.random.randint(1000)}",
                        "device_id": f"dev_{np.random.randint(500)}",
                        "ip_address": f"192.168.{np.random.randint(256)}.{np.random.randint(256)}",
                        "shipping_address": {"pincode": f"{np.random.randint(100000, 999999)}"},
                        "billing_address": {"pincode": f"{np.random.randint(100000, 999999)}"},
                        "avs_result": np.random.choice(
                            ["match", "partial", "mismatch", "unavailable"]
                        ),
                        "cvv_result": np.random.choice(["match", "mismatch", "unavailable"]),
                        "3ds_result": np.random.choice(
                            ["success", "failed", "attempted", "unavailable"]
                        ),
                        "merchant_category": np.random.choice(
                            ["retail", "digital", "travel", "food"]
                        ),
                        "customer_history": {
                            "previous_orders": np.random.randint(0, 20),
                            "previous_chargebacks": np.random.randint(0, 3),
                            "account_age_days": np.random.randint(1, 1000),
                        },
                    },
                    expected={
                        "should_defend": not is_fraud,
                        "win_probability": 0.7 if not is_fraud else 0.2,
                        "evidence_quality": np.random.uniform(0.5, 1.0)
                        if not is_fraud
                        else np.random.uniform(0.1, 0.5),
                    },
                    metadata={"is_fraud": is_fraud},
                    tags=["synthetic"],
                )
            )
        return cases

    @staticmethod
    def generate_synthetic_fraud_cases(count: int = 5000) -> list[EvalCase]:
        cases = []
        for i in range(count):
            is_fraud = np.random.random() < 0.15
            cases.append(
                EvalCase(
                    id=f"synth_fraud_{i}",
                    input={
                        "transaction_id": f"txn_{i}",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "amount": np.random.uniform(50, 100000),
                        "user_id": f"user_{np.random.randint(2000)}",
                        "device_id": f"dev_{np.random.randint(1000)}",
                        "ip_address": f"10.0.{np.random.randint(256)}.{np.random.randint(256)}",
                        "card_bin": f"{np.random.randint(400000, 499999)}",
                        "merchant_id": f"merch_{np.random.randint(100)}",
                        "velocity_1h": np.random.randint(0, 10),
                        "velocity_24h": np.random.randint(0, 50),
                        "new_device": np.random.random() < 0.1,
                        "new_ip": np.random.random() < 0.15,
                        "geo_mismatch": np.random.random() < 0.05,
                    },
                    expected={
                        "is_fraud": is_fraud,
                        "fraud_type": np.random.choice(
                            ["card_testing", "account_takeover", "identity_theft", "friendly_fraud"]
                        )
                        if is_fraud
                        else "legitimate",
                    },
                    metadata={"is_fraud": is_fraud},
                    tags=["synthetic"],
                )
            )
        return cases


class PrecisionRecallEvaluator(Evaluator):
    def __init__(self, positive_class: Any = True, threshold: float = 0.5):
        self.positive_class = positive_class
        self.threshold = threshold

    def evaluate(self, case: EvalCase, output: dict) -> list[MetricResult]:
        y_true = case.expected.get("is_fraud", case.expected.get("should_defend", False))
        y_pred = output.get("is_fraud", output.get("should_defend", False))

        if isinstance(y_pred, (int, float)):
            y_pred = y_pred >= self.threshold

        tp = int(y_true and y_pred)
        fp = int(not y_true and y_pred)
        fn = int(y_true and not y_pred)
        tn = int(not y_true and not y_pred)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        return [
            MetricResult("precision", MetricType.PRECISION, precision),
            MetricResult("recall", MetricType.RECALL, recall),
            MetricResult("f1", MetricType.F1, f1),
        ]

    def get_metric_types(self) -> list[MetricType]:
        return [MetricType.PRECISION, MetricType.RECALL, MetricType.F1]


class WinRateEvaluator(Evaluator):
    def __init__(self, threshold: float = 0.6):
        self.threshold = threshold

    def evaluate(self, case: EvalCase, output: dict) -> list[MetricResult]:
        won = output.get("win", case.expected.get("should_defend", False))
        return [MetricResult("win_rate", MetricType.WIN_RATE, float(won), threshold=self.threshold)]

    def get_metric_types(self) -> list[MetricType]:
        return [MetricType.WIN_RATE]


class FalsePositiveCostEvaluator(Evaluator):
    def __init__(self, cost_per_fp: float = 2400, threshold: float = 1500):
        self.cost_per_fp = cost_per_fp
        self.threshold = threshold

    def evaluate(self, case: EvalCase, output: dict) -> list[MetricResult]:
        is_fraud = case.expected.get("is_fraud", False)
        predicted_fraud = output.get("is_fraud", False)

        fp_cost = self.cost_per_fp if (not is_fraud and predicted_fraud) else 0.0
        return [
            MetricResult(
                "false_positive_cost",
                MetricType.FALSE_POSITIVE_COST,
                fp_cost,
                threshold=self.threshold,
            )
        ]

    def get_metric_types(self) -> list[MetricType]:
        return [MetricType.FALSE_POSITIVE_COST]


class LatencyEvaluator(Evaluator):
    def __init__(self, p50_threshold: float = 100, p99_threshold: float = 300):
        self.p50_threshold = p50_threshold
        self.p99_threshold = p99_threshold

    def evaluate(self, case: EvalCase, output: dict) -> list[MetricResult]:
        latency = output.get("_latency_ms", 0)
        return [
            MetricResult(
                "latency_p50", MetricType.LATENCY_P50, latency, threshold=self.p50_threshold
            ),
            MetricResult(
                "latency_p99", MetricType.LATENCY_P99, latency, threshold=self.p99_threshold
            ),
        ]

    def get_metric_types(self) -> list[MetricType]:
        return [MetricType.LATENCY_P50, MetricType.LATENCY_P99]


def run_eval_suite(
    agent: AgentUnderTest,
    dataset: list[EvalCase],
    evaluators: list[Evaluator],
    name: str,
    parallel: bool = False,
) -> dict:
    harness = EvalHarness(agent, evaluators, dataset, name)
    import asyncio

    return asyncio.run(harness.run(parallel=parallel))
