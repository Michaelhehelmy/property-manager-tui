"""Tests for ui.tui.modals (headless)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button, DataTable, Input, RichLog, Select, Static

from app_core import services
from tests.tui.conftest import pause, run_async, seed_basic_data, tui_settings
from ui.tui import modals


def app_for_modals():
    from ui.tui.app import PropertyManagerApp

    return PropertyManagerApp


def prep_modal_app(monkeypatch, **kw):
    from textual.screen import ModalScreen
    from textual.widgets import Static

    class AutoLoading(ModalScreen):
        def compose(self):
            yield Static("loading")

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
    monkeypatch.setattr(services, "is_session_unlocked", lambda: kw.get("unlocked", True))


async def dismiss_top(pilot, key="escape"):
    await pilot.press(key)
    await pause(pilot, 3)


# ── Base ───────────────────────────────────────────────────────────────────

def test_error_modal(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            pilot.app.push_screen(modals.ErrorModal("boom", "T"))
            await pause(pilot, 3)
            await pilot.click("#btn_ok")
            await pause(pilot, 2)
            pilot.app.push_screen(modals.ErrorModal("x"))
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)

    run_async(_())


def test_confirm_modal(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)

    async def _():
        res = {}
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            pilot.app.push_screen(modals.ConfirmModal("sure?"), callback=lambda v: res.setdefault("a", v))
            await pause(pilot, 4)
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 4)
            pilot.app.push_screen(modals.ConfirmModal("sure?"), callback=lambda v: res.setdefault("b", v))
            await pause(pilot, 4)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 4)
            pilot.app.push_screen(modals.ConfirmModal("sure?"), callback=lambda v: res.setdefault("c", v))
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
        assert res.get("a") is True and res.get("b") is False and res.get("c") is False

    run_async(_())


def test_protected_modal_paths(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)

    class PM(modals.ProtectedModal):
        TITLE = "T"
        CONFIRM_LABEL = "Go"
        CONFIRM_VARIANT = "warning"

        def body(self):
            yield Static("body")

        def perform(self, password):
            if password == "badperm":
                raise PermissionError("no")
            if password == "boom":
                raise RuntimeError("boom")
            self.did = password

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            m = PM()
            pilot.app.push_screen(m)
            await pause(pilot, 4)
            pilot.app.screen.query_one("#pw_field", Input).value = "ok"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 4)
            assert getattr(m, "did", None) == "ok"

            pilot.app.push_screen(PM())
            await pause(pilot, 4)
            pilot.app.screen.query_one("#pw_field", Input).value = "badperm"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 4)
            await pilot.press("escape")
            await pause(pilot, 2)

            pilot.app.push_screen(PM())
            await pause(pilot, 4)
            pilot.app.screen.query_one("#pw_field", Input).value = "boom"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 4)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 3)

    run_async(_())


# ── Password gate ──────────────────────────────────────────────────────────

def test_unlock_settings_modal(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    monkeypatch.setattr(services, "unlock_session", lambda p: p == "ok")

    async def _():
        res = {}
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            pilot.app.push_screen(modals.UnlockSettingsModal(), lambda v: res.setdefault("a", v))
            await pause(pilot, 3)
            s = pilot.app.screen
            s.query_one("#pw", Input).value = "bad"
            await pause(pilot, 1)
            s.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            s = pilot.app.screen
            if s.query("#pw"):
                s.query_one("#pw", Input).value = "ok"
                await pause(pilot, 1)
                s.query_one("#btn_do", Button).press()
                await pause(pilot, 4)
            pilot.app.push_screen(modals.UnlockSettingsModal(), lambda v: res.setdefault("b", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_recovery", Button).press()
            await pause(pilot, 3)
            pilot.app.push_screen(modals.UnlockSettingsModal(), lambda v: res.setdefault("c", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 3)
        assert res == {"a": True, "b": "recovery", "c": False}

    run_async(_())


def test_recovery_reset_modal(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)

    async def _():
        res = {}
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            monkeypatch.setattr(services, "use_recovery_code", lambda *a: None)
            monkeypatch.setattr(services, "unlock_session", lambda p: False)
            monkeypatch.setattr(services, "_session", {"unlocked": False, "last_activity": None})
            pilot.app.push_screen(modals.RecoveryResetModal(), lambda v: res.setdefault("a", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#code", Input).value = "c"
            pilot.app.screen.query_one("#pw_new", Input).value = "newpass"
            pilot.app.screen.query_one("#pw_cf", Input).value = "newpass"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 4)

            monkeypatch.setattr(services, "unlock_session", lambda p: True)
            pilot.app.push_screen(modals.RecoveryResetModal(), lambda v: res.setdefault("b", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#code", Input).value = "c"
            pilot.app.screen.query_one("#pw_new", Input).value = "newpass"
            pilot.app.screen.query_one("#pw_cf", Input).value = "newpass"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 4)

            monkeypatch.setattr(services, "use_recovery_code", lambda *a: (_ for _ in ()).throw(ValueError("bad")))
            pilot.app.push_screen(modals.RecoveryResetModal(), lambda v: res.setdefault("c", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 3)
        assert res.get("a") is True and res.get("b") is True and res.get("c") is False

    run_async(_())


def test_set_super_password_and_recovery_codes(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)

    async def _():
        res = {}
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            monkeypatch.setattr(services, "set_super_password", lambda p: None)
            monkeypatch.setattr(services, "generate_recovery_codes", lambda p: ["A", "B"])
            pilot.app.push_screen(modals.SetSuperPasswordScreen(), lambda v: res.setdefault("a", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw_new", Input).value = "abc123"
            pilot.app.screen.query_one("#pw_confirm", Input).value = "nope"
            pilot.app.screen.query_one("#btn_set", Button).press()
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw_confirm", Input).value = "abc123"
            pilot.app.screen.query_one("#btn_set", Button).press()
            await pause(pilot, 5)
            # recovery codes screen should be on top
            try:
                pilot.app.screen.query_one("#btn_copy", Button).press()
                await pause(pilot, 2)
            except Exception:
                pass
            try:
                pilot.app.screen.query_one("#btn_close", Button).press()
                await pause(pilot, 3)
            except Exception:
                try:
                    await pilot.press("escape")
                    await pause(pilot, 2)
                except Exception:
                    pass

            monkeypatch.setattr(services, "set_super_password", lambda p: (_ for _ in ()).throw(ValueError("x")))
            pilot.app.push_screen(modals.SetSuperPasswordScreen(), lambda v: res.setdefault("b", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw_new", Input).value = "abc123"
            pilot.app.screen.query_one("#pw_confirm", Input).value = "abc123"
            pilot.app.screen.query_one("#btn_set", Button).press()
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_quit", Button).press()
            await pause(pilot, 3)
        assert res.get("b") is False

    run_async(_())


def test_recovery_codes_clipboard_error(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            pilot.app.push_screen(modals.RecoveryCodesScreen(["X"]))
            await pause(pilot, 2)
            monkeypatch.setattr(pilot.app, "copy_to_clipboard", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no")))
            await pilot.click("#btn_copy")
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)

    run_async(_())


def test_change_password_and_reset_defaults(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    monkeypatch.setattr(services, "change_password", lambda *a: None)
    monkeypatch.setattr(services, "reset_to_defaults", lambda p: None)

    async def _():
        res = {}
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            pilot.app.push_screen(modals.ChangePasswordModal(), lambda v: res.setdefault("a", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            monkeypatch.setattr(services, "change_password", lambda *a: (_ for _ in ()).throw(ValueError("x")))
            pilot.app.push_screen(modals.ChangePasswordModal(), lambda v: res.setdefault("b", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 3)

            pilot.app.push_screen(modals.ResetDefaultsModal(), lambda v: res.setdefault("c", v))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw_field", Input).value = "testpass"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 4)
        assert res.get("a") is True and res.get("b") is False and res.get("c") is True

    run_async(_())
# ── Reservation actions ────────────────────────────────────────────────────

def test_reservation_action_modals(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    data = seed_basic_data()
    monkeypatch.setattr(services, "delete_reservation", lambda *a: None)
    monkeypatch.setattr(services, "modify_reservation", lambda *a: None)
    monkeypatch.setattr(services, "void_pos_item", lambda *a: None)
    monkeypatch.setattr(services, "estimate_cancellation_outcome", lambda *a: {"policy":"x","total_paid":10,"estimated_refund":5,"kept_amount":5})
    monkeypatch.setattr(services, "cancel_reservation", lambda *a: None)
    monkeypatch.setattr(services, "mark_no_show", lambda *a: None)
    monkeypatch.setattr(services, "get_balance_breakdown", lambda rid: {"total_paid": 50})
    monkeypatch.setattr(services, "record_refund", lambda *a: None)
    monkeypatch.setattr(services, "checkout_reservation", lambda *a, **k: None)

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            rid = data["rid"]
            rooms = services.get_all_rooms()
            # Delete
            pilot.app.push_screen(modals.DeleteReservationScreen(rid, "A", "d"))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw_field", Input).value = "testpass"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            # Modify - no room selected then valid
            pilot.app.push_screen(modals.ModifyReservationScreen(rid, "100", "2026-01-01", "2026-01-02", rooms[0]["id"]))
            await pause(pilot, 3)
            s = pilot.app.screen
            try:
                s.query_one("#room", Select).clear()
            except Exception:
                pass
            s.query_one("#pw_field", Input).value = "p"
            s.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            s = pilot.app.screen
            if s.query("#room"):
                s.query_one("#room", Select).value = rooms[0]["id"]
                s.query_one("#pw_field", Input).value = "p"
                s.query_one("#btn_do", Button).press()
                await pause(pilot, 3)
            else:
                # if dismissed unexpectedly, still cover valid path
                pilot.app.push_screen(modals.ModifyReservationScreen(rid, "100", "2026-01-01", "2026-01-02", rooms[0]["id"]))
                await pause(pilot, 3)
                pilot.app.screen.query_one("#room", Select).value = rooms[0]["id"]
                pilot.app.screen.query_one("#pw_field", Input).value = "p"
                pilot.app.screen.query_one("#btn_do", Button).press()
                await pause(pilot, 3)
            # Void item
            pilot.app.push_screen(modals.VoidItemScreen(1))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            # Cancel / no-show
            for mode in ("cancel", "no_show"):
                pilot.app.push_screen(modals.CancelConfirmScreen(rid, mode))
                await pause(pilot, 2)
                pilot.app.screen.query_one("#pw_field", Input).value = "p"
                pilot.app.screen.query_one("#btn_do", Button).press()
                await pause(pilot, 3)
            # estimate returns empty/None-ish
            monkeypatch.setattr(services, "estimate_cancellation_outcome", lambda *a: None)
            pilot.app.push_screen(modals.CancelConfirmScreen(rid, "cancel"))
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
            monkeypatch.setattr(services, "estimate_cancellation_outcome", lambda *a: {"policy":"x","total_paid":10,"estimated_refund":5,"kept_amount":5})
            # Refund
            pilot.app.push_screen(modals.RefundScreen(rid))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#amount", Input).value = "10"
            pilot.app.screen.query_one("#method", Select).value = "Cash"
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            pilot.app.push_screen(modals.RefundScreen())
            await pause(pilot, 2)
            pilot.app.screen.query_one("#rid", Input).value = str(rid)
            pilot.app.screen.query_one("#amount", Input).value = "1"
            try:
                pilot.app.screen.query_one("#method", Select).value = 999
            except Exception:
                pass
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
            # Override checkout
            pilot.app.push_screen(modals.OverrideCheckoutModal(rid, 50.0))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)

    run_async(_())


# ── Inventory / qty / expense / rooms / rate plans ─────────────────────────

def test_inventory_room_rate_guest_modals(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    data = seed_basic_data()
    monkeypatch.setattr(services, "add_inventory_item", lambda *a, **k: 99)
    monkeypatch.setattr(services, "update_inventory_item", lambda *a, **k: None)
    monkeypatch.setattr(services, "delete_inventory_item", lambda *a, **k: None)
    monkeypatch.setattr(services, "void_expense", lambda *a, **k: None)
    monkeypatch.setattr(services, "get_room", lambda rid: {"room_number":"101","room_type":"Std","base_price":100})
    monkeypatch.setattr(services, "update_room_type", lambda *a, **k: None)
    monkeypatch.setattr(services, "update_room_price", lambda *a, **k: None)
    monkeypatch.setattr(services, "get_all_rate_plans", lambda: [{"id":1,"name":"Std","price_modifier":0}])
    monkeypatch.setattr(services, "add_rate_plan", lambda *a, **k: None)
    monkeypatch.setattr(services, "update_rate_plan", lambda *a, **k: None)
    monkeypatch.setattr(services, "delete_rate_plan", lambda *a, **k: None)
    monkeypatch.setattr(services, "count_reservations_using_plan", lambda pid: 0)

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            # Inventory add/edit
            pilot.app.push_screen(modals.InventoryEditScreen("add"))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#name", Input).value = "X"
            pilot.app.screen.query_one("#cat", Input).value = "C"
            pilot.app.screen.query_one("#unit", Input).value = "u"
            pilot.app.screen.query_one("#pw", Input).value = "testpass"
            pilot.app.screen.query_one("#btn_save", Button).press()
            await pause(pilot, 3)
            item = {"id":1,"item_name":"X","category":"C","unit":"u","stock_qty":1,"unit_cost":1,"selling_price":2,"low_stock_threshold":1,"overstock_threshold":9}
            pilot.app.push_screen(modals.InventoryEditScreen("edit", item))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw", Input).value = "testpass"
            pilot.app.screen.query_one("#btn_save", Button).press()
            await pause(pilot, 3)
            # permission/value errors
            monkeypatch.setattr(services, "add_inventory_item", lambda *a, **k: (_ for _ in ()).throw(PermissionError("x")))
            pilot.app.push_screen(modals.InventoryEditScreen("add"))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#name", Input).value = "X"
            pilot.app.screen.query_one("#cat", Input).value = "C"
            pilot.app.screen.query_one("#unit", Input).value = "u"
            pilot.app.screen.query_one("#pw", Input).value = "bad"
            pilot.app.screen.query_one("#btn_save", Button).press()
            await pause(pilot, 2)
            monkeypatch.setattr(services, "add_inventory_item", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
            pilot.app.screen.query_one("#btn_save", Button).press()
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 2)
            # delete
            pilot.app.push_screen(modals.InventoryDeleteScreen(1, "X"))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            # qty input
            res = {}
            pilot.app.push_screen(modals.QtyInputScreen("Cola"), lambda v: res.setdefault("a", v))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#qty", Input).value = "2"
            pilot.app.screen.query_one("#btn_ok", Button).press()
            await pause(pilot, 2)
            pilot.app.push_screen(modals.QtyInputScreen("Cola"), lambda v: res.setdefault("b", v))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#qty", Input).value = "-1"
            pilot.app.screen.query_one("#btn_ok", Button).press()
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 2)
            assert res.get("a") == 2 and res.get("b") is None
            # void expense
            pilot.app.push_screen(modals.VoidExpenseScreen(1))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            # edit room type/price
            room_id = data["room_id"]
            pilot.app.push_screen(modals.EditRoomFieldScreen(room_id, "type"))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#val", Input).value = "Deluxe"
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            pilot.app.push_screen(modals.EditRoomFieldScreen(room_id, "price"))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#val", Input).value = "150"
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            # rate plans
            pilot.app.push_screen(modals.RatePlanEditScreen())
            await pause(pilot, 2)
            pilot.app.screen.query_one("#name", Input).value = "N"
            pilot.app.screen.query_one("#mod", Input).value = "5"
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            pilot.app.push_screen(modals.RatePlanEditScreen(1))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            monkeypatch.setattr(services, "count_reservations_using_plan", lambda pid: (_ for _ in ()).throw(RuntimeError("x")))
            pilot.app.push_screen(modals.RatePlanDeleteScreen(1, "Std"))
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
            monkeypatch.setattr(services, "count_reservations_using_plan", lambda pid: 2)
            pilot.app.push_screen(modals.RatePlanDeleteScreen(1, "Std"))
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
            monkeypatch.setattr(services, "count_reservations_using_plan", lambda pid: 0)
            pilot.app.push_screen(modals.RatePlanDeleteScreen(1, "Std"))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)

    run_async(_())

# ── Guests ─────────────────────────────────────────────────────────────────

def test_guest_and_history_modals(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    data = seed_basic_data()
    g = services.get_guest_by_id(data["guest_id"])
    monkeypatch.setattr(services, "get_guest_by_id", lambda gid: g if gid == data["guest_id"] else None)
    monkeypatch.setattr(services, "update_guest", lambda *a, **k: None)
    monkeypatch.setattr(services, "merge_guests", lambda *a, **k: 3)
    monkeypatch.setattr(services, "get_guest_history_by_id", lambda gid: [
        {"id":1,"room_number":"101","check_in_date":"2026-01-01","check_out_date":"2026-01-02","status":"Checked Out","agreed_nightly_rate":100.0,"tax_amount":5.0}
    ])

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            pilot.app.push_screen(modals.GuestEditScreen(g))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            # merge - selections invalid
            pilot.app.push_screen(modals.GuestMergeModal())
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
            # merge valid
            guests = services.get_all_guests()
            if len(guests) >= 2:
                pilot.app.push_screen(modals.GuestMergeModal())
                await pause(pilot, 3)
                pilot.app.screen.query_one("#winner", Select).value = guests[0]["id"]
                pilot.app.screen.query_one("#loser", Select).value = guests[1]["id"]
                pilot.app.screen.query_one("#pw_field", Input).value = "p"
                pilot.app.screen.query_one("#btn_do", Button).press()
                await pause(pilot, 3)
            # history
            pilot.app.push_screen(modals.GuestHistoryModal(data["guest_id"]))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_close", Button).press()
            await pause(pilot, 2)

    run_async(_())


def test_guest_crm_modal(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    data = seed_basic_data()
    monkeypatch.setattr(services, "get_duplicate_suggestions", lambda: [{"name_a":"A","name_b":"B","score":90}])

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            # patch push_screen on app for sub-modals
            pushed = []
            orig = pilot.app.push_screen
            def cap(screen, *a, **k):
                pushed.append(screen)
                cb = k.get("callback")
                if cb: cb(False)
            monkeypatch.setattr(pilot.app, "push_screen", cap)
            crm = modals.GuestCRMModal()
            orig(crm)
            await pause(pilot, 4)
            crm.query_one("#search", Input).value = "Ali"
            crm.on_input_changed(SimpleNamespace(input=SimpleNamespace(id="search")))
            await pause(pilot, 2)
            crm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_refresh")))
            await pause(pilot, 2)
            monkeypatch.setattr(services, "get_duplicate_suggestions", lambda: [])
            crm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_refresh")))
            monkeypatch.setattr(services, "get_duplicate_suggestions", lambda: (_ for _ in ()).throw(RuntimeError("x")))
            crm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_refresh")))
            # select row if any
            table = crm.query_one("#table", DataTable)
            if table.row_count:
                table.move_cursor(row=0)
            crm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_hist")))
            crm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_edit")))
            crm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_merge")))
            crm.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_close")))
            await pause(pilot, 2)

    run_async(_())


# ── Invoice / receipt / report / chart / folio ─────────────────────────────

def test_invoice_and_receipt_modals(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    data = seed_basic_data()
    monkeypatch.setattr(services, "generate_invoice_text", lambda *a, **k: "INV\nline2")
    monkeypatch.setattr(services, "save_invoice_file", lambda *a, **k: "/tmp/inv.txt")
    monkeypatch.setattr(services, "print_to_printer", lambda p: (True, "ok"))

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            pilot.app.push_screen(modals.InvoiceScreen(data["rid"], save=True))
            await pause(pilot, 4)
            pilot.app.screen.query_one("#btn_cur", Button).press()
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_print", Button).press()
            await pause(pilot, 2)
            monkeypatch.setattr(services, "print_to_printer", lambda p: (_ for _ in ()).throw(RuntimeError("x")))
            pilot.app.screen.query_one("#btn_print", Button).press()
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_close", Button).press()
            await pause(pilot, 2)
            # walk-in provided
            pilot.app.push_screen(modals.WalkInReceiptScreen([], 0, "Cash", receipt_text="R\n1", receipt_path="/tmp/r", payment_id=1))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#btn_print", Button).press()
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
            # generated + fallback
            monkeypatch.setattr(services, "generate_walkin_receipt_text", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
            monkeypatch.setattr(services, "get_app_display_name", lambda: "X")
            monkeypatch.setattr(services, "save_walkin_receipt_file", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
            monkeypatch.setattr(services, "save_invoice_file", lambda *a, **k: "/tmp/f")
            pilot.app.push_screen(modals.WalkInReceiptScreen([{"desc":"a","qty":1,"price":1.0,"gross":1.0}], 1.0, "Cash"))
            await pause(pilot, 3)
            await pilot.press("escape")
            await pause(pilot, 2)

    run_async(_())


def test_report_chart_folio(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    data = seed_basic_data()
    monkeypatch.setattr(services, "get_pos_items", lambda rid: [{"id":1,"item_description":"x","quantity":1,"unit_price":5.0,"tax_amount":0.5}])
    monkeypatch.setattr(services, "get_daily_revenue_expense_series", lambda *a: [("2026-01-01", 10, 2)])
    monkeypatch.setattr(services, "get_daily_profit_series", lambda *a: [("2026-01-01", 8)])
    monkeypatch.setattr(services, "get_payment_method_dict", lambda *a: {"Cash": 10})
    monkeypatch.setattr(services, "get_daily_occupancy_series", lambda *a: [("2026-01-01", 50)])
    monkeypatch.setattr(services, "get_occupancy_heatmap_data", lambda *a: {"matrix": [[1,0],[0,1]]})

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            pilot.app.push_screen(modals.ReportScreen("T", "a\nb"))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_close", Button).press()
            await pause(pilot, 2)
            # charts
            for ctype in ["revenue","profit","payments","occupancy","heatmap"]:
                pilot.app.push_screen(modals.ChartScreen(ctype))
                await pause(pilot, 4)
                await pilot.press("escape")
                await pause(pilot, 2)
            # no-data branches
            monkeypatch.setattr(services, "get_daily_revenue_expense_series", lambda *a: [])
            pilot.app.push_screen(modals.ChartScreen("revenue"))
            await pause(pilot, 3)
            try:
                pilot.app.screen.query_one("#type", Select).value = "bogus"
                pilot.app.screen.on_select_changed(SimpleNamespace(select=SimpleNamespace(id="type"), value="bogus"))
            except Exception:
                pass
            await pilot.press("escape")
            await pause(pilot, 2)
            # folio
            pushed = []
            orig = pilot.app.push_screen
            monkeypatch.setattr(pilot.app, "push_screen", lambda s,*a,**k: (pushed.append(s), k.get("callback", lambda x:None)(True)))
            monkeypatch.setattr(pilot.app, "refresh_all_tables", lambda: None)
            fol = modals.ViewFolioScreen(data["rid"])
            orig(fol)
            await pause(pilot, 3)
            t = fol.query_one("#table", DataTable)
            if t.row_count: t.move_cursor(row=0)
            fol.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_void")))
            fol.on_button_pressed(SimpleNamespace(button=SimpleNamespace(id="btn_close")))
            try:
                fol.action_cancel()
            except Exception:
                pass
            await pause(pilot, 2)

    run_async(_())

# ── Settings modals / branding / language / chart no-plotext ───────────────

def test_review_import_audit_modals(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    monkeypatch.setattr(services, "save_settings", lambda staged, pw: (2, []))
    monkeypatch.setattr(services, "import_settings_from_json", lambda *a: {"ok": True, "changes": {"a":1}, "errors": []})
    monkeypatch.setattr(services, "get_audit_log", lambda n: [{"at":"now","action":"A","target_type":"x","target_id":1,"details":"d"}])

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            changes = {"language": ("en","ar")}
            pilot.app.push_screen(modals.ReviewChangesModal(changes))
            await pause(pilot, 3)
            pilot.app.screen.query_one("#pw", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            monkeypatch.setattr(services, "save_settings", lambda *a: (0, ["e1"]))
            pilot.app.push_screen(modals.ReviewChangesModal(changes))
            await pause(pilot, 2)
            pilot.app.screen.query_one("#pw", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 2)
            monkeypatch.setattr(services, "save_settings", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_cancel", Button).press()
            await pause(pilot, 2)
            # import
            pilot.app.push_screen(modals.ImportSettingsModal())
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            monkeypatch.setattr(services, "import_settings_from_json", lambda *a: {"ok": False, "changes": {}, "errors": ["bad"]})
            pilot.app.push_screen(modals.ImportSettingsModal())
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 2)
            monkeypatch.setattr(services, "import_settings_from_json", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)
            # audit
            pilot.app.push_screen(modals.AuditLogScreen())
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_close", Button).press()
            await pause(pilot, 2)

    run_async(_())


def test_branding_apply_demo_language(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    monkeypatch.setattr(services, "apply_branding_settings", lambda *a, **k: None)
    monkeypatch.setattr(services, "gift_attribution_for", lambda n: f"Gift for {n}")
    monkeypatch.setattr(services, "list_demo_data_presets", lambda: [{"key":"a","label":"A"}])
    monkeypatch.setattr(services, "apply_demo_data_preset", lambda *a, **k: None)
    monkeypatch.setattr(services, "set_setting", lambda *a, **k: None)
    monkeypatch.setattr(services, "set_language", lambda *a, **k: None)

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            # branding wizard
            bw = modals.BrandingWizardScreen(allow_cancel=True)
            pilot.app.push_screen(bw)
            await pause(pilot, 4)
            bw.query_one("#wiz_name", Input).value = ""
            bw.query_one("#btn_save", Button).press()
            await pause(pilot, 2)
            bw.query_one("#wiz_name", Input).value = "MyPlace"
            bw.query_one("#wiz_tagline", Input).value = "t"
            bw.on_input_changed(SimpleNamespace(input=SimpleNamespace(id="wiz_name")))
            bw.on_select_changed(SimpleNamespace(select=SimpleNamespace(id="wiz_anim")))
            bw._tick_preview()
            bw.query_one("#btn_save", Button).press()
            await pause(pilot, 3)
            monkeypatch.setattr(services, "apply_branding_settings", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
            bw2 = modals.BrandingWizardScreen(allow_cancel=True)
            pilot.app.push_screen(bw2)
            await pause(pilot, 3)
            bw2.query_one("#wiz_name", Input).value = "X"
            bw2.query_one("#btn_save", Button).press()
            await pause(pilot, 2)
            bw2.action_cancel()
            await pause(pilot, 2)
            # apply demo
            pilot.app.push_screen(modals.ApplyDemoDataModal())
            await pause(pilot, 3)
            pilot.app.screen.query_one("#demo_confirm", Input).value = "APPLY DEMO DATA"
            pilot.app.screen.query_one("#pw_field", Input).value = "p"
            pilot.app.screen.query_one("#btn_do", Button).press()
            await pause(pilot, 3)
            # language
            pilot.app.push_screen(modals.LanguageScreen())
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_en", Button).press()
            await pause(pilot, 3)
            pilot.app.push_screen(modals.LanguageScreen())
            await pause(pilot, 2)
            pilot.app.screen.query_one("#btn_ar", Button).press()
            await pause(pilot, 3)

    run_async(_())


def test_chart_no_plotext(clean_db, monkeypatch):
    prep_modal_app(monkeypatch)
    monkeypatch.setattr(modals, "PLOTEXT_OK", False)

    async def _():
        async with app_for_modals()().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            pilot.app.push_screen(modals.ChartScreen("revenue"))
            await pause(pilot, 3)
            await pilot.press("escape")
            await pause(pilot, 2)

    run_async(_())
