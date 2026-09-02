import mlflow


def main():
    mlflow.set_tracking_uri("file:/tmp/mlruns")

    experiments = mlflow.search_experiments()
    all_passed = True

    print("=== VAJRA CI GATE CHECK ===\n")

    thresholds = {
        "win_rate_mean": 0.60,
        "false_positive_cost_mean": 1500,
        "precision_mean": 0.85,
        "recall_mean": 0.80,
        "latency_p50_mean": 300,
        "pass_rate": 0.80,
    }

    for exp in experiments:
        if "vajra" not in exp.name:
            continue

        runs = mlflow.search_runs(experiment_ids=[exp.experiment_id])
        if runs.empty:
            print(f"Experiment {exp.name}: NO RUNS")
            continue

        latest_run = runs.sort_values("start_time", ascending=False).iloc[0]
        run_id = latest_run.run_id
        metrics = latest_run.data.metrics

        print(f"Experiment: {exp.name}")
        print(f"  Run: {run_id[:8]}")

        exp_passed = True
        for metric, threshold in thresholds.items():
            if metric in metrics:
                value = metrics[metric]
                if "cost" in metric or "latency" in metric:
                    passed = value <= threshold
                else:
                    passed = value >= threshold
                status = "PASS" if passed else "FAIL"
                if not passed:
                    exp_passed = False
                    all_passed = False
                print(f"  {metric}: {value:.4f} vs {threshold} [{status}]")
            else:
                print(f"  {metric}: MISSING")

        if not exp_passed:
            print("  >>> EXPERIMENT FAILED")

        artifacts = mlflow.artifacts.list_artifacts(run_id)
        failed_artifacts = [a for a in artifacts if "failed_cases" in a.path]
        if failed_artifacts:
            print(f"  >>> FAILED CASES ARTIFACTS: {len(failed_artifacts)}")
            all_passed = False

        print()

    print("=" * 40)
    if all_passed:
        print("✓ ALL CI GATES PASSED")
        exit(0)
    else:
        print("✗ SOME CI GATES FAILED")
        exit(1)


if __name__ == "__main__":
    main()
