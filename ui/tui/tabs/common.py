"""All tabs — UI wiring only, logic in services.py."""
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

PAYMENT_METHODS = ["Cash", "Card", "OTA", "Bank Transfer"]
PAYMENT_TYPES = ["Payment", "Deposit"]
SOURCES = ["Direct", "Booking.com", "Airbnb", "Walk-in", "Phone", "Other"]


def push_modal(app, modal, on_success=None):
    """Push modal; callback is guarded so exceptions can't crash the app."""
    def cb(ok):
        try:
            if ok and on_success is not None:
                on_success()
            elif ok:
                app.refresh_all_tables()
        except Exception as exc:  # pragma: no cover - defensive/widget-state branch
            try:
                app.notify(str(exc), severity="error")  # pragma: no cover - defensive/widget-state branch
            except Exception:  # pragma: no cover - defensive/widget-state branch
                pass  # pragma: no cover - defensive/widget-state branch
    try:
        app.push_screen(modal, callback=cb)
    except Exception as exc:
        try:
            app.notify(str(exc), severity="error")
        except Exception:  # pragma: no cover - defensive/widget-state branch
            pass  # pragma: no cover - defensive/widget-state branch


def show_error(app, msg):
    app.push_screen(ErrorModal(msg))


def _room_options():
    return [(f"{r['room_number']} - {r['room_type']}", r["id"]) for r in services.get_all_rooms()]


def _category_options():
    return [(c["category_name"], c["id"]) for c in services.get_expense_categories()]


# ═══════════════════════════════════════════════════════════════════════════
# FRONT DESK
# ═══════════════════════════════════════════════════════════════════════════

