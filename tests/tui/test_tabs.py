"""Tests for ui.tui.tabs (headless)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, DataTable, Input, RadioSet, Select

from app_core import services
from tests.tui.conftest import pause, run_async, seed_basic_data
from ui.tui import modals, tabs
from ui.tui.app import PropertyManagerApp


def prep_app(monkeypatch, unlocked=True):
    from textual.screen import ModalScreen
    from textual.widgets import Static

    class AutoLoading(ModalScreen):
        def compose(self):
            yield Static("l")

        def on_mount(self):
            self.dismiss(None)

    monkeypatch.setattr("ui.tui.app.is_branding_configured", lambda: True)
    monkeypatch.setattr("ui.tui.app.get_loading_animation", lambda: "none")
    monkeypatch.setattr("ui.tui.app.get_app_display_name", lambda: "Test")
    monkeypatch.setattr("ui.tui.app.get_app_tagline", lambda: "")
    monkeypatch.setattr("ui.tui.app.get_gift_attribution", lambda: "G")
    monkeypatch.setattr("ui.tui.app.LoadingScreen", AutoLoading)
    monkeypatch.setattr(services, "verify_app_attribution", lambda: (True, ""))
    monkeypatch.setattr(services, "has_super_password", lambda: True)
    monkeypatch.setattr(services, "is_session_unlocked", lambda: unlocked)


def sync_worker(self, work, thread=False, **k):
    try:
        work()
    except Exception:
        pass
    return SimpleNamespace()


# ── helpers ────────────────────────────────────────────────────────────────

def test_tab_helpers(clean_db, monkeypatch):
    prep_app(monkeypatch)
    seed_basic_data()
    calls = []
    app = SimpleNamespace(notify=lambda *a, **k: calls.append(("n", a, k)),
                          refresh_all_tables=lambda: calls.append("r"),
                          push_screen=lambda modal, callback=None: callback(True))
    tabs.push_modal(app, object(), on_success=lambda: calls.append("s"))
    tabs.push_modal(app, object())
    app.push_screen = lambda modal, callback=None: callback(False)
    tabs.push_modal(app, object(), on_success=lambda: calls.append("no"))
    app.push_screen = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x"))
    tabs.push_modal(app, object())
    app.push_screen = lambda *a, **k: calls.append("err")
    tabs.show_error(app, "err")
    assert tabs._room_options()
    assert tabs._category_options() is not None


# ── FrontDesk ──────────────────────────────────────────────────────────────

def test_front_desk_tab(clean_db, monkeypatch):
    prep_app(monkeypatch)
    data = seed_basic_data()
    monkeypatch.setattr(PropertyManagerApp, "push_screen", lambda self, s, *a, **k: k.get("callback", lambda *x: None)(True))
    monkeypatch.setattr(services, "get_front_desk_summary", lambda: {
        "occupancy_pct": 50, "occupied": 1, "total": 2, "clean": 1, "dirty": 1, "maint": 0,
        "arrivals_today": 0, "departures_today": 0, "in_house": 1,
    })
    monkeypatch.setattr(services, "get_occupancy_for_window", lambda *a: {"pct": 1, "adr": 2, "revpar": 3})
    monkeypatch.setattr(services, "update_housekeeping_status", lambda *a, **k: None)

    async def _():
        async with PropertyManagerApp().run_test(size=(140, 45)) as pilot:
            await pause(pilot, 6)
            fd = pilot.app.query_one(tabs.FrontDeskTab)
            fd.refresh_data()
            await pause(pilot, 2)
            # mode switches
            fd.query_one("#mode", RadioSet)._pressed_button = fd.query_one("#m_avail")
            fd.query_one("#from", Input).value = data["today"]
            fd.query_one("#to", Input).value = data["tomorrow"]
            fd.refresh_data()
            fd.query_one("#mode", RadioSet)._pressed_button = fd.query_one("#m_room")
            fd.query_one("#room_sel", Select).value = data["room_id"]
            fd.refresh_data()
            # windows
            for b in ["#w_day", "#w_week", "#w_month", "#w_custom"]:
                try:
                    fd.query_one("#window", RadioSet)._pressed_button = fd.query_one(b)
                except Exception:
                    pass
                fd._window_range()
            # buttons
            fd.query_one("#room_table", DataTable).move_cursor(row=0)
            fd.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_clean")))
            fd.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_dirty")))
            fd.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_maint")))
            fd.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_apply")))
            fd.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_chart")))
            # edit path depends on cursor column
            tbl = fd.query_one("#room_table", DataTable)
            tbl.move_cursor(row=0, column=2)
            fd._update_edit_btn()
            fd.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_edit")))
            tbl.move_cursor(row=0, column=3)
            fd._update_edit_btn()
            fd.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_edit")))
            fd.on_data_table_cell_highlighted(SimpleNamespace(data_table=tbl))
            await pause(pilot, 2)

    run_async(_())


# ── Reservations ───────────────────────────────────────────────────────────

def test_reservations_tab(clean_db, monkeypatch):
    prep_app(monkeypatch)
    data = seed_basic_data()
    orig_push = PropertyManagerApp.push_screen
    monkeypatch.setattr(PropertyManagerApp, "push_screen", lambda self, s, *a, **k: k.get("callback", lambda *x: None)(True) if not isinstance(s, modals.ErrorModal) else orig_push(self, s, *a, **k))
    monkeypatch.setattr(services, "update_reservation_status", lambda *a, **k: None)
    monkeypatch.setattr(services, "checkout_requires_override", lambda rid: (False, 0, 0))
    monkeypatch.setattr(services, "checkout_reservation", lambda *a, **k: None)
    monkeypatch.setattr(services, "get_balance_breakdown", lambda rid: {"total_charges":10,"total_paid":5,"balance_due":5} if rid == data["rid"] else None)
    monkeypatch.setattr(services, "record_payment", lambda *a, **k: None)
    monkeypatch.setattr(services, "find_guest_matches", lambda *a, **k: [{"id":1,"name":"Alice Test","score":1.0,"stays":2}])
    monkeypatch.setattr(services, "create_reservation", lambda *a, **k: 999)

    async def _():
        async with PropertyManagerApp().run_test(size=(140, 45)) as pilot:
            await pause(pilot, 6)
            rt = pilot.app.query_one(tabs.ReservationsTab)
            rt.refresh_data()
            rt.query_one("#table", DataTable).move_cursor(row=0)
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_in")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_preview")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_folio")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_cancel")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_noshow")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_modify")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_delete")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_guests")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_chart")))
            rt._checkout = lambda: setattr(rt, "_co", True)
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_out")))
            # save validation + success
            rt.query_one("#guest", Input).value = ""
            rt._save_reservation()
            rt.query_one("#guest", Input).value = "New Guest"
            rt.query_one("#room", Select).value = data["room_id"]
            rt.query_one("#rate", Input).value = "100"
            rt._save_reservation()
            # guest hint paths
            rt.query_one("#guest", Input).value = "Alice"
            rt._check_guest()
            monkeypatch.setattr(services, "find_guest_matches", lambda *a, **k: [{"id":2,"name":"Similar","score":0.8,"stays":1}])
            rt._check_guest()
            monkeypatch.setattr(services, "find_guest_matches", lambda *a, **k: [])
            rt._check_guest()
            # balance/payment
            rt.query_one("#pay_rid", Input).value = str(data["rid"])
            rt._check_balance()
            rt.query_one("#pay_amt", Input).value = "5"
            rt.query_one("#pay_method", Select).value = "Cash"
            rt._record_payment()
            # checkout override path
            monkeypatch.setattr(services, "checkout_requires_override", lambda rid: (True, 50, 10))
            rt._checkout = tabs.ReservationsTab._checkout.__get__(rt, tabs.ReservationsTab)
            rt._do_checkout(data["rid"], override=True)
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_balance")))
            rt.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_pay")))
            await pause(pilot, 2)

    run_async(_())
# ── POS / Inventory ────────────────────────────────────────────────────────

def test_pos_inventory_tabs(clean_db, monkeypatch):
    prep_app(monkeypatch)
    data = seed_basic_data()
    monkeypatch.setattr(PropertyManagerApp, "push_screen", lambda self, s, *a, **k: k.get("callback", lambda *x: None)(2) or None)
    monkeypatch.setattr(services, "get_balance_breakdown", lambda rid: {"balance_due": 10})
    monkeypatch.setattr(services, "get_pos_items", lambda rid: [{"id":1,"item_description":"Cola","quantity":1,"unit_price":5.0,"tax_amount":0.5}])
    monkeypatch.setattr(services, "add_pos_item", lambda *a, **k: 1)
    monkeypatch.setattr(services, "complete_walkin_sale", lambda *a, **k: {"receipt_items":[], "total_gross":5.0, "method":"Cash", "receipt_text":"r", "receipt_path":"/tmp/r", "payment_id":1})
    monkeypatch.setattr(services, "calculate_tax", lambda amt, d: {"service":0,"vat":0,"city_tax":0,"gross":amt})
    monkeypatch.setattr(services, "restock_inventory", lambda *a, **k: None)

    async def _():
        async with PropertyManagerApp().run_test(size=(140, 45)) as pilot:
            await pause(pilot, 6)
            # NewOrderTab
            no = pilot.app.query_one(tabs.NewOrderTab)
            no.refresh_data()
            no.query_one("#rid", Input).value = str(data["rid"])
            no.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_load")))
            no._add_to_cart("Cola", 1, 5.0, data["inv_id"])
            no._refresh_cart()
            # custom item
            no.query_one("#custom_desc", Input).value = "X"
            no.query_one("#custom_qty", Input).value = "1"
            no.query_one("#custom_price", Input).value = "2"
            no.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_custom")))
            # walk-in mode
            try:
                no.query_one("#target", RadioSet)._pressed_button = no.query_one("#t_walk")
            except Exception:
                pass
            no.refresh_data()
            no._add_to_cart("Cola", 1, 5.0, data["inv_id"])
            no._pay()  # will fail validation? method select may be blank
            try:
                no.query_one("#pay_method", Select).value = "Cash"
            except Exception:
                pass
            monkeypatch.setattr(PropertyManagerApp, "push_screen", lambda self, s, *a, **k: None)
            no._pay()
            # grid selection add
            grid = no.query_one("#grid", DataTable)
            if grid.row_count:
                no.on_data_table_row_selected(SimpleNamespace(data_table=grid, cursor_row=0))
            no._add_from_grid("Cola", 1)
            # search/category events
            no.on_input_changed(SimpleNamespace(input=SimpleNamespace(id="search")))
            no.on_select_changed(SimpleNamespace(select=SimpleNamespace(id="cat")))
            # InventorySubTab
            inv = pilot.app.query_one(tabs.InventorySubTab)
            inv.refresh_data()
            inv.on_input_changed(SimpleNamespace(input=SimpleNamespace(id="search")))
            inv.on_select_changed(SimpleNamespace(select=SimpleNamespace(id="stock")))
            tbl = inv.query_one("#table", DataTable)
            if tbl.row_count:
                tbl.move_cursor(row=0)
                inv.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_edit")))
                inv.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_del")))
                inv.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_restock")))
                inv.query_one("#rs_qty", Input).value = "5"
                inv.query_one("#rs_cost", Input).value = "1"
                monkeypatch.setattr(services, "is_session_unlocked", lambda: True)
                inv._do_restock()
            inv.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_new")))
            # _finish_restock paths
            inv._finish_restock({"id": data["inv_id"], "item_name": "Cola"}, 1, 1, "recovery")
            inv._finish_restock({"id": data["inv_id"], "item_name": "Cola"}, 1, 1, False)
            monkeypatch.setattr(services, "restock_inventory", lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
            inv._finish_restock({"id": data["inv_id"], "item_name": "Cola"}, 1, 1, True)
            monkeypatch.setattr(services, "restock_inventory", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
            inv._finish_restock({"id": data["inv_id"], "item_name": "Cola"}, 1, 1, True)
            await pause(pilot, 2)

    run_async(_())


# ── Accounting ─────────────────────────────────────────────────────────────

def test_accounting_tab(clean_db, monkeypatch):
    prep_app(monkeypatch)
    seed_basic_data()
    orig_push = PropertyManagerApp.push_screen
    monkeypatch.setattr(PropertyManagerApp, "push_screen", lambda self, s, *a, **k: None)
    monkeypatch.setattr(PropertyManagerApp, "run_worker", sync_worker)
    monkeypatch.setattr(PropertyManagerApp, "call_from_thread", lambda self, fn: fn())
    monkeypatch.setattr(services, "get_general_ledger_summary", lambda: {"billed_revenue":10,"expenses":2,"net_profit":8,"cash_in":10,"cash_out":2,"ar_balance":0})
    monkeypatch.setattr(services, "get_recent_expenses", lambda n: [{"id":1,"at":"now","category_name":"C","parameter_name":"P","net_amount":1,"vat_amount":0,"method":"Cash","notes":""}])
    monkeypatch.setattr(services, "get_recent_ledger", lambda n: [{"id":1,"at":"now","entry_type":"E","category":"C","net":1,"vat":0,"gross":1,"description":"d"}])
    monkeypatch.setattr(services, "record_operational_expense", lambda *a, **k: 1)
    monkeypatch.setattr(services, "report_pl_statement", lambda *a, **k: "pl")
    monkeypatch.setattr(services, "report_cash_flow", lambda *a, **k: "cf")
    monkeypatch.setattr(services, "report_payment_methods", lambda *a, **k: "pm")
    monkeypatch.setattr(services, "report_vat", lambda *a, **k: "vat")
    monkeypatch.setattr(services, "report_daily_ops", lambda *a, **k: "ops")
    monkeypatch.setattr(services, "generate_unified_report", lambda *a, **k: {"datasets":["d"], "outputs":{"x":"y","z":["a"]}})

    async def _():
        async with PropertyManagerApp().run_test(size=(140, 45)) as pilot:
            await pause(pilot, 6)
            at = pilot.app.query_one(tabs.AccountingTab)
            at.refresh_data()
            for bid in ["btn_pl","btn_cash","btn_methods","btn_vat","btn_daily","btn_chart","btn_refresh","btn_apply","btn_refund","btn_void_exp","btn_analytics"]:
                try:
                    if bid == "btn_void_exp":
                        tbl = at.query_one("#exp_table", DataTable)
                        if tbl.row_count: tbl.move_cursor(row=0)
                    at.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id=bid)))
                except Exception:
                    pass
            # add expense validation/success
            cats = services.get_expense_categories()
            if cats:
                at.query_one("#exp_cat", Select).value = cats[0]["id"]
            at.query_one("#exp_name", Input).value = "Rent"
            at.query_one("#exp_amt", Input).value = "10"
            at.query_one("#exp_method", Select).value = "Cash"
            at.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_exp")))
            # worker error path
            monkeypatch.setattr(services, "generate_unified_report", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
            at.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_analytics")))
            await pause(pilot, 2)

    run_async(_())

# ── Settings ───────────────────────────────────────────────────────────────

def test_settings_tab(clean_db, monkeypatch):
    prep_app(monkeypatch, unlocked=False)
    monkeypatch.setattr(PropertyManagerApp, "push_screen", lambda self, s, *a, **k: k.get("callback", lambda *x: None)(True))
    monkeypatch.setattr(PropertyManagerApp, "run_worker", sync_worker)
    monkeypatch.setattr(PropertyManagerApp, "call_from_thread", lambda self, fn: fn())
    monkeypatch.setattr(PropertyManagerApp, "refresh", lambda self, *a, **k: None)
    monkeypatch.setattr(services, "verify_ledger_integrity", lambda: {"ok": True})
    monkeypatch.setattr(services, "export_settings_to_json", lambda: "/tmp/s.json")
    monkeypatch.setattr(services, "backup_now", lambda: "/tmp/b")
    monkeypatch.setattr(services, "list_printers", lambda: {"queues":["p1"], "default":"p1", "source":"x"})
    monkeypatch.setattr(services, "test_print", lambda: (True, "ok"))

    async def _():
        async with PropertyManagerApp().run_test(size=(140, 45)) as pilot:
            await pause(pilot, 6)
            st = pilot.app.query_one(tabs.SettingsTab)
            body = st.query_one("#body")
            for w in list(body.children):
                try:
                    await w.remove()
                except Exception:
                    pass
            await pause(pilot, 2)
            st._render_locked()
            await pause(pilot, 2)
            # unlock/render
            monkeypatch.setattr(services, "is_session_unlocked", lambda: True)
            await st._render_unlocked()
            await pause(pilot, 4)
            st._reload_rp()
            st._check_timeout()
            monkeypatch.setattr(services, "is_session_unlocked", lambda: False)
            st._check_timeout()
            await pause(pilot, 3)
            # render unlocked again for button tests
            monkeypatch.setattr(services, "is_session_unlocked", lambda: True)
            body = st.query_one("#body")
            for w in list(body.children):
                try:
                    await w.remove()
                except Exception:
                    pass
            await pause(pilot, 2)
            await st._render_unlocked()
            await pause(pilot, 4)
            async def press(bid):
                try:
                    await st.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id=bid)))
                except Exception:
                    pass
                try:
                    await pause(pilot, 1)
                except Exception:
                    pass
            await press("unlocked_btn_lock")
            body = st.query_one("#body")
            for w in list(body.children):
                try: await w.remove()
                except Exception: pass
            await pause(pilot, 1)
            monkeypatch.setattr(services, "is_session_unlocked", lambda: True)
            await st._render_unlocked(); await pause(pilot, 3)
            await press("unlocked_btn_save")  # no changes
            await press("unlocked_btn_export")
            await press("unlocked_btn_backup")
            await press("unlocked_btn_branding")
            orig_call_later = pilot.app.call_later
            monkeypatch.setattr(pilot.app, "call_later", lambda *a, **k: None)
            await press("unlocked_btn_reset_defaults")
            monkeypatch.setattr(pilot.app, "call_later", orig_call_later)
            await press("unlocked_btn_health")
            await press("unlocked_btn_demo_data")
            await press("unlocked_btn_audit")
            await press("unlocked_btn_import")
            await press("unlocked_btn_change_pw")
            await press("unlocked_btn_rp_add")
            rp = st.query_one("#unlocked_rp_table", DataTable)
            if rp.row_count:
                rp.move_cursor(row=0)
            await press("unlocked_btn_rp_edit")
            await press("unlocked_btn_rp_del")
            await press("unlocked_btn_pr_detect")
            await press("unlocked_btn_pr_test")
            # collect/save changes
            st._collect_changes()
            try:
                st.query_one("#unlocked_set_language", Select).value = "ar"
            except Exception:
                pass
            st._save_settings()
            st._after_save(True)
            st._after_unlock("recovery")
            st._after_unlock(True)
            await pause(pilot, 2)

    run_async(_())


# ── Assistant ──────────────────────────────────────────────────────────────

def test_assistant_tab(clean_db, monkeypatch):
    prep_app(monkeypatch, unlocked=True)
    monkeypatch.setattr(PropertyManagerApp, "push_screen", lambda self, s, *a, **k: k.get("callback", lambda *x: None)(True))
    monkeypatch.setattr(PropertyManagerApp, "run_worker", sync_worker)
    monkeypatch.setattr(PropertyManagerApp, "call_from_thread", lambda self, fn: fn())
    from app_core import assistant as assistant_backend
    monkeypatch.setattr(assistant_backend, "_llm_config", lambda: {"response_mode":"core","provider":"none","enabled":False,"model":""})
    monkeypatch.setattr(assistant_backend, "chat", lambda msg, history=None: {"ok":True,"source":"core","tools_used":["t"],"answer":"ok"})

    async def _():
        async with PropertyManagerApp().run_test(size=(140, 45)) as pilot:
            await pause(pilot, 6)
            atab = pilot.app.query_one(tabs.AssistantTab)
            atab._refresh_gate()
            atab.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="assistant_caps")))
            atab.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="assistant_q_0")))
            atab.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="assistant_clear")))
            atab.query_one("#assistant_input", Input).value = "hello"
            atab.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="assistant_send")))
            atab.on_input_submitted(SimpleNamespace(input=SimpleNamespace(id="assistant_input"), value="hi"))
            # locked
            monkeypatch.setattr(services, "is_session_unlocked", lambda: False)
            atab._refresh_gate()
            atab._ask("blocked")
            atab.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="assistant_unlock")))
            atab._after_unlock("recovery")
            atab._after_unlock(True)
            # worker error
            monkeypatch.setattr(services, "is_session_unlocked", lambda: True)
            atab._refresh_gate()
            monkeypatch.setattr(assistant_backend, "chat", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
            atab._ask("err")
            # llm enabled info
            monkeypatch.setattr(assistant_backend, "_llm_config", lambda: {"response_mode":"talk","provider":"x","enabled":True,"model":"m"})
            atab._refresh_gate()
            await pause(pilot, 2)

    run_async(_())
