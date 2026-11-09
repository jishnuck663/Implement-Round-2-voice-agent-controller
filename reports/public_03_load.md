# Public 03 load test — status and reproducible evidence

**Status: Not completed.** Do not count this as a passing `public_03_load` run.

The full `python -m sim baseline --scenario scenarios/public_03_load.yaml` was attempted in this environment. It exceeded the tool's 200-second execution limit while generating the seven-day history, before the baseline or controlled run finished. The interrupted partial run was discarded, not graded. Consequently there is no measured public_03 end-to-end throughput, decision lag, peak RSS or wall time per simulated day. Those numbers **must not be invented**.

## Measured HTTP ingestion microbenchmark

See `ingest_microbench.json`, generated with `python scripts/bench_ingest.py <SIM_URL> --repeats 4`. It uses a real simulator HTTP history chunk, streams NDJSON, performs six per-call aggregate updates in SQLite transactions, and deliberately replays the same chunk in an isolated throwaway store for throughput measurement. This is **not** a substitute for the 12-workflow scenario and does not measure simulator generation, real backpressure, credit use, contention, or a 4-vCPU / 8 GB deployment.

## Full test to run on a machine with more available execution time

```bash
python -m sim baseline --scenario scenarios/public_03_load.yaml --run-dir runs/base03
python -m sim serve --scenario scenarios/public_03_load.yaml --run-dir runs/load03 --port 8700
CONTROLLER_STATE_DIR=run_states/load03 ./run.sh http://127.0.0.1:8700
python -m sim grade --run runs/load03 --baseline runs/base03 --out reports/public_03_grade.md
```

For the full run, capture wall time and peak RSS separately for the simulator, HTTP controller, and SQLite store; also measure event-through-ingest lag and decision latency, and report the worst 15-minute interval. Controller's `metrics()` writes `last_metrics` to its SQLite store. Workload volume and service characteristics at 10× require partitioning by workflow/tenant, bounded queues, durable streaming checkpoints and a multiwriter store.
