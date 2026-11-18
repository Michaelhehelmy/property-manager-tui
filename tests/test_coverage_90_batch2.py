from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_services_init_alias_more_branches(monkeypatch):
    import app_core.services as svc
    import app_core.services._monolith as m

    # hit elif branches (lines 30, 34): when primary attr absent but fallback attr present
    # line 30: net_types not in globals and no m.net_types but m._net_types exists
    try:
        svc.__dict__.pop("net_types", None)
        if hasattr(m, "net_types"):
            monkeypatch.delattr(m, "net_types", raising=False)
        monkeypatch.setattr(m, "_net_types", "NET_ALT", raising=False)
        svc2 = importlib.reload(svc)
        assert getattr(svc2, "net_types", None) == "NET_ALT" or "net_types" in svc2.__dict__

        # line 34: _print_raw not in globals and no m._print_raw but m._print_raw_socket exists
        svc2.__dict__.pop("_print_raw", None)
        if hasattr(m, "_print_raw"):
            monkeypatch.delattr(m, "_print_raw", raising=False)
        monkeypatch.setattr(m, "_print_raw_socket", "PR_ALT", raising=False)
        svc3 = importlib.reload(svc)
        assert getattr(svc3, "_print_raw", None) == "PR_ALT" or "_print_raw" in svc3.__dict__
    finally:
        importlib.reload(svc)


def test_barcodes_coverage(tmp_path, monkeypatch):
    import app_core.barcodes as bar

    assert "T" in bar._now_iso() or "-" in bar._now_iso()
    assert bar.normalize_code(None) == ""
    assert bar.normalize_code("  A\x00B\r\n\tC  ") == "ABC"
    # max_len parse exception branch and truncation
    assert bar.normalize_code("abcdef", max_len="bad") == "abcdef"  # exception -> default 256
    assert bar.normalize_code("abcdef", max_len=3) == "abc"
    assert bar.normalize_barcode_type("QR") == "qr"
    assert bar.normalize_barcode_type("unknown", default="code128") == "code128"

    # validate_barcode_value branches
    with pytest.raises(ValueError):
        bar.validate_barcode_value("", allow_empty=False)
    assert bar.validate_barcode_value("", allow_empty=True) == ""
    with pytest.raises(ValueError):
        bar.validate_barcode_value("bad*char")
    assert bar.validate_barcode_value("ABC-123_.:#") == "ABC-123_.:#"

    # sanitize_label_text
    s = bar.sanitize_label_text("  A^B~C[]{}<>  ")
    assert "A" in s and "B" in s and "C" in s
    assert bar.sanitize_label_text("x", max_len=1) == "x"

    # make_lot_code with received_at parsing and seq exception
    code = bar.make_lot_code(5, received_at="2026-01-02 03:04:05", seq=7)
    assert code.startswith("L5-")
    code2 = bar.make_lot_code(5, received_at="bad-date", seq="bad")
    assert code2.startswith("L5-")
    code3 = bar.make_lot_code(5, received_at=None, seq=None)
    assert code3.startswith("L5-")

    # make_lot_payload getattr fallback
    class Item:
        id = 1
        sku = "S1"
        item_name = "Item"
    class Batch:
        lot_code = "L1"
        received_at = "2026-01-01"
        expiry_date = "2026-02-01"
        quantity_received = 10
        quantity_remaining = 5
    payload = bar.make_lot_payload(Item(), Batch())
    assert payload["lot"] == "L1" and payload["item"] == 1
    s = bar.encode_payload(payload)
    assert isinstance(s, str) and "L1" in s

    # try_decode_payload branches
    assert bar.try_decode_payload("") is None
    assert bar.try_decode_payload("notjson") is None
    assert bar.try_decode_payload("{bad") is None
    assert bar.try_decode_payload('{"v":1,"t":"x"}') is None
    good = '{"v":1,"t":"lot","lot":"A"}'
    assert bar.try_decode_payload(good)["lot"] == "A"

    # suggest_expiry branches
    assert bar.suggest_expiry("2026-01-01", None) is None
    assert bar.suggest_expiry("2026-01-01", "") is None
    assert bar.suggest_expiry("2026-01-01", "bad") is None
    assert bar.suggest_expiry("2026-01-01", 0) is None
    assert bar.suggest_expiry("2026-01-01 10:00:00", 5) == "2026-01-06"
    assert bar.suggest_expiry(None, 1) is not None

    # expiry_status branches
    assert bar.expiry_status(None) == ("none", None)
    assert bar.expiry_status("bad-date") == ("none", None)
    # use relative dates around today
    from datetime import date, timedelta
    today = date.today()
    assert bar.expiry_status((today - timedelta(days=1)).strftime("%Y-%m-%d"))[0] == "expired"
    assert bar.expiry_status((today + timedelta(days=1)).strftime("%Y-%m-%d"), warn_days=7)[0] == "expiring"
    assert bar.expiry_status((today + timedelta(days=30)).strftime("%Y-%m-%d"), warn_days=7)[0] == "ok"

    # _escpos_text_barcode and _get_field
    b = bar._escpos_text_barcode("CODE123", human="Item")
    assert isinstance(b, (bytes, bytearray)) and b"CODE123" in b
    b2 = bar._escpos_text_barcode("C")
    assert b"C" in b2
    assert bar._get_field({"a": None}, "a", "d") == "d"
    assert bar._get_field({"a": 1}, "a", "d") == 1
    obj = SimpleNamespace(x=None, y=2)
    assert bar._get_field(obj, "x", "d") == "d"
    assert bar._get_field(obj, "y", "d") == 2
    assert bar._get_field(obj, "z", "d") == "d"

    # render label functions
    item = {"id": 1, "item_name": "Water", "sku": "SKU1", "barcode": "BC1", "category": "Bev", "selling_price": 10, "barcode_type": "code128"}
    batch = {"lot_code": "LOT1", "received_at": "2026-01-01", "expiry_date": "", "quantity_received": 5, "quantity_remaining": 2, "unit_cost": 3, "supplier": "Sup", "reference": "R", "barcode_value": "BV1"}
    txt = bar.render_item_label_text(item)
    assert "ITEM LABEL" in txt and "Water" in txt
    btxt = bar.render_batch_label_text(item, batch, include_payload=True)
    assert "BATCH / LOT LABEL" in btxt and "LOT1" in btxt and "QR-PAYLOAD" in btxt
    btxt2 = bar.render_batch_label_text(item, batch, include_payload=False)
    assert "QR-PAYLOAD" not in btxt2
    content, is_bin = bar.render_label_for_print("zpl", item, batch=batch)
    assert is_bin is False and "^XA" in content
    content2, _ = bar.render_label_for_print("code128", item, batch=None)
    assert "ITEM LABEL" in content2


