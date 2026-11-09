"""Query OUR SQLite ledger; no simulator run-dir files are used."""
import argparse
import json
import sqlite3

ap = argparse.ArgumentParser()
ap.add_argument("db")
ap.add_argument("--workflow", required=True)
ap.add_argument("--limit", type=int, default=30)
a = ap.parse_args()
conn = sqlite3.connect(a.db)
for ts, action, detail in conn.execute("SELECT at_ts,action,detail FROM audit WHERE workflow=? ORDER BY id DESC LIMIT ?", (a.workflow, a.limit)):
    print(ts, action, json.dumps(json.loads(detail), sort_keys=True))
