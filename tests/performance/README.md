# Doorboard performance regression harness

T-702 composes the T-104 latency harness into a simulator regression runner.
It exercises orchestration and catches order-of-magnitude code-path regressions;
it is not evidence for physical latency, thermals, cameras, Hailo, WebRTC, or
hardware acceptance.

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
adapters are available. Only a report with `hardware_acceptance: true` may be
used to sign off ARCHITECTURE.md section 4 or the T-702 hardware gate.
