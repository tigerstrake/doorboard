# Doorboard performance acceptance

T-702 composes the T-104 latency harness into a soak-style acceptance runner.

The simulator profile advances a 30-minute workload without waiting 30 real
minutes:

```bash
uv run python -m tests.performance.soak --profile full
```

CI uses the shorter profile, which keeps orchestration and verdict logic
exercised:

```bash
uv run python -m tests.performance.soak --profile ci
```

Reports are written under `tests/performance/results/`, which is gitignored.
Each run writes:

- `soak-verdict.json`: machine-readable pass/fail, counters, latency tables,
  resource maxima, and raw resource samples.
- `soak-report.txt`: human-readable verdict with p95 tables and compact ASCII
  graphs for queue depth and CPU.

The real bench profile should keep the same verdict schema when the hardware
adapters are available.
