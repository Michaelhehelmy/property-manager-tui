from __future__ import annotations
import os
from datetime import datetime, timedelta
from textual.widgets import (Static, DataTable, Button, Input, RichLog, Select,
                              RadioSet, RadioButton, Checkbox, Collapsible, TabbedContent, TabPane)
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual import events
from app_core import services, updater
from app_core.services import t, fmt, selected_id, SETTINGS_CATALOG
from app_core import assistant as assistant_backend
from ..modals import (
    ErrorModal, ConfirmModal, ProtectedModal, UnlockSettingsModal, RecoveryResetModal, SetSuperPasswordScreen,
    RecoveryCodesScreen, ChangePasswordModal, ResetDefaultsModal, ImportSeedConfigModal, AddRoomModal, DeleteReservationScreen, ModifyReservationScreen,
    VoidItemScreen, CancelConfirmScreen, RefundScreen, OverrideCheckoutModal,
    InventoryEditScreen, InventoryDeleteScreen, QtyInputScreen, VoidExpenseScreen,
    EditRoomFieldScreen, RatePlanEditScreen, RatePlanDeleteScreen, GuestEditScreen,
    GuestMergeModal, GuestCRMModal, GuestHistoryModal, InvoiceScreen, WalkInReceiptScreen,
    ReportScreen, ChartScreen, ViewFolioScreen, ReviewChangesModal, ImportSettingsModal,
    AuditLogScreen, LanguageScreen, ApplyDemoDataModal, BrandingWizardScreen, AssistantCorrectionModal,
    BatchSelectScreen,
)
from .common import PAYMENT_METHODS, PAYMENT_TYPES, SOURCES, push_modal, show_error, _room_options, _category_options

