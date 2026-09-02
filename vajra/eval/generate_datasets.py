import json
from pathlib import Path

from vajra.eval.harness import DatasetLoader


def main():
    output_dir = Path("/tmp/vajra_datasets")
    output_dir.mkdir(exist_ok=True, parents=True)

    chargeback_cases = DatasetLoader.generate_synthetic_chargeback_cases(5000)
    fraud_cases = DatasetLoader.generate_synthetic_fraud_cases(10000)

    chargeback_file = output_dir / "chargeback_test.jsonl"
    with open(chargeback_file, "w") as f:
        for case in chargeback_cases:
            f.write(
                json.dumps(
                    {
                        "id": case.id,
                        "input": case.input,
                        "expected": case.expected,
                        "metadata": case.metadata,
                        "tags": case.tags,
                    }
                )
                + "\n"
            )

    fraud_file = output_dir / "fraud_test.jsonl"
    with open(fraud_file, "w") as f:
        for case in fraud_cases:
            f.write(
                json.dumps(
                    {
                        "id": case.id,
                        "input": case.input,
                        "expected": case.expected,
                        "metadata": case.metadata,
                        "tags": case.tags,
                    }
                )
                + "\n"
            )

    print(f"Generated {len(chargeback_cases)} chargeback cases -> {chargeback_file}")
    print(f"Generated {len(fraud_cases)} fraud cases -> {fraud_file}")


if __name__ == "__main__":
    main()
