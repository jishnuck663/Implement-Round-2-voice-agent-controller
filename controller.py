"""Policy-aware outcome-first controller.

Only the simulator's public HTTP endpoints are used. Statistics are aggregated
by observation day and randomised arm; no simulator modules/scenario truth imports.
SQLite is the durable checkpoint, experiment, proposal and decision store.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, date
from pathlib import Path

import httpx

D0 = date(2027, 1, 4)  # simulator contract's first business day
DAY = 1440
MIN_ARM_CONNECTED = 120
CONFIDENCE_Z = 2.8  # conservative repeated, daily tests / two variants


def compact(x):
    return json.dumps(x, separators=(",", ":"), sort_keys=True)


def rate(a, b):
    return a / b if b else 0.0


def z_difference(s1, n1, s2, n2):
    if min(n1, n2) == 0:
        return 0.0
    p = (s1 + s2) / (n1 + n2)
    se = math.sqrt(max(p * (1 - p) * (1 / n1 + 1 / n2), 1e-12))
    return (s1 / n1 - s2 / n2) / se


def tv(a, b):
    return 0.5 * sum(abs(a.get(k, 0) - b.get(k, 0)) for k in set(a) | set(b))


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, val TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS stats (
          workflow TEXT NOT NULL, day INTEGER NOT NULL, hour INTEGER NOT NULL,
          dim TEXT NOT NULL, arm TEXT NOT NULL,
          dials INTEGER NOT NULL DEFAULT 0, connected INTEGER NOT NULL DEFAULT 0,
          engaged INTEGER NOT NULL DEFAULT 0, halluc INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY(workflow, day, hour, dim, arm));
        CREATE INDEX IF NOT EXISTS stat_query ON stats(workflow,dim,day,arm);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, at_ts TEXT, workflow TEXT,
          action TEXT, detail TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS intents (
          proposal_id TEXT PRIMARY KEY, workflow TEXT NOT NULL, kind TEXT NOT NULL,
          status TEXT NOT NULL, approval_id TEXT, base_version INTEGER NOT NULL,
          body TEXT NOT NULL, created_day INTEGER NOT NULL);
        CREATE INDEX IF NOT EXISTS intent_wf ON intents(workflow,status);
        CREATE TABLE IF NOT EXISTS plans (workflow TEXT PRIMARY KEY, json TEXT NOT NULL);
        """)
        self.db.commit()

    def get(self, key, default=None):
        r = self.db.execute("SELECT val FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(r[0]) if r else default

    def set(self, key, val):
        with self.db:
            self.db.execute("INSERT INTO meta(key,val) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET val=excluded.val", (key, compact(val)))

    def log(self, ts, wid, action, detail):
        with self.db:
            self.db.execute("INSERT INTO audit(at_ts,workflow,action,detail) VALUES(?,?,?,?)", (ts, wid, action, compact(detail)))

    def get_plan(self, wid):
        r = self.db.execute("SELECT json FROM plans WHERE workflow=?", (wid,)).fetchone()
        return json.loads(r[0]) if r else {"last_action_day": -999, "last_prompt_attempt": "", "started": False}

    def set_plan(self, wid, plan):
        with self.db:
            self.db.execute("INSERT INTO plans VALUES(?,?) ON CONFLICT(workflow) DO UPDATE SET json=excluded.json", (wid, compact(plan)))

    def aggregate(self, chunk_id, text_iter):
        counts = defaultdict(lambda: [0, 0, 0, 0])
        read = 0
        for line in text_iter:
            if not line:
                continue
            ev = json.loads(line)
            if ev.get("type") != "call":
                continue
            read += 1
            ts = ev["ts"]
            day = (date.fromisoformat(ts[:10]) - D0).days
            hour = int(ts[11:13])
            wid = ev["workflow_id"]
            var = ev["variant"]
            connected = int(bool(ev["connected"]))
            engaged = int(bool(ev["engaged"]))
            halluc = int(bool((ev.get("qa") or {}).get("hallucination_flag", False)))
            for dim, arm in [("all", "all"), ("hour", str(hour)),
                             ("caller_id", var["caller_id"]), ("welcome_message", var["welcome_message"]),
                             ("voice", var["voice"]), ("prompt_version", var["prompt_version"])]:
                c = counts[(wid, day, hour, dim, arm)]
                c[0] += 1
                c[1] += connected
                c[2] += engaged
                c[3] += halluc
        # Stats + chunk checkpoint are one durable transaction: replay never doubles counts.
        with self.db:
            self.db.executemany("""
              INSERT INTO stats(workflow,day,hour,dim,arm,dials,connected,engaged,halluc)
              VALUES(?,?,?,?,?,?,?,?,?)
              ON CONFLICT(workflow,day,hour,dim,arm) DO UPDATE SET
              dials=dials+excluded.dials, connected=connected+excluded.connected,
              engaged=engaged+excluded.engaged, halluc=halluc+excluded.halluc
              """, ((*k, *v) for k, v in counts.items()))
            self.db.execute("INSERT INTO meta(key,val) VALUES('last_chunk',?) ON CONFLICT(key) DO UPDATE SET val=excluded.val", (compact(chunk_id),))
        return read

    def stats(self, wid, dim, day_lo, day_hi=None):
        hi = 100000 if day_hi is None else day_hi
        return {r[0]: {"dials": r[1], "connected": r[2], "engaged": r[3], "halluc": r[4]}
                for r in self.db.execute("""
                 SELECT arm,SUM(dials),SUM(connected),SUM(engaged),SUM(halluc) FROM stats
                 WHERE workflow=? AND dim=? AND day BETWEEN ? AND ? GROUP BY arm
                """, (wid, dim, day_lo, hi))}

    def recent_approvals(self, wid, day):
        return self.db.execute("SELECT COUNT(*) FROM intents WHERE workflow=? AND kind='approval' AND created_day>=?", (wid, day-1)).fetchone()[0]

    def active(self, wid):
        return self.db.execute("SELECT proposal_id,kind,status,approval_id,base_version,body FROM intents WHERE workflow=? AND status IN ('sending','pending','approved','applying','auto_sending') ORDER BY rowid", (wid,)).fetchall()

    def intent(self, pid, wid, kind, base, body, day):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO intents VALUES(?,?,?,?,?,?,?,?)", (pid, wid, kind, 'auto_sending' if kind == 'auto' else 'sending', None, base, compact(body), day))

    def update_intent(self, pid, status, aid=None):
        with self.db:
            self.db.execute("UPDATE intents SET status=?,approval_id=COALESCE(?,approval_id) WHERE proposal_id=?", (status, aid, pid))


class Controller:
    def __init__(self, url, state_dir, minutes=60):
        self.http = httpx.Client(base_url=url, timeout=180, limits=httpx.Limits(max_connections=5))
        self.store = Store(Path(state_dir) / "controller.sqlite")
        self.wfs = {w["workflow_id"]: w for w in self.request("GET", "/workflows")}
        self.minutes = minutes
        self.st = self.request("GET", "/sim/state")
        old = self.store.get("scenario")
        # This detects a fresh world with fewer chunks than the prior world.
        if old and old != self.st["scenario"]:
            raise RuntimeError("State directory belongs to a different scenario. Set CONTROLLER_STATE_DIR.")
        self.store.set("scenario", self.st["scenario"])
        self.start_wall = time.monotonic()
        self.calls_processed = 0
        self.next_metrics = time.monotonic()

    def request(self, method, path, *, json_body=None, params=None, allow_error=False):
        for tries in range(5):
            try:
                r = self.http.request(method, path, json=json_body, params=params)
            except (httpx.TimeoutException, httpx.TransportError):
                if tries == 4:
                    raise
                time.sleep(min(0.5 * 2**tries, 5))
                continue
            if r.status_code in (429, 500, 502, 503, 504):
                if tries == 4:
                    r.raise_for_status()
                time.sleep(min(0.5 * 2**tries, 5))
                continue
            if allow_error:
                return r
            r.raise_for_status()
            return r.json()
        raise RuntimeError("exhausted retries")

    def ingest(self):
        last = self.store.get("last_chunk", -1)
        # Reconcile missed chunks BEFORE asking the simulator to advance again.
        # History is also delivered over HTTP (never via runs/ or scenario files).
        chunks = self.request("GET", "/events/chunks", params={"kind": "history", "after": last})
        chunks += self.request("GET", "/events/chunks", params={"kind": "events", "after": last})
        for ch in sorted(chunks, key=lambda c: c["chunk"]):
            if ch["chunk"] <= self.store.get("last_chunk", -1):
                continue
            with self.http.stream("GET", f"/events/chunks/{ch['chunk']}", timeout=180) as res:
                res.raise_for_status()
                n = self.store.aggregate(ch["chunk"], res.iter_lines())
                self.calls_processed += n
        return len(chunks)

    def clock(self):
        minute = self.st["sim_minute"]
        return minute // DAY, (minute % DAY) // 60, self.st["ts"]

    def make_pid(self, wid, base, reason, cfg):
        raw = compact([wid, base, reason, cfg])
        return "loop_" + hashlib.sha256(raw.encode()).hexdigest()[:24]

    def governance_ready(self, wf, day, hour):
        lo, hi = wf["governance"]["approver_hours"]
        if hour < max(lo, 11 if wf["tenant_id"].startswith("kestrel") else lo) or hour >= hi-2:
            return False
        limit = wf["governance"]["max_requests_per_24h"]
        return self.store.recent_approvals(wf["workflow_id"], day) < min(limit, 2)

    def reconciled(self, wid, now):
        """Reconcile durable intents with the authoritative public API on every pass."""
        cur = self.request("GET", f"/workflows/{wid}/config")
        last_seen = self.store.get(f"config_version:{wid}")
        if last_seen != cur["version"]:
            self.store.log(now, wid, "observed_config", {"old_version": last_seen,
                            "version": cur["version"], "actor": cur["actor"],
                            "config": cur["config"], "applied_at": cur.get("applied_at")})
            self.store.set(f"config_version:{wid}", cur["version"])
        hist = self.request("GET", f"/workflows/{wid}/config/history")
        applied_pids = {x.get("proposal_id") for x in hist}
        pendings = {a["proposal_id"]: a for a in self.request("GET", "/approvals", params={"workflow_id": wid})}
        for pid, kind, status, aid, base, raw in self.store.active(wid):
            if pid in applied_pids:
                self.store.update_intent(pid, "applied")
                continue
            if kind == "auto":
                if status == "auto_sending":
                    # A crash before the HTTP response: retry with the same id.
                    r = self.request("PUT", f"/workflows/{wid}/config", json_body=json.loads(raw), allow_error=True)
                    if r.status_code == 200:
                        self.store.update_intent(pid, "applied")
                        self.store.log(now, wid, "auto_applied", {"proposal_id": pid, "result": r.json()})
                    elif r.status_code == 409 and r.json().get("error") == "duplicate_proposal_id":
                        # Reconciliation already checked the history; refresh it once.
                        hist = self.request("GET", f"/workflows/{wid}/config/history")
                        self.store.update_intent(pid, "applied" if pid in {x.get('proposal_id') for x in hist} else "conflict")
                    else:
                        self.store.update_intent(pid, "conflict")
                        self.store.log(now, wid, "auto_failed", {"proposal_id": pid, "status": r.status_code, "body": r.text[:300]})
                continue
            a = pendings.get(pid)
            if not a and status == "sending":
                r = self.request("POST", "/approvals", json_body=json.loads(raw), allow_error=True)
                if r.status_code == 200:
                    a = r.json()
                else:
                    # If the server accepted the id before crashing, locate it.
                    a = {x["proposal_id"]: x for x in self.request("GET", "/approvals", params={"workflow_id": wid})}.get(pid)
                    if a is None:
                        self.store.update_intent(pid, "failed")
                        self.store.log(now, wid, "request_failed", {"proposal_id": pid, "response": r.text[:300]})
                        continue
            if not a:
                self.store.update_intent(pid, "missing")
                continue
            aid = a["approval_id"]
            if a["status"] == "approved":
                if cur["version"] != a["base_version"]:
                    self.store.update_intent(pid, "stale", aid)
                    self.store.log(now, wid, "stale", {"approval_id": aid, "current_version": cur["version"]})
                    intent_reason = json.loads(raw).get("rationale", "")
                    plan = self.store.get_plan(wid)
                    if intent_reason.startswith("Bounded randomized exploration"):
                        plan["started"] = False
                        plan["last_action_day"] = self.st["sim_minute"] // DAY
                    if intent_reason.startswith("Investigate high hallucination"):
                        plan["last_prompt_attempt"] = ""
                        plan["prompt_cooldown_day"] = self.st["sim_minute"] // DAY
                    self.store.set_plan(wid, plan)
                    continue
                self.store.update_intent(pid, "applying", aid)
                r = self.request("POST", f"/workflows/{wid}/apply", json_body={"approval_id": aid}, allow_error=True)
                if r.status_code == 200:
                    self.store.update_intent(pid, "applied", aid)
                    self.store.log(now, wid, "approved_applied", {"proposal_id": pid, "approval_id": aid, "version": r.json().get("version")})
                    cur = self.request("GET", f"/workflows/{wid}/config")
                else:
                    hist = self.request("GET", f"/workflows/{wid}/config/history")
                    done = pid in {x.get("proposal_id") for x in hist}
                    self.store.update_intent(pid, "applied" if done else "conflict", aid)
                    self.store.log(now, wid, "apply_result", {"proposal_id": pid, "http": r.status_code, "detail": r.text[:300]})
            elif a["status"] in ("pending",):
                self.store.update_intent(pid, "pending", aid)
            else:
                self.store.update_intent(pid, a["status"], aid)
                self.store.log(now, wid, "approval_closed", {"proposal_id": pid, "approval_id": aid, "status": a["status"], "reasons": a.get("reasons", [])})
                if a["status"] in ("rejected", "expired", "lapsed"):
                    intent_reason = json.loads(raw).get("rationale", "")
                    plan = self.store.get_plan(wid)
                    if intent_reason.startswith("Bounded randomized exploration"):
                        plan["started"] = False
                        plan["last_action_day"] = self.st["sim_minute"] // DAY
                    if intent_reason.startswith("Investigate high hallucination"):
                        plan["last_prompt_attempt"] = ""
                        plan["prompt_cooldown_day"] = self.st["sim_minute"] // DAY
                    self.store.set_plan(wid, plan)
        return cur

    def propose(self, wf, cur, cfg, reason, evidence=None, impact=0.05, metrics=None):
        wid = wf["workflow_id"]
        day, hour, now = self.clock()
        if cfg == cur["config"] or self.store.active(wid):
            return False
        base = cur["version"]
        pid = self.make_pid(wid, base, reason, cfg)
        dims = [k for k in ("cohorts", "allocations", "prompt_version", "schedule") if cfg[k] != cur["config"][k]]
        affected = int(wf["daily_batch"]["size"] * max(1, len(wf["cadence"])))
        score = min(1.0, max(0.0, impact))
        body = {"proposal_id": pid, "workflow_id": wid, "base_version": base, "proposed_config": cfg,
                "change_summary": reason[:160], "diff": {k: {"from": cur["config"].get(k), "to": cfg[k]} for k in dims},
                "affected_leads": affected,
                "dry_run": {"method": "historical randomized-arm aggregate, not a causal projection",
                            "available_stats": metrics or {}, "assumption": "future traffic resembles recent traffic"},
                "impact_score": score, "rationale": reason,
                "evidence": evidence or {}}
        auto = wf["governance"]["auto_apply"]
        allowed = set(auto.get("allowed_dims", []))
        actual = []
        if cfg["cohorts"] != cur["config"]["cohorts"]:
            actual.append("cohorts")
        if cfg["prompt_version"] != cur["config"]["prompt_version"]:
            actual.append("prompt_version")
        if cfg["schedule"] != cur["config"]["schedule"]:
            actual.append("schedule")
        for d in ("welcome_message", "voice", "caller_id"):
            before = cur["config"]["allocations"]
            after = cfg["allocations"]
            keys = {"default"} | set(before) | set(after)
            if any((before.get(k) or before["default"]).get(d, before["default"][d]) !=
                   (after.get(k) or after["default"]).get(d, after["default"][d]) for k in keys):
                actual.append(d)
        shift = 0.0
        for k in {"default"} | {c["id"] for c in cfg["cohorts"]}:
            for d in ("welcome_message", "voice", "caller_id"):
                before = (cur["config"]["allocations"].get(k) or {}).get(d) or cur["config"]["allocations"]["default"][d]
                after = (cfg["allocations"].get(k) or {}).get(d) or cfg["allocations"]["default"][d]
                shift = max(shift, tv(before, after))
        use_auto = (auto.get("enabled") and set(actual) <= allowed and shift <= auto.get("max_shift", 0) + 1e-9)
        if not use_auto and not self.governance_ready(wf, day, hour):
            return False
        if use_auto:
            body = {"proposal_id": pid, "base_version": base, "proposed_config": cfg, "rationale": reason}
            kind = "auto"
        else:
            kind = "approval"
        self.store.intent(pid, wid, kind, base, body, day)
        self.store.log(now, wid, "intent", {"kind": kind, "proposal_id": pid, "reason": reason,
                                             "base_version": base, "changed_dims": actual, "impact_score": score,
                                             "affected_leads_estimate": affected, "dry_run": metrics or {}})
        self.reconciled(wid, now)
        return True

    def discover_halluc(self, wf, cur):
        wid = wf["workflow_id"]
        day, hour, now = self.clock()
        if day < 0 or not 10 <= hour <= 17:
            return False
        current_prompt = cur["config"]["prompt_version"]
        plan = self.store.get_plan(wid)
        if (plan.get("last_prompt_attempt") == f"{current_prompt}:{cur['version']}"
                or plan.get("prompt_cooldown_day", -100) >= day):
            return False
        recent = self.store.stats(wid, "prompt_version", max(0, day-1), day).get(current_prompt)
        if not recent or recent["connected"] < 100:
            return False
        hrate = rate(recent["halluc"], recent["connected"])
        # The QA label has systematic false positives: compare against the
        # same workflow's long historical control, not an absolute 2% rule.
        history = self.store.stats(wid, "prompt_version", -1000, -1).get(current_prompt)
        if not history or history["connected"] < 300:
            history = self.store.stats(wid, "all", -1000, -1).get("all", {})
        base_n, base_h = history.get("connected", 0), history.get("halluc", 0)
        base_rate = rate(base_h, base_n)
        z = z_difference(recent["halluc"], recent["connected"], base_h, base_n)
        # Conservative single-day threshold avoids triggering on incidental P1 noise.
        if (hrate < max(0.028, base_rate + 0.012) or z < 2.7
                or recent["halluc"] < 7 or recent["connected"] < 180):
            return False
        if self.store.active(wid):
            return False
        credits = self.st.get("credits_remaining", 0)
        if credits < 12:
            return False
        self.store.log(now, wid, "halluc_alert", {"prompt": current_prompt, "rate": hrate,
                         "baseline_rate": base_rate, "z": z,
                         "flags": recent["halluc"], "connected": recent["connected"]})
        # The LLM suggests candidates, but validation and deterministic gating decide safety.
        suggestion = self.request("POST", "/llm/suggest-fixes", json_body={"workflow_id": wid,
                                 "problem": f"Observed hallucination flags {recent['halluc']}/{recent['connected']} on {current_prompt}"})
        candidates = suggestion.get("suggestions", []) or suggestion.get("fixes", [])
        if not isinstance(candidates, list):
            candidates = []
        names = []
        for candidate in candidates:
            pv = candidate if isinstance(candidate, str) else candidate.get("prompt_version")
            if pv and pv != current_prompt and pv in wf["options"]["prompt_version"] and pv not in names:
                names.append(pv)
        for pv in wf["options"]["prompt_version"]:
            if pv != current_prompt and pv not in names:
                names.append(pv)
        # Prioritise grounded/limited claims and a proven production fallback;
        # a more restrictive script is NOT automatically a better prompt.
        def prompt_rank(pv):
            summary = wf.get("prompt_catalog", {}).get(pv, "").lower()
            return (3 if "grounded" in summary or "restricted" in summary else
                    2 if "production" in summary or "current" in summary else
                    0 if "strict" in summary or "verbatim" in summary or "persuasive" in summary else 1)
        names.sort(key=prompt_rank, reverse=True)
        best = None
        # Budgeted: at most 2 candidate validations, 600 conversations each (12 credits).
        for pv in names[:2]:
            if self.st.get("credits_remaining", 0) < 10:
                break
            val = self.request("POST", "/validation/runs", json_body={"workflow_id": wid,
                                                                       "prompt_version": pv, "n": 600})
            self.st["credits_remaining"] = val.get("credits_remaining", self.st["credits_remaining"]-6)
            if val["pass_rate"] >= wf["governance"]["min_validation_pass_rate"]:
                # Validation establishes safety, not engagement. Within safe
                # candidates prefer a grounded policy instead of max pass rate.
                if best is None or prompt_rank(val["prompt_version"]) > prompt_rank(best["prompt_version"]):
                    best = val
        plan["last_prompt_attempt"] = f"{current_prompt}:{cur['version']}"
        self.store.set_plan(wid, plan)
        if best is None:
            self.store.log(now, wid, "no_safe_prompt", {"current": current_prompt})
            return False
        cfg = copy.deepcopy(cur["config"])
        cfg["prompt_version"] = best["prompt_version"]
        return self.propose(wf, cur, cfg, f"Investigate high hallucination flags {hrate:.1%}; switch to validated prompt {best['prompt_version']}",
                            evidence={"validation_run_id": best["validation_run_id"]},
                            impact=min(0.9, hrate * 10),
                            metrics={"detected_rate": hrate, "n": recent["connected"], "validation": best})

    def did_health(self, wf, cur):
        wid = wf["workflow_id"]
        day, hour, _ = self.clock()
        if day < 1 or not 11 <= hour <= 17:
            return False
        stats = self.store.stats(wid, "caller_id", max(0, day-1), day)
        dist = cur["config"]["allocations"]["default"]["caller_id"]
        active = [(v, stats.get(v, {})) for v, p in dist.items() if p >= 0.06]
        bad = None
        for v, s in active:
            n = s.get("dials", 0)
            rest = {"dials": sum(x.get("dials", 0) for vv, x in active if vv != v),
                    "connected": sum(x.get("connected", 0) for vv, x in active if vv != v)}
            if n < 160 or rest["dials"] < 160:
                continue
            diff = rate(s["connected"], n) - rate(rest["connected"], rest["dials"])
            z = z_difference(s["connected"], n, rest["connected"], rest["dials"])
            if diff < -0.045 and z < -3.0:
                if bad is None or z < bad[2]:
                    bad = (v, diff, z)
        if bad is None:
            return False
        v, diff, z = bad
        cfg = copy.deepcopy(cur["config"])
        other = max((q for q in dist if q != v), key=lambda q: rate(stats.get(q, {}).get("connected",0), stats.get(q, {}).get("dials",0)), default=None)
        if other is None:
            return False
        shift = min(0.2, max(0.0, dist[v]-0.05))
        if shift < 0.04:
            return False
        cfg["allocations"]["default"]["caller_id"] = {**dist, v: round(dist[v]-shift, 5), other: round(dist[other]+shift, 5)}
        return self.propose(wf, cur, cfg, f"Reduce degraded caller ID {v}: pickup {diff:+.1%} versus peer IDs (z={z:.1f})",
                            impact=min(1, abs(diff)*3), metrics={"arm": v, "peer": other, "pickup_diff": diff, "z": z})

    def learn(self, wf, cur):
        wid = wf["workflow_id"]
        day, hour, now = self.clock()
        if day < 0 or hour != 12:
            return False
        plan = self.store.get_plan(wid)
        if plan.get("last_action_day", -999) >= day or self.store.active(wid):
            return False
        cfg = copy.deepcopy(cur["config"])
        defaults = cfg["allocations"]["default"]
        # One challenger per dimension, with randomized holdout against incumbent.
        # No cross-tenant transport of winners. Existing supervisor distributions are respected.
        if not plan.get("started"):
            to_try = []
            for dim in ("welcome_message", "voice"):
                arm = defaults[dim]
                options = wf["options"][dim]
                incumbent = max(arm, key=arm.get)
                challenger = next((x for x in options if x != incumbent), None)
                if challenger and len(options) > 1 and len(arm) == 1:
                    cfg["allocations"]["default"][dim] = {incumbent: 0.80, challenger: 0.20}
                    to_try.append((dim, incumbent, challenger))
            if to_try:
                reason = "Bounded randomized exploration: " + ", ".join(f"{dim} {a}/{b} at 80/20" for dim, a, b in to_try)
                if self.propose(wf, cur, cfg, reason, impact=0.08, metrics={"arms": to_try, "sample_rule": MIN_ARM_CONNECTED}):
                    plan["started"] = True
                    plan["last_action_day"] = day
                    self.store.set_plan(wid, plan)
                    return True
            # If policy stopped the proposal, retry next eligible day.
            return False
        if day < 2:
            return False
        for dim in ("welcome_message", "voice"):
            dist = defaults[dim]
            if len(dist) < 2:
                continue
            pairs = sorted(dist, key=dist.get, reverse=True)
            inc, alt = pairs[:2]
            stats = self.store.stats(wid, dim, max(0, day-2), day)
            a, b = stats.get(inc, {}), stats.get(alt, {})
            n1, n2 = a.get("connected", 0), b.get("connected", 0)
            if min(n1, n2) < MIN_ARM_CONNECTED:
                continue
            primary = wf["objective"]["primary"]
            if primary == "engagement_rate":
                z = z_difference(b["engaged"], n2, a["engaged"], n1)
                diff = rate(b["engaged"], n2) - rate(a["engaged"], n1)
            else:
                z = z_difference(b["connected"], b["dials"], a["connected"], a["dials"])
                diff = rate(b["connected"], b["dials"]) - rate(a["connected"], a["dials"])
            guardrail = wf["objective"].get("guardrail", {})
            guard_ok = True
            if primary == "engagement_rate":
                guard_ok = rate(b["connected"], b["dials"]) - rate(a["connected"], a["dials"]) >= -guardrail.get("pickup_rate_max_drop", 0.02)
            else:
                guard_ok = rate(b["engaged"], n2) - rate(a["engaged"], n1) >= -guardrail.get("engagement_rate_max_drop", 0.02)
            # Holm-like conservative fixed evaluation once per business day, z>=2.8.
            # Inferior arms remain exposed at <=20% to permit decay detection.
            if z >= CONFIDENCE_Z and diff > 0.02 and guard_ok:
                new = {inc: max(0.15, dist[inc]-0.2), alt: min(0.85, dist[alt]+0.2)}
                reason = f"Evidence-based promote {dim} {alt}: primary delta {diff:+.1%}, z={z:.2f}, n={n1}/{n2}; holdout retained"
            elif z <= -CONFIDENCE_Z and diff < -0.02:
                new = {inc: min(0.95, dist[inc]+0.15), alt: max(0.05, dist[alt]-0.15)}
                reason = f"Rollback underperforming {dim} {alt}: primary delta {diff:+.1%}, z={z:.2f}"
            else:
                continue
            new = {k: round(v, 6) for k, v in new.items()}
            if new == dist:
                continue
            cfg["allocations"]["default"][dim] = new
            metrics = {"dim": dim, "candidate": alt, "incumbent": inc,
                       "primary_delta": diff, "z": z, "connected_n": {inc: n1, alt: n2}, "guardrail_ok": guard_ok}
            if self.propose(wf, cur, cfg, reason, impact=min(1, max(0, abs(diff))), metrics=metrics):
                plan["last_action_day"] = day
                self.store.set_plan(wid, plan)
                return True
        return False

    def decide(self):
        day, hour, ts = self.clock()
        for wid, wf in self.wfs.items():
            cur = self.reconciled(wid, ts)
            if day < 0 or self.store.active(wid):
                continue
            # Incidents take priority over experiments.
            if self.discover_halluc(wf, cur):
                continue
            if self.did_health(wf, cur):
                continue
            self.learn(wf, cur)

    def metrics(self):
        elapsed = max(0.001, time.monotonic() - self.start_wall)
        day, hour, ts = self.clock()
        final = {"timestamp": ts, "sim_minute": self.st["sim_minute"], "last_chunk": self.store.get("last_chunk", -1),
                 "calls_ingested_this_process": self.calls_processed,
                 "calls_per_wall_second": round(self.calls_processed / elapsed, 1),
                 "credits_remaining": self.st.get("credits_remaining", self.request("GET", "/sim/state")["credits_remaining"]),
                 "wall_seconds": round(elapsed, 2),
                 "pending": self.store.db.execute("SELECT COUNT(*) FROM intents WHERE status IN ('pending','sending','approved','applying')").fetchone()[0]}
        self.store.set("last_metrics", final)
        return final

    def run(self):
        self.ingest()
        while True:
            self.st = self.request("GET", "/sim/state")
            self.decide()
            if self.st["finished"]:
                break
            r = self.request("POST", "/sim/advance", json_body={"minutes": self.minutes}, allow_error=True)
            if r.status_code == 409 and r.json().get("error") == "finished":
                self.st = self.request("GET", "/sim/state")
                self.ingest()
                break
            r.raise_for_status()
            self.st = r.json()
            self.ingest()
            day, hour, ts = self.clock()
            if self.st["sim_minute"] % DAY == 0 or self.st["finished"]:
                print(compact(self.metrics()), flush=True)
        print("FINISHED", compact(self.metrics()), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--minutes", type=int, default=60)
    args = ap.parse_args()
    base = args.state_dir or os.environ.get("CONTROLLER_STATE_DIR", "controller_state")
    # Under separate scenarios avoid state collisions; for repeated runs set state-dir.
    with httpx.Client(base_url=args.url, timeout=30) as hc:
        scenario = hc.get("/sim/state").json()["scenario"]
    state_dir = Path(base) / scenario
    c = Controller(args.url, state_dir, args.minutes)
    c.run()


if __name__ == "__main__":
    main()