class SettingsTab(VerticalScroll):
    def compose(self):
        yield Static("Settings & maintenance: branding, rate plans, rooms, seed config export/import (incl. activities & barcode fields), demo data presets, backups and health. Protected actions require unlock/super password. Hover controls for tips.", classes="help")
        yield Static("⚙ " + t("Settings"), classes="title")
        yield Static("", id="lock_msg", classes="warn")
        yield Vertical(id="body")

    def on_mount(self):
        self._render_locked()
        self.set_interval(30, self._check_timeout)

    def _check_timeout(self):
        if not services.is_session_unlocked():
            if self.query_one("#body", Vertical).children:
                try:
                    body = self.query_one("#body", Vertical)
                    for child in list(body.children):
                        if child.id and child.id.startswith("unlocked_"):
                            self._render_locked()
                            return
                except Exception:  # pragma: no cover - defensive/widget-state branch
                    pass  # pragma: no cover - defensive/widget-state branch

    def _render_locked(self):
        body = self.query_one("#body", Vertical)
        for w in list(body.children):
            try:
                w.remove()
            except Exception:  # pragma: no cover - defensive/widget-state branch
                pass  # pragma: no cover - defensive/widget-state branch
        self.query_one("#lock_msg", Static).update("🔒 Settings locked.")
        body.mount(Button("🔓 " + t("Unlock Settings"), id="unlocked_btn_unlock", variant="primary"))

    async def _render_unlocked(self):
        body = self.query_one("#body", Vertical)
        for w in list(body.children):
            try:
                w.remove()
            except Exception:  # pragma: no cover - defensive/widget-state branch
                pass  # pragma: no cover - defensive/widget-state branch
        self.query_one("#lock_msg", Static).update("")

        await body.mount(Button("🔒 " + t("Lock Now"), id="unlocked_btn_lock", variant="warning"))
        await body.mount(Button(t("Save Changes"), id="unlocked_btn_save", variant="success"))

        sections = {}
        for key, spec in SETTINGS_CATALOG.items():
            if spec[1] == "internal":
                continue
            sections.setdefault(spec[3], []).append((key, spec))
        titles = {"property": t("Property Information"), "financial": t("Financial & Tax"),
                  "reservations": t("Reservations"), "inventory": t("Inventory"), "system": t("System")}
        for section_id in ["property", "financial", "reservations", "inventory", "system"]:
            if section_id not in sections:
                continue  # pragma: no cover - defensive/widget-state branch
            coll = Collapsible(title=titles[section_id],
                               collapsed=(section_id not in ("property", "financial")))
            await body.mount(coll)
            for key, spec in sections[section_id]:
                label, typ, default = spec[:3]
                current = services.get_setting(key, default)
                await coll.mount(Static(label, classes="warn"))
                if len(spec) > 4 and spec[4]:
                    await coll.mount(Static(spec[4], classes="help"))
                if typ == "enum":
                    opts = spec[5] if len(spec) > 5 else []
                    sel = Select([(o, o) for o in opts], id=f"unlocked_set_{key}")
                    await coll.mount(sel)
                    try:
                        sel.value = current
                    except Exception:  # pragma: no cover - defensive/widget-state branch
                        pass  # pragma: no cover - defensive/widget-state branch
                elif typ == "bool":
                    cb = Checkbox("Enabled", id=f"unlocked_set_{key}", value=(current == "true"))
                    await coll.mount(cb)
                else:
                    is_secret = key == "assistant_llm_api_key" or "password" in key or "secret" in key
                    await coll.mount(Input(id=f"unlocked_set_{key}", value=current, password=is_secret))

        coll_rp = Collapsible(title=t("Rate Plans"), collapsed=True)
        await body.mount(coll_rp)
        await coll_rp.mount(DataTable(id="unlocked_rp_table"))
        rp_row = Horizontal(classes="button_row")
        await coll_rp.mount(rp_row)
        await rp_row.mount(Button("➕ " + t("Add Item"), id="unlocked_btn_rp_add", variant="primary"))
        await rp_row.mount(Button(t("Edit"), id="unlocked_btn_rp_edit", variant="primary"))
        await rp_row.mount(Button(t("Delete"), id="unlocked_btn_rp_del", variant="error"))

        coll_pr = Collapsible(title=t("Printers"), collapsed=True)
        await body.mount(coll_pr)
        await coll_pr.mount(Input(id="unlocked_set_default_printer",
                                   value=services.get_setting("default_printer", "")))
        pr_row = Horizontal(classes="button_row")
        await coll_pr.mount(pr_row)
        await pr_row.mount(Button(t("Detect Printers"), id="unlocked_btn_pr_detect", variant="primary"))
        await pr_row.mount(Button(t("Test Print"), id="unlocked_btn_pr_test", variant="primary"))
        await coll_pr.mount(DataTable(id="unlocked_printer_table"))

        coll_sec = Collapsible(title=t("Security"), collapsed=True)
        await body.mount(coll_sec)
        await coll_sec.mount(Button("🔒 " + t("Change Password"), id="unlocked_btn_change_pw", variant="warning"))
        await coll_sec.mount(Button("Audit Log", id="unlocked_btn_audit", variant="primary"))

        coll_rooms = Collapsible(title="Rooms (Add / Remove)", collapsed=True)
        await body.mount(coll_rooms)
        await coll_rooms.mount(Static("Add rooms for new inventory/units. Delete is blocked if referenced by reservations.", classes="help"))
        await coll_rooms.mount(DataTable(id="unlocked_rooms_table"))
        rooms_row = Horizontal(classes="button_row")
        await coll_rooms.mount(rooms_row)
        await rooms_row.mount(Button("➕ Add Room", id="unlocked_btn_room_add", variant="primary"))
        await rooms_row.mount(Button("Delete Room", id="unlocked_btn_room_del", variant="error"))

        coll_m = Collapsible(title=t("Maintenance"), collapsed=True)
        await body.mount(coll_m)
        await coll_m.mount(Static(f"DB: {services.db.DB_PATH}", classes="help"))
        try:
            size = os.path.getsize(services.db.DB_PATH)
            await coll_m.mount(Static(f"Size: {size // 1024} KB", classes="help"))
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch
        m_row = Horizontal(classes="button_row")
        await coll_m.mount(m_row)
        await m_row.mount(Button(t("Export Settings"), id="unlocked_btn_export", variant="primary"))
        await m_row.mount(Button(t("Import Settings"), id="unlocked_btn_import", variant="primary"))
        await m_row.mount(Button(t("Backup Now"), id="unlocked_btn_backup", variant="success"))
        await m_row.mount(Button("Branding / Animation", id="unlocked_btn_branding", variant="primary"))
        await m_row.mount(Button("Reset to Defaults", id="unlocked_btn_reset_defaults", variant="error"))
        await m_row.mount(Button("Export Seed Config", id="unlocked_btn_seed_export", variant="primary"))
        await m_row.mount(Button("Import Seed Config", id="unlocked_btn_seed_import", variant="warning"))
        await m_row.mount(Button("Health Check", id="unlocked_btn_health", variant="primary"))
        await m_row.mount(Button("Load Demo Data (Guarded)", id="unlocked_btn_demo_data", variant="warning"))
        await m_row.mount(Button("Check Updates", id="unlocked_btn_update_check", variant="primary"))
        await m_row.mount(Button("Apply Update", id="unlocked_btn_update_apply", variant="warning"))

        self._reload_rp()
        self._reload_rooms()

    def _reload_rp(self):
        try:
            table = self.query_one("#unlocked_rp_table", DataTable)
            table.clear(columns=True)
            table.add_columns("ID", "Name", "Modifier")
            for r in services.get_all_rate_plans():
                table.add_row(str(r["id"]), r["name"], f"{r['price_modifier']:+.2f} EGP")
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch


    def _reload_rooms(self):
        try:
            table = self.query_one("#unlocked_rooms_table", DataTable)
            table.clear(columns=True)
            table.add_columns("ID", "Number", "Type", "Base", "HK")
            for r in services.get_all_rooms():
                table.add_row(str(r["id"]), r["room_number"], r["room_type"],
                              f"{float(r['base_price'] or 0):.2f}", r["housekeeping_status"])
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch

    def _after_unlock(self, ok):
        if ok == "recovery":
            self.app.push_screen(RecoveryResetModal(), callback=self._after_unlock)
            return
        if ok:
            try:
                self.app.call_later(self._async_unlock)
            except Exception:  # pragma: no cover - defensive/widget-state branch
                pass  # pragma: no cover - defensive/widget-state branch

    async def _async_unlock(self):
        try:
            await self._render_unlocked()
        except Exception as exc:
            self.app.notify(str(exc), severity="error")

    async def on_button_pressed(self, e):
        bid = e.button.id
        try:
            if bid == "unlocked_btn_unlock":
                self.app.push_screen(UnlockSettingsModal(), callback=self._after_unlock)
            elif bid == "unlocked_btn_lock":
                services.lock_session()
                self._render_locked()
            elif bid == "unlocked_btn_save":
                self._save_settings()
            elif bid == "unlocked_btn_change_pw":
                self.app.push_screen(ChangePasswordModal())
            elif bid == "unlocked_btn_audit":
                self.app.push_screen(AuditLogScreen())
            elif bid == "unlocked_btn_export":
                path = services.export_settings_to_json()
                self.app.notify(f"Exported: {path}")
            elif bid == "unlocked_btn_import":
                self.app.push_screen(ImportSettingsModal(),
                                     callback=lambda ok: self.app.refresh(recompose=True) if ok else None)
            elif bid == "unlocked_btn_backup":
                path = services.backup_now()
                self.app.notify(f"Backup: {path}")
            elif bid == "unlocked_btn_seed_export":
                try:
                    p = services.export_seed_config()
                    self.app.notify(f"Seed config exported: {p} (edit JSON incl. rooms/inventory/activities, then Import for first-time seed)", timeout=8)
                except Exception as exc:
                    self.app.notify(str(exc), severity="error")
            elif bid == "unlocked_btn_seed_import":
                def _after_import(ok):
                    if ok:
                        try:
                            self._reload_rp()
                            self._reload_rooms()
                        except Exception:
                            pass
                        self.app.refresh_all_tables()
                        self.app.call_later(self._async_unlock)
                        self.app.notify("Seed config applied.")
                self.app.push_screen(ImportSeedConfigModal(), callback=_after_import)
            elif bid == "unlocked_btn_branding":
                def _after(ok):
                    if ok:
                        try:
                            self.app._apply_branding()
                        except Exception:  # pragma: no cover - defensive/widget-state branch
                            pass  # pragma: no cover - defensive/widget-state branch
                        self.app.refresh_all_tables()
                        self.app.notify("Branding updated.")
                self.app.push_screen(BrandingWizardScreen(allow_cancel=True), callback=_after)
            elif bid == "unlocked_btn_reset_defaults":
                push_modal(self.app, ResetDefaultsModal(),
                           on_success=lambda: (self.app.notify("Settings reset to defaults."),
                                               self.app.call_later(self._async_unlock)))
            elif bid == "unlocked_btn_update_check":
                res = updater.check_for_updates()
                if res.get("error"):
                    self.app.notify(f"Update check failed: {res['error']}", severity="error", timeout=8)
                elif res.get("update_available"):
                    n = res.get("commits_behind")
                    latest = (res.get("latest") or {}).get("subject") or ""
                    msg = f"Update available ({n} commit(s)). Latest: {latest}" if n is not None else f"Update available. Latest: {latest}"
                    # show top changelog subjects
                    changes = [c.get("subject","") for c in (res.get("changelog") or [])[:5] if c.get("subject")]
                    if changes:
                        msg += " | " + "; ".join(changes)
                    msg += " (data preserved on update; restart after apply)"
                    self.app.notify(msg, timeout=12)
                else:
                    self.app.notify("Up to date. " + ((res.get("latest") or {}).get("subject") or ""), timeout=6)
            elif bid == "unlocked_btn_update_apply":
                def _after_confirm(ok):
                    if not ok:
                        return
                    result = updater.apply_update()
                    if result.get("ok"):
                        after = (result.get("local_after") or {}).get("commit_short") or ""
                        self.app.notify(f"Update applied ({after}). Restart app to load new version. Data (DB/exports/invoices) preserved.", timeout=12)
                    else:
                        self.app.notify(result.get("error") or "Update failed.", severity="error", timeout=10)
                self.app.push_screen(ConfirmModal("Apply app update now? Uses git pull --ff-only, preserves DB/exports/invoices. Restart required after update.", "UPDATE APP"), callback=_after_confirm)
            elif bid == "unlocked_btn_health":
                self.app.notify("Running ledger/data health check...", severity="information", timeout=2)
                def _work():
                    try:
                        import json as _json
                        result = services.verify_ledger_integrity()
                        text = _json.dumps(result, indent=2, ensure_ascii=False, default=str)
                        title = "Health Check — " + ("OK" if result.get("ok") else "ISSUES FOUND")
                        self.app.call_from_thread(lambda: self.app.push_screen(ReportScreen(title, text)))
                    except Exception as exc:  # pragma: no cover - defensive/widget-state branch
                        self.app.call_from_thread(lambda: show_error(self.app, str(exc)))  # pragma: no cover - defensive/widget-state branch
                self.run_worker(_work, thread=True, exclusive=False)
            elif bid == "unlocked_btn_demo_data":
                push_modal(self.app, ApplyDemoDataModal(),
                           on_success=lambda: (self.app.refresh_all_tables(), self.app.notify("Demo data preset applied.")))
            elif bid == "unlocked_btn_rp_add":
                push_modal(self.app, RatePlanEditScreen(), on_success=self._reload_rp)
            elif bid == "unlocked_btn_rp_edit":
                pid = selected_id(self.query_one("#unlocked_rp_table", DataTable))
                if pid:
                    push_modal(self.app, RatePlanEditScreen(pid), on_success=self._reload_rp)
            elif bid == "unlocked_btn_rp_del":
                pid = selected_id(self.query_one("#unlocked_rp_table", DataTable))
                if pid:
                    row = self.query_one("#unlocked_rp_table", DataTable).get_row_at(
                        self.query_one("#unlocked_rp_table", DataTable).cursor_row)
                    push_modal(self.app, RatePlanDeleteScreen(pid, row[1]), on_success=self._reload_rp)
            elif bid == "unlocked_btn_room_add":
                def _after_room(ok):
                    if ok:
                        self._reload_rooms()
                        self.app.refresh_all_tables()
                self.app.push_screen(AddRoomModal(), callback=_after_room)
            elif bid == "unlocked_btn_room_del":
                table = self.query_one("#unlocked_rooms_table", DataTable)
                rid = selected_id(table)
                if rid:
                    row = table.get_row_at(table.cursor_row)
                    room_no = row[1] if row else str(rid)
                    def _do_delete(ok):
                        if ok:
                            try:
                                services.delete_room(rid)
                                self.app.notify("Room deleted.")
                                self._reload_rooms()
                                self.app.refresh_all_tables()
                            except Exception as exc:
                                self.app.notify(str(exc), severity="error")
                    self.app.push_screen(ConfirmModal(f"Delete room {room_no} (#{rid})? Blocked if referenced by reservations."), callback=_do_delete)
            elif bid == "unlocked_btn_pr_detect":
                detected = services.list_printers()
                table = self.query_one("#unlocked_printer_table", DataTable)
                table.clear(columns=True)
                table.add_columns("Queue", "Default")
                for q in detected["queues"]:
                    table.add_row(q, "✓" if q == detected["default"] else "")
                self.app.notify(f"Detected {len(detected['queues'])} printer(s) [{detected['source']}]")
            elif bid == "unlocked_btn_pr_test":
                ok, msg = services.test_print()
                self.app.notify(msg, severity="information" if ok else "error")
        except Exception as exc:  # pragma: no cover - defensive/widget-state branch
            self.app.notify(str(exc), severity="error")  # pragma: no cover - defensive/widget-state branch

    def _collect_changes(self):
        changes = {}
        for key, spec in SETTINGS_CATALOG.items():
            if spec[1] == "internal":
                continue
            widget_id = f"#unlocked_set_{key}"
            try:
                if spec[1] == "enum":
                    w = self.query_one(widget_id, Select)
                    new = w.value if isinstance(w.value, str) else spec[2]
                elif spec[1] == "bool":
                    w = self.query_one(widget_id, Checkbox)
                    new = "true" if w.value else "false"
                else:
                    w = self.query_one(widget_id, Input)
                    new = w.value.strip()
                current = services.get_setting(key, spec[2])
                if new != current:
                    changes[key] = (current, new)
            except Exception:  # pragma: no cover - defensive/widget-state branch
                continue  # pragma: no cover - defensive/widget-state branch
        return changes

    def _save_settings(self):
        changes = self._collect_changes()
        if not changes:
            return self.app.notify("No changes.", severity="information")
        self.app.push_screen(ReviewChangesModal(changes), callback=self._after_save)

    def _after_save(self, ok):
        if ok:
            try:
                services.set_language(services.get_setting("language", "en"))
                self.app.refresh_all_tables()
            except Exception as exc:  # pragma: no cover - defensive/widget-state branch
                self.app.notify(str(exc), severity="error")  # pragma: no cover - defensive/widget-state branch
# ═══════════════════════════════════════════════════════════════════════════
# MANAGER ASSISTANT (grounded, real backend tools)
# ═══════════════════════════════════════════════════════════════════════════