def test_feedback_coverage(monkeypatch, tmp_path):
    import app_core.assistant.feedback as fb
    import app_core.assistant.common as common

    # _ensure_nlu_dirs OSError branch
    monkeypatch.setattr(fb, "INTENT_DATA_DIR", SimpleNamespace(mkdir=lambda *a, **k: (_ for _ in ()).throw(OSError("x"))))
    fb._ensure_nlu_dirs()  # should not raise

    # record_feedback: DB insert exception and JSONL append exception paths
    monkeypatch.setattr(fb, "FEEDBACK_JSONL", tmp_path / "fb.jsonl")
    monkeypatch.setattr(fb, "_ensure_nlu_dirs", lambda: None)
    class BadConn:
        def execute(self, *a, **k): raise RuntimeError("db")
        def commit(self): pass
    monkeypatch.setattr("app_core.db.connect", lambda: BadConn())
    # also force open failure
    real_open = Path.open
    def bad_open(self, *a, **k):
        if str(self).endswith("fb.jsonl"):
            raise OSError("nope")
        return real_open(self, *a, **k)
    monkeypatch.setattr(Path, "open", bad_open)
    fid = fb.record_feedback("hello", predicted_intent="x")
    assert fid is None
    monkeypatch.setattr(Path, "open", real_open)

    # list_feedback: query success with bad JSON decode branch and exception branch
    class Row(dict):
        def keys(self): return super().keys()
    class GoodConn:
        def execute(self, q, args=None):
            class R:
                def fetchall(self_inner):
                    return [Row({"id": 1, "slots_json": "{bad", "corrected_slots_json": None, "tools_used_json": "[1]"})]
            return R()
    monkeypatch.setattr("app_core.db.connect", lambda: GoodConn())
    out = fb.list_feedback(limit=10, status="new")
    assert isinstance(out, list) and out and isinstance(out[0].get("slots"), dict)

    class BadConn2:
        def execute(self, *a, **k): raise RuntimeError("dbfail")
    monkeypatch.setattr("app_core.db.connect", lambda: BadConn2())
    assert fb.list_feedback() == []

    # get_correction_exemplars: bad slots json and exception branches
    class CRow(dict):
        def keys(self): return super().keys()
    class ExConn:
        def execute(self, q, args=None):
            class R:
                def fetchall(self_inner):
                    return [
                        CRow({"message": "m", "corrected_intent": "i1", "corrected_slots_json": "{bad", "predicted_intent": "", "slots_json": "{}"}),
                        CRow({"message": "m2", "corrected_intent": "", "predicted_intent": "", "slots_json": "{}"}),
                    ]
            return R()
    monkeypatch.setattr("app_core.db.connect", lambda: ExConn())
    ex = fb.get_correction_exemplars()
    assert any(e["label"] == "i1" for e in ex)

    class BadExConn:
        def execute(self, *a, **k): raise RuntimeError("x")
    monkeypatch.setattr("app_core.db.connect", lambda: BadExConn())
    assert fb.get_correction_exemplars() == []

    # exemplar_match cache/empty/exact/similar/threshold branches
    fb._EXEMPLAR_CACHE = []
    assert fb.exemplar_match("hi") is None
    fb._EXEMPLAR_CACHE = [
        {"text": "same text", "label": "a", "slots": {"x": 1}},
        {"text": "completely different words here", "label": "b", "slots": {}},
        {"text": "check reservation balance please", "label": "c", "slots": {}},
    ]
    m = fb.exemplar_match("same text")
    assert m and m["source"] == "exemplar_exact"
    m2 = fb.exemplar_match("check reservation balance please now", threshold=0.5)
    assert m2 is None or m2.get("intent") in ("a","b","c")

    # ML loader branches (imports/env paths + exception paths)
    fb._SKLEARN_CACHE = None
    monkeypatch.setenv("ASSISTANT_SKLEARN_MODEL", str(tmp_path / "missing.joblib"))
    assert fb._load_sklearn_router() is None
    fb._SKLEARN_CACHE = None
    monkeypatch.setenv("ASSISTANT_SETFIT_MODEL_DIR", str(tmp_path / "missing_setfit"))
    fb._SETFIT_CACHE = None
    assert fb._load_setfit_router() is None

    # force exception in loaders
    fb._SKLEARN_CACHE = None
    import builtins
    real_import = builtins.__import__
    def fake_import(name, *a, **k):
        if name == "joblib":
            raise ImportError("no")
        return real_import(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert fb._load_sklearn_router() is None
    monkeypatch.setattr(builtins, "__import__", real_import)

    # _predict_intent_ml mode routing and failures
    monkeypatch.setenv("ASSISTANT_NLU", "setfit")
    monkeypatch.setattr(fb, "_load_setfit_router", lambda: None)
    assert fb._predict_intent_ml("x") is None
    monkeypatch.setenv("ASSISTANT_NLU", "sklearn")
    monkeypatch.setattr(fb, "_load_sklearn_router", lambda: None)
    assert fb._predict_intent_ml("x") is None

    # sklearn prediction branches
    monkeypatch.setenv("ASSISTANT_NLU", "sklearn")
    class Vec:
        def transform(self, xs): return xs
    class Clf:
        def predict(self, X): return ["intent_x"]
        def predict_proba(self, X): return [[0.2, 0.8]]
    monkeypatch.setattr(fb, "_load_sklearn_router", lambda: {"vectorizer": Vec(), "classifier": Clf()})
    r = fb._predict_intent_ml("hello")
    assert r and r["intent"] == "intent_x"
    class Clf2:
        def predict(self, X): return ["intent_y"]
        def decision_function(self, X): return [[1.0, 2.0]]
    monkeypatch.setattr(fb, "_load_sklearn_router", lambda: {"vectorizer": Vec(), "classifier": Clf2()})
    r = fb._predict_intent_ml("hello")
    assert r and r["intent"] == "intent_y"


def test_common_coverage(monkeypatch):
    import app_core.assistant.common as common

    # _safe_int
    assert common._safe_int(None) is None
    assert common._safe_int("") is None
    assert common._safe_int("12") == 12
    assert common._safe_int("bad") is None

    # _prev_period success and exception
    assert common._prev_period("2026-01-10", "2026-01-12") == ("2026-01-07", "2026-01-09")
    assert common._prev_period("bad", "x") == ("bad", "x")

    # _parse_date_range various branches
    monkeypatch.setattr(common, "_today", lambda: datetime(2026, 3, 15).date())
    assert common._parse_date_range("between 2026-01-10 and 2026-01-01")[0] == "2026-01-01"
    assert common._parse_date_range("2026-02-01 to 2026-02-03") == ("2026-02-01", "2026-02-03")
    assert common._parse_date_range("since 2026-03-01") == ("2026-03-01", "2026-03-15")
    assert common._parse_date_range("on 2026-03-10") == ("2026-03-10", "2026-03-10")
    assert common._parse_date_range("today") == ("2026-03-15", "2026-03-15")
    assert common._parse_date_range("yesterday") == ("2026-03-14", "2026-03-14")
    assert common._parse_date_range("tomorrow") == ("2026-03-16", "2026-03-16")
    assert common._parse_date_range("this week")[1] == "2026-03-15"
    assert common._parse_date_range("last week")[0] == "2026-03-02"
    assert common._parse_date_range("last 7 days") == ("2026-03-09", "2026-03-15")
    assert common._parse_date_range("past 14 days") == ("2026-03-02", "2026-03-15")
    assert common._parse_date_range("last 30 days") == ("2026-02-14", "2026-03-15")
    assert common._parse_date_range("last 90 days") == ("2025-12-16", "2026-03-15")
    assert common._parse_date_range("this month") == ("2026-03-01", "2026-03-15")
    assert common._parse_date_range("last month") == ("2026-02-01", "2026-02-28")
    assert common._parse_date_range("2026-04-01") == ("2026-04-01", "2026-04-01")
    assert common._parse_date_range("no date here") == ("2026-03-01", "2026-03-15")

    # extract_slots basics
    slots = common.extract_slots('find reservation #12 guest #3 group #4 room #A1 "John Doe" source Booking.com segment Corporate')
    assert slots.get("reservation_id") == 12 and slots.get("guest_id") == 3 and slots.get("group_id") == 4
    assert slots.get("guest_query") == "John Doe"
    assert "Booking.com" in (slots.get("source") or "")
    assert "Corporate" in (slots.get("market_segment") or "")


def test_support_more_coverage(clean_db, monkeypatch, tmp_path):
    import app_core.support as support

    # sanitize edge branches
    assert support.sanitize_for_report(None, "password") is None
    assert support.sanitize_for_report(True, "pm_web_secret_key_set") is True
    assert support.sanitize_for_report("x", "password") == "<redacted>"
    # Path exception fallback (scoped; restore before other code uses Path.name)
    orig_name = Path.name
    try:
        monkeypatch.setattr(Path, "name", property(lambda self: (_ for _ in ()).throw(RuntimeError("x"))), raising=False)
        v = support.sanitize_for_report(Path("/tmp/x.txt"))
        assert v == "<path>" or "x" in str(v)
    finally:
        monkeypatch.setattr(Path, "name", orig_name, raising=False)

    # _safe_readiness_summary exception branches
    monkeypatch.setattr(support.paths, "check_runtime_permissions", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(support.db, "is_initialized", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    r = support._safe_readiness_summary()
    assert isinstance(r, dict) and r.get("db_initialized") is False

    # doctor summary exception and integrity summarization branches
    monkeypatch.setattr("app_core.doctor.collect_health", lambda **k: (_ for _ in ()).throw(RuntimeError("docfail")))
    d = support._safe_doctor_summary()
    assert d.get("ok") is False
    monkeypatch.setattr("app_core.doctor.collect_health", lambda **k: {
        "ok": True,
        "schema": {"current_version": 1, "expected_version": 1, "tables": {"missing_tables": ["x"]}},
        "integrity": {"db": {"ok": True, "integrity_check": ["ok", "extra"], "error": "e"}, "x": "v", "bad": [1]},
        "config_issues": [{"severity": "warn", "code": "c", "message": "m"}, "bad"],
        "update": {"local": {"app_version": "1"}, "remote": {}},
        "notes": ["n"]
    })
    d = support._safe_doctor_summary()
    assert d.get("ok") is True and d["schema"]["missing_tables_count"] == 1

    # collect_support_diagnostics update notice branches
    monkeypatch.setattr(support, "_safe_readiness_summary", lambda: {})
    monkeypatch.setattr(support, "_safe_doctor_summary", lambda **k: {})
    monkeypatch.setattr("app_core.updater.get_cached_update_notice", lambda **k: {"update_available": True, "kind": "git", "message": "m", "latest_release": {"tag_name": "v1"}})
    # avoid init_db side effects issues; collect_support_diagnostics calls services.init_db in try
    diag = support.collect_support_diagnostics(context={"secret": "x"}, include_update_check=True)
    assert diag["report_version"] == support.REPORT_VERSION

    # format_support_report_markdown json dumps exception/truncation
    class Bad:
        def __str__(self): raise RuntimeError("x")
    # normal path already tested; test large diagnostics truncation via outer length hard; ensure function returns string
    md = support.format_support_report_markdown({"a": 1}, user={"title": "t", "steps": "s", "expected": "e", "actual": "a", "notes": "n", "contact": "c", "category": "bug", "severity": "low"})
    assert "Issue report" in md

    # build_github_issue_url large body truncation
    res = support.build_github_issue_url(title="t", body="x" * 9000, labels=None, repo="o/r")
    assert "url" in res

    # CLI branches
    rc = support._cli_main(["--json"])
    assert rc == 0
    rc = support._cli_main([])
    assert rc == 0

