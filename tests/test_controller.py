"""Controller implementation tests — no simulator internals imported."""
import sqlite3
from controller import Store, rate, tv, z_difference


def test_z_test_and_shift():
    assert z_difference(60, 100, 40, 100) > 2.8
    assert abs(tv({"a": 0.8, "b": 0.2}, {"a": 0.6, "b": 0.4}) - 0.2) < 1e-10
    assert rate(0, 0) == 0


def test_exactly_once_ingest(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    ev = {"type": "call", "ts": "2027-01-04T11:00:00+05:30", "workflow_id": "foo",
          "variant": {"voice": "V1", "welcome_message": "W1", "caller_id": "D1", "prompt_version": "P1"},
          "connected": True, "engaged": True, "qa": {"hallucination_flag": False}}
    import json
    assert store.aggregate(0, [json.dumps(ev)]) == 1
    assert store.get("last_chunk") == 0
    assert store.stats("foo", "all", 0, 0)["all"]["engaged"] == 1
    # A failed process can replay from its last durable chunk cursor.
    assert store.aggregate(1, [json.dumps(ev)]) == 1
    assert store.stats("foo", "all", 0, 0)["all"]["engaged"] == 2


def test_intent_idempotent(tmp_path):
    store = Store(tmp_path / "test.sqlite")
    store.intent("same-id", "wf", "approval", 1, {"x": 1}, 0)
    store.intent("same-id", "wf", "approval", 1, {"x": 1}, 0)
    assert len(store.active("wf")) == 1
    store.update_intent("same-id", "pending", "apr_1")
    assert store.active("wf")[0][3] == "apr_1"
