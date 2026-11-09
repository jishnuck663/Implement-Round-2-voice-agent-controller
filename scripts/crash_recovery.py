"""Demonstrate killing the controller while simulator stays alive, then restarting.

Run from repository root:
    python scripts/crash_recovery.py http://127.0.0.1:8704 --state-dir run_states/recovery
Start a fresh public_01 simulator on port 8704 first. Uses ONLY the HTTP API.
"""
import argparse
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("--state-dir", default="run_states/recovery")
    parser.add_argument("--kill-after", type=int, default=2100, help="simulated minute")
    parser.add_argument("--out", default="reports/crash_recovery.json")
    args = parser.parse_args()
    scenario = httpx.get(args.url + "/sim/state", timeout=20).json()["scenario"]
    db_path = Path(args.state_dir) / scenario / "controller.sqlite"
    Path(args.state_dir).mkdir(parents=True, exist_ok=True)

    def spawn(log):
        logpath = Path(args.state_dir) / log
        handle = logpath.open("w")
        proc = subprocess.Popen([sys.executable, "-u", "controller.py", "--url", args.url,
                                 "--state-dir", args.state_dir, "--minutes", "15"],
                                stdout=handle, stderr=subprocess.STDOUT)
        return proc, handle

    first, output = spawn("before_kill.log")
    killed_at = None
    deadline = time.monotonic() + 160
    while time.monotonic() < deadline:
        st = httpx.get(args.url + "/sim/state", timeout=30).json()
        if st["sim_minute"] >= args.kill_after and not st["finished"]:
            killed_at = st["sim_minute"]
            first.kill()
            break
        if first.poll() is not None:
            raise RuntimeError(f"controller exited early with code {first.returncode}")
        time.sleep(0.05)
    else:
        first.kill()
        raise TimeoutError("controller did not reach kill point")
    first.wait(timeout=10)
    output.close()
    restarted, output2 = spawn("after_restart.log")
    try:
        restarted.wait(timeout=160)
    finally:
        output2.close()
    final = httpx.get(args.url + "/sim/state", timeout=30).json()
    with sqlite3.connect(db_path) as db:
        intents = db.execute("SELECT proposal_id,status FROM intents").fetchall()
        duplicate_pids = db.execute("SELECT proposal_id,COUNT(*) FROM intents GROUP BY proposal_id HAVING COUNT(*)>1").fetchall()
        observed = db.execute("SELECT COUNT(*) FROM audit WHERE action='observed_config'").fetchone()[0]
        last_chunk = json.loads(db.execute("SELECT val FROM meta WHERE key='last_chunk'").fetchone()[0])
    assert restarted.returncode == 0, f"restart failed: {restarted.returncode}"
    assert final["finished"] is True
    assert not duplicate_pids
    assert all(s not in ("sending", "applying", "auto_sending", "pending", "approved") for _, s in intents), intents
    result = {"scenario": scenario, "kill_at_minute": killed_at, "restart_exit_code": restarted.returncode,
              "finished": final["finished"], "end_minute": final["sim_minute"],
              "persisted_last_chunk": last_chunk, "all_unique_proposal_ids": True,
              "unresolved_intents": 0, "observed_config_snapshots": observed,
              "audit_db": str(db_path)}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
