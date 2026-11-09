"""Streaming HTTP ingestion microbenchmark; not a full public_03 grade.

Uses real HTTP NDJSON chunk and isolated SQLite DB. Repeats identical input
solely for throughput measurement; do not treat its counts as actual events.
"""
import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

import httpx
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from controller import Store

p = argparse.ArgumentParser()
p.add_argument("url")
p.add_argument("--repeats", type=int, default=4)
p.add_argument("--out", default="reports/ingest_microbench.json")
a = p.parse_args()
with httpx.Client(base_url=a.url, timeout=180) as http:
    chunks = http.get("/events/chunks", params={"kind": "history"}).json()
    chunk = max(chunks, key=lambda x: x["events"])
    with tempfile.TemporaryDirectory() as td:
        s = Store(Path(td) / "bench.sqlite")
        t0 = time.monotonic()
        calls = 0
        for i in range(a.repeats):
            with http.stream("GET", f"/events/chunks/{chunk['chunk']}") as response:
                response.raise_for_status()
                calls += s.aggregate(1000000 + i, response.iter_lines())
        elapsed = time.monotonic() - t0
        result = {"kind": "replayed_HTTP_chunk_microbenchmark_NOT_full_load",
                  "source_chunk_events": chunk["events"],
                  "source_chunk_calls": calls//a.repeats,
                  "repeats": a.repeats,
                  "call_rows_parsed": calls,
                  "throughput_calls_per_second": round(calls / elapsed),
                  "wall_seconds": round(elapsed, 3),
                  "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024, 1),
                  "warning": "Does not measure simulator generation or the public_03 full-scale scenario"}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(result, indent=2)+"\n")
    print(json.dumps(result, indent=2))
