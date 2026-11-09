# Working controller — how to run and submit

**Start here:** `SUBMISSION.md` explains scope, metrics, trade-offs, stakeholders, incidents and operational risks. The original assignment instructions remain in `README.md` and the public simulator contract remains in `docs/SIMULATOR.md`.

## Files added

- `controller.py` — closed-loop controller, **HTTP-only**, no hidden scenario/truth access
- `run.sh` — mandatory evaluator entry point (`./run.sh <SIM_URL>`)
- `SUBMISSION.md` — full evidence and judgement document; update name/hours honestly
- `reports/public_01.md`, `reports/public_02.md` — actual `sim grade` outputs
- `reports/public_03_load.md`, `reports/ingest_microbench.json` — transparent load limitation and measured HTTP parsing microbenchmark
- `scripts/crash_recovery.py`, `reports/crash_recovery.json` — kill/restart proof
- `scripts/bench_ingest.py` — reproducible ingestion-only microbenchmark
- `scripts/audit.py` — search decision evidence in our SQLite store
- `tests/test_controller.py` — controller unit checks

## Native Windows (no Docker or Conda required)

The grader runs `run.sh` on Linux. On Windows, call `python controller.py --url ...` instead of running Bash. Python 3.11+ recommended.

```powershell
cd sde-assignment-2026-main
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q tests
```

**Terminal A** (start a fresh public simulator):

```powershell
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/mine01 --port 8700
```

**Terminal B:**

```powershell
$env:CONTROLLER_STATE_DIR="run_states/mine01"
python controller.py --url http://127.0.0.1:8700
```

Do not close Terminal A while running controller. To restart just the controller, keep Terminal A alive and use the **same** state directory. For public_02 use a fresh simulator run-dir and fresh state directory.

## Linux grader

```bash
python3 -m pip install -r requirements.txt
python -m sim baseline --scenario scenarios/public_01_dev.yaml --run-dir runs/base01
python -m sim serve --scenario scenarios/public_01_dev.yaml --run-dir runs/mine01
CONTROLLER_STATE_DIR=run_states/mine01 ./run.sh http://127.0.0.1:8700
python -m sim grade --run runs/mine01 --baseline runs/base01 --out reports/public_01.md
```

Repeat with public_02 scenario and new run/SQLite dirs. `run.sh` processes all history chunks before it advances simulated time; it continues until `finished=true`. It is normal for dev scenario results to show limited/no positive lift. Logs show one line of metrics per simulated day.

## Audit / crash / load

```bash
python scripts/audit.py run_states/mine01/public_01_dev/controller.sqlite --workflow nw_renewal --limit 15
# With a DIFFERENT fresh simulator already serving on port 8704:
python scripts/crash_recovery.py http://127.0.0.1:8704 --state-dir run_states/recovery
python scripts/bench_ingest.py http://127.0.0.1:8704 --repeats 4
```

The original repository is public, but the assignment instructs you **not to publish your solution publicly**. Create a private GitHub repository for your solution and share evaluator access as directed by the form; do not publish client-related audit data. Commit your own work honestly in stages. The supplied ZIP contains project files and saved reports, **not** the full local run directories or generated historical events.

## Critical review discussion

Know how event chunk checkpointing is atomic, why only actually observed experimental arms are evaluated, why P3 validation tests safety but cannot prove engagement lift, why changing `caller_id` affects pickup, why `mid_flight` differs by workflow, why an approval ID is not a named human, and why the missing `public_03` run cannot be silently claimed as passed.
