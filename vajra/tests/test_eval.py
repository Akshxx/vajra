import pytest

from vajra.eval.harness import (
    AgentUnderTest,
    DatasetLoader,
    EvalCase,
    EvalHarness,
    FalsePositiveCostEvaluator,
    LatencyEvaluator,
    MetricResult,
    MetricType,
    PrecisionRecallEvaluator,
    WinRateEvaluator,
)


class MockAgent(AgentUnderTest[dict]):
    def __init__(self, name: str, predictions: dict[str, dict]):
        self._name = name
        self._predictions = predictions

    def get_name(self) -> str:
        return self._name

    async def predict(self, input: dict) -> dict:
        case_id = input.get("case_id", "")
        return self._predictions.get(case_id, {})


@pytest.fixture
def sample_cases():
    return [
        EvalCase(
            id=f"case_{i}",
            input={"case_id": f"case_{i}", "feature": i},
            expected={"is_fraud": i % 2 == 0, "should_defend": i % 2 == 1},
            metadata={"is_fraud": i % 2 == 0},
        )
        for i in range(10)
    ]


@pytest.fixture
def mock_predictions():
    return {
        f"case_{i}": {
            "is_fraud": i % 2 == 0,
            "should_defend": i % 2 == 1,
            "win": i % 2 == 1,
            "_latency_ms": 50 + i * 10,
        }
        for i in range(10)
    }


@pytest.mark.asyncio
async def test_precision_recall_evaluator(sample_cases):
    agent = MockAgent("test", {})
    evaluator = PrecisionRecallEvaluator(positive_class=True, threshold=0.5)

    case = sample_cases[0]
    case.expected = {"is_fraud": True}
    output = {"is_fraud": True}
    metrics = evaluator.evaluate(case, output)
    assert any(m.name == "precision" and m.value == 1.0 for m in metrics)
    assert any(m.name == "recall" and m.value == 1.0 for m in metrics)

    case.expected = {"is_fraud": False}
    output = {"is_fraud": True}
    metrics = evaluator.evaluate(case, output)
    assert any(m.name == "precision" and m.value == 0.0 for m in metrics)


@pytest.mark.asyncio
async def test_win_rate_evaluator(sample_cases):
    evaluator = WinRateEvaluator(threshold=0.6)

    case = sample_cases[0]
    case.expected = {"should_defend": True}
    output = {"win": True}
    metrics = evaluator.evaluate(case, output)
    assert any(m.name == "win_rate" and m.value == 1.0 and m.passed is True for m in metrics)

    output = {"win": False}
    metrics = evaluator.evaluate(case, output)
    assert any(m.name == "win_rate" and m.value == 0.0 and m.passed is False for m in metrics)


@pytest.mark.asyncio
async def test_false_positive_cost_evaluator(sample_cases):
    evaluator = FalsePositiveCostEvaluator(cost_per_fp=2400, threshold=1500)

    case = sample_cases[0]
    case.expected = {"is_fraud": False}
    output = {"is_fraud": True}
    metrics = evaluator.evaluate(case, output)
    assert any(
        m.name == "false_positive_cost" and m.value == 2400 and m.passed is False for m in metrics
    )

    output = {"is_fraud": False}
    metrics = evaluator.evaluate(case, output)
    assert any(
        m.name == "false_positive_cost" and m.value == 0 and m.passed is True for m in metrics
    )


@pytest.mark.asyncio
async def test_latency_evaluator(sample_cases):
    evaluator = LatencyEvaluator(p50_threshold=100, p99_threshold=300)

    case = sample_cases[0]
    output = {"_latency_ms": 50}
    metrics = evaluator.evaluate(case, output)
    assert any(m.name == "latency_p50" and m.value == 50 and m.passed is True for m in metrics)

    output = {"_latency_ms": 500}
    metrics = evaluator.evaluate(case, output)
    assert any(m.name == "latency_p50" and m.value == 500 and m.passed is False for m in metrics)


@pytest.mark.asyncio
async def test_eval_harness(sample_cases, mock_predictions):
    agent = MockAgent("mock_agent", mock_predictions)
    evaluators = [
        PrecisionRecallEvaluator(positive_class=True),
        WinRateEvaluator(threshold=0.5),
        FalsePositiveCostEvaluator(cost_per_fp=2400, threshold=1500),
        LatencyEvaluator(p50_threshold=200, p99_threshold=500),
    ]

    harness = EvalHarness(agent, evaluators, sample_cases, "test_agent")
    result = await harness.run(parallel=False)

    assert "summary" in result
    assert "results" in result
    assert len(result["results"]) == 10

    assert "precision_mean" in result["summary"]
    assert "recall_mean" in result["summary"]
    assert "win_rate_mean" in result["summary"]
    assert "pass_rate" in result["summary"]


@pytest.mark.asyncio
async def test_eval_harness_parallel(sample_cases, mock_predictions):
    agent = MockAgent("mock_agent", mock_predictions)
    evaluators = [PrecisionRecallEvaluator(positive_class=True)]

    harness = EvalHarness(agent, evaluators, sample_cases, "test_agent_parallel")
    result = await harness.run(parallel=True, max_concurrent=5)

    assert len(result["results"]) == 10


def test_dataset_loader_synthetic_chargeback():
    cases = DatasetLoader.generate_synthetic_chargeback_cases(100)
    assert len(cases) == 100
    assert all(isinstance(c, EvalCase) for c in cases)
    assert all("transaction_id" in c.input for c in cases)
    assert all("should_defend" in c.expected for c in cases)
    assert all("is_fraud" in c.metadata for c in cases)


def test_dataset_loader_synthetic_fraud():
    cases = DatasetLoader.generate_synthetic_fraud_cases(100)
    assert len(cases) == 100
    assert all(isinstance(c, EvalCase) for c in cases)
    assert all("transaction_id" in c.input for c in cases)
    assert all("is_fraud" in c.expected for c in cases)


def test_metric_result_threshold_logic():
    m1 = MetricResult("test", MetricType.PRECISION, 0.9, threshold=0.8)
    assert m1.passed is True

    m2 = MetricResult("test", MetricType.PRECISION, 0.7, threshold=0.8)
    assert m2.passed is False

    m3 = MetricResult("test", MetricType.FALSE_POSITIVE_COST, 1000, threshold=1500)
    assert m3.passed is True

    m4 = MetricResult("test", MetricType.FALSE_POSITIVE_COST, 2000, threshold=1500)
    assert m4.passed is False
