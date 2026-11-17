"""Pending action proposals and validated execution wrappers for assistant write actions.

This module provides action proposal/execute/cancel helpers (no NLU):
- `create_proposal(...)` normalizes args, runs read-only validation, and returns a pending action
- `execute_pending_action(...)` re-validates and executes via existing service functions
- `cancel_pending_action(...)` marks a proposal as cancelled

Execution does not bypass service-layer proofs. Callers (web/session/UI) must enforce
required confirmations/proofs before invoking execute.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Any, Callable, Mapping

from . import action_validation as av
from .common import _safe_int
from .. import services

logger = logging.getLogger(__name__)

PROPOSAL_ACTION_TYPES = set(getattr(av, "PROPOSAL_ACTION_TYPES", ())) or {
    "preview_add_pos_item",
    "propose_add_pos_item",
    "propose_complete_walkin_sale",
    "propose_record_payment",
    "propose_book_activity",
    "propose_create_reservation",
}

EXECUTE_ACTION_TYPES = set(getattr(av, "EXECUTE_ACTION_TYPES", ())) or {
    "execute_add_pos_item",
    "execute_complete_walkin_sale",
    "execute_record_payment",
    "execute_book_activity",
    "execute_create_reservation",
}

# Metadata only: callers/UI/web layers must enforce actual proofs.
_REQUIRED_PROOFS_BASE = {
    "propose_add_pos_item": ["ui_confirmation", "session_or_auth_context"],
    "propose_complete_walkin_sale": ["ui_confirmation", "payment_confirmation", "session_or_auth_context"],
    "propose_record_payment": ["ui_confirmation", "payment_confirmation", "session_or_auth_context"],
    "propose_book_activity": ["ui_confirmation", "session_or_auth_context"],
    "propose_create_reservation": ["ui_confirmation", "session_or_auth_context"],
}
REQUIRED_PROOFS = dict(_REQUIRED_PROOFS_BASE)
REQUIRED_PROOFS.update({k.replace("propose_", "execute_"): v for k, v in _REQUIRED_PROOFS_BASE.items()})


def _norm_str(v: Any) -> str:
    return str(v or "").strip()


def _now_iso_z() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _generate_action_id() -> str:
    return secrets.token_urlsafe(16)


def _float(v: Any, default: float | None = None) -> float | None:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _int(v: Any, default: int | None = None) -> int | None:
    iv = _safe_int(v)
    return default if iv is None else iv


def _extract_proofs(form_proofs: Any, ui_context: Any) -> dict[str, Any]:
    proofs: dict[str, Any] = {}
    if isinstance(ui_context, Mapping):
        p = ui_context.get("proofs")
        if isinstance(p, Mapping):
            proofs.update(dict(p))
    if isinstance(form_proofs, Mapping):
        proofs.update(dict(form_proofs))
    for k in ("confirmed", "confirm", "ui_confirmed", "ack", "acknowledged", "approved"):
        if k in proofs and "confirmed" not in proofs:
            proofs["confirmed"] = proofs[k]
    return proofs


def _summary_for(action_type: str, prechecks: Mapping[str, Any], args: Mapping[str, Any]) -> str:
    preview = prechecks.get("preview") if isinstance(prechecks, Mapping) else {}
    if not isinstance(preview, Mapping):
        preview = {}
    ptype = _norm_str(preview.get("type") or action_type)

    if ptype in {"preview_add_pos_item", "propose_add_pos_item"}:
        rid = preview.get("reservation_id") or args.get("reservation_id")
        item = preview.get("item_name") or preview.get("description") or args.get("desc") or "item"
        qty = preview.get("qty") or args.get("qty") or args.get("quantity")
        price = preview.get("unit_price") or args.get("price")
        return f"Add POS item '{item}' x{qty} @ {price} to reservation #{rid}"
    if ptype in {"preview_complete_walkin_sale", "propose_complete_walkin_sale"}:
        count = preview.get("item_count") or (len(args.get("items") or []) if isinstance(args.get("items"), list) else 0)
        total = preview.get("estimated_total")
        method = preview.get("method") or args.get("method")
        return f"Complete walk-in sale ({count} item(s), est total {total}) via {method}"
    if ptype in {"preview_record_payment", "propose_record_payment"}:
        rid = preview.get("reservation_id") or args.get("reservation_id")
        amt = preview.get("amount") or args.get("amount")
        method = preview.get("method") or args.get("method")
        return f"Record payment {amt} via {method} for reservation #{rid}"
    if ptype in {"preview_book_activity", "propose_book_activity"}:
        rid = preview.get("reservation_id") or args.get("reservation_id")
        act = preview.get("activity_name") or preview.get("activity_id") or args.get("activity_id")
        qty = preview.get("quantity") or args.get("quantity") or 1
        return f"Book activity '{act}' x{qty} for reservation #{rid}"
    if ptype in {"preview_create_reservation", "propose_create_reservation"}:
        guest = preview.get("guest_name") or args.get("guest_name") or f"guest#{preview.get('guest_id') or args.get('guest_id')}"
        room = preview.get("room_number") or preview.get("room_id") or args.get("room_id")
        ci = preview.get("check_in") or args.get("check_in")
        co = preview.get("check_out") or args.get("check_out")
        return f"Create reservation for {guest} in room {room} ({ci} to {co})"
    return f"Proposed action: {action_type}"


def _map_propose_to_execute(action_type: str) -> str | None:
    at = _norm_str(action_type)
    mapped = av.ACTION_MAP.get(at)
    if mapped:
        return mapped[1]
    if at.startswith("execute_") and at in EXECUTE_ACTION_TYPES:
        return at
    return None


def create_proposal(
    action_type: str,
    args: Mapping[str, Any] | None = None,
    ui_context: Mapping[str, Any] | None = None,
    ttl_seconds: int | None = None,
) -> dict[str, Any]:
    """Create a pending action proposal with validation/prechecks (no execution)."""
    action_type = _norm_str(action_type)
    if action_type not in PROPOSAL_ACTION_TYPES:
        return {
            "ok": False,
            "status": "invalid",
            "error": f"Unsupported proposal action_type '{action_type}'",
            "supported_action_types": sorted(PROPOSAL_ACTION_TYPES),
        }

    normalized_args = av.normalize_action_args(action_type, args or {})
    prechecks = av.build_prechecks(action_type, normalized_args, ui_context=ui_context)
    if not prechecks.get("ok", False):
        return {
            "ok": False,
            "status": "invalid",
            "action_type": action_type,
            "errors": prechecks.get("errors") or [],
            "warnings": prechecks.get("warnings") or [],
            "checks": prechecks.get("checks") or [],
            "resolved": prechecks.get("resolved") or {},
            "preview": prechecks.get("preview") or {},
            "prechecks": prechecks,
        }

    action_id = _generate_action_id()
    expires_at = av._expires_at(ttl_seconds if ttl_seconds is not None else av.DEFAULT_TTL_SECONDS)
    execute_type = _map_propose_to_execute(action_type)
    required_proofs = list(REQUIRED_PROOFS.get(action_type) or REQUIRED_PROOFS.get(execute_type or "") or ["ui_confirmation"])

    return {
        "ok": True,
        "status": "pending" if execute_type else "preview",
        "action_id": action_id,
        "action_type": action_type,
        "execute_action_type": execute_type,
        "args": normalized_args,
        "ui_context": dict(ui_context or {}),
        "created_at": _now_iso_z(),
        "expires_at": expires_at,
        "summary": _summary_for(action_type, prechecks, normalized_args),
        "required_proofs": required_proofs,
        "proofs_hint": {
            "caller_must_enforce": True,
            "note": "Do not bypass service proofs. Web/session/UI layers must enforce confirmations/proofs before execute.",
        },
        "prechecks": prechecks,
        "preview": prechecks.get("preview") or {},
        "resolved": prechecks.get("resolved") or {},
        "warnings": prechecks.get("warnings") or [],
        "checks": prechecks.get("checks") or [],
    }


def cancel_pending_action(pending_action: Mapping[str, Any] | None, reason: str = "") -> dict[str, Any]:
    """Mark a pending action proposal as cancelled (caller persists state as needed)."""
    if not isinstance(pending_action, Mapping):
        return {"ok": False, "status": "invalid", "error": "pending_action is required"}
    out = dict(pending_action)
    out["status"] = "cancelled"
    out["cancelled_at"] = _now_iso_z()
    if reason:
        out["cancel_reason"] = reason
    out["ok"] = True
    return out


# ── Execution wrappers (call existing services) ───────────────────────────────

def _exec_add_pos_item(args: Mapping[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any]:
    rid = _int(args.get("reservation_id") or ((resolved.get("reservation") or {}).get("id") if isinstance(resolved.get("reservation"), dict) else None))
    item = resolved.get("item") if isinstance(resolved.get("item"), dict) else {}
    batch = resolved.get("batch") if isinstance(resolved.get("batch"), dict) else {}
    inv_id = _int(args.get("item_id") or args.get("inv_id") or item.get("id"))
    desc = _norm_str(args.get("desc") or args.get("item_description") or item.get("item_name") or item.get("name"))
    qty = _int(args.get("qty") or args.get("quantity"))
    price = _float(args.get("price") if args.get("price") is not None else args.get("unit_price"))
    if price is None and isinstance(item, dict):
        price = _float(item.get("selling_price"))
    batch_id = _int(args.get("batch_id") or batch.get("id"))
    lot_code = _norm_str(args.get("lot_code") or batch.get("lot_code")) or None
    if rid is None or inv_id is None or qty is None or price is None or not desc:
        raise ValueError("Missing required add_pos_item args (reservation_id, inv_id/item_id, desc, qty, price)")
    result = services.add_pos_item(rid, desc, int(qty), float(price), inv_id=inv_id, batch_id=batch_id, lot_code=lot_code)
    affected: dict[str, Any] = {"reservation_id": rid, "inventory_item_id": inv_id}
    if batch_id:
        affected["batch_id"] = batch_id
    if isinstance(result, dict):
        if result.get("pos_id") is not None:
            affected["pos_id"] = result.get("pos_id")
        if result.get("pos_ids") is not None:
            affected["pos_ids"] = result.get("pos_ids")
    return {"result": result, "affected_ids": affected, "message": f"Added POS item to reservation #{rid}"}


def _exec_complete_walkin_sale(args: Mapping[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any]:
    items_in = args.get("items") or []
    method = _norm_str(args.get("method") or args.get("payment_method"))
    notes = _norm_str(args.get("notes"))
    payment_type = _norm_str(args.get("payment_type")) or "Payment"
    if not isinstance(items_in, list) or not items_in:
        raise ValueError("items is required for complete_walkin_sale")
    if not method:
        raise ValueError("method is required for complete_walkin_sale")

    payload_items = []
    for it in items_in:
        it = dict(it or {})
        inv_id = _int(it.get("inv_id") or it.get("item_id") or it.get("inventory_item_id"))
        desc = _norm_str(it.get("desc") or it.get("item_description") or it.get("item_name"))
        qty = _int(it.get("qty") or it.get("quantity"))
        price = _float(it.get("price") if it.get("price") is not None else it.get("unit_price"))
        batch_id = _int(it.get("batch_id"))
        lot_code = _norm_str(it.get("lot_code")) or None
        if inv_id is None or not desc or qty is None or price is None:
            raise ValueError("Each walk-in item requires inv_id/item_id, desc, qty, price")
        payload_items.append({
            "desc": desc,
            "qty": int(qty),
            "price": float(price),
            "inv_id": inv_id,
            "batch_id": batch_id,
            "lot_code": lot_code,
        })

    result = services.complete_walkin_sale(payload_items, method, notes=notes, payment_type=payment_type)
    affected: dict[str, Any] = {}
    if isinstance(result, dict):
        if result.get("payment_id") is not None:
            affected["payment_id"] = result.get("payment_id")
        if result.get("pos_ids") is not None:
            affected["pos_ids"] = result.get("pos_ids")
    return {"result": result, "affected_ids": affected, "message": f"Completed walk-in sale via {method}"}


def _exec_record_payment(args: Mapping[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any]:
    rid = _int(args.get("reservation_id") or ((resolved.get("reservation") or {}).get("id") if isinstance(resolved.get("reservation"), dict) else None))
    amount = _float(args.get("amount"))
    method = _norm_str(args.get("method") or args.get("payment_method"))
    notes = _norm_str(args.get("notes"))
    payment_type = _norm_str(args.get("payment_type")) or "Payment"
    if rid is None or amount is None or not method:
        raise ValueError("reservation_id, amount, and method are required for record_payment")
    result = services.record_payment(rid, float(amount), method, notes=notes, payment_type=payment_type)
    affected: dict[str, Any] = {"reservation_id": rid}
    if isinstance(result, dict) and result.get("payment_id") is not None:
        affected["payment_id"] = result.get("payment_id")
    return {"result": result, "affected_ids": affected, "message": f"Recorded payment for reservation #{rid}"}


def _exec_book_activity(args: Mapping[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any]:
    rid = _int(args.get("reservation_id") or ((resolved.get("reservation") or {}).get("id") if isinstance(resolved.get("reservation"), dict) else None))
    aid = _int(args.get("activity_id") or ((resolved.get("activity") or {}).get("id") if isinstance(resolved.get("activity"), dict) else None))
    qty = _int(args.get("quantity") or args.get("qty") or 1) or 1
    scheduled_at = args.get("scheduled_at")
    notes = _norm_str(args.get("notes"))
    if rid is None or aid is None:
        raise ValueError("reservation_id and activity_id are required for book_activity")
    result = services.book_activity(rid, aid, quantity=int(qty), scheduled_at=scheduled_at, notes=notes)
    affected: dict[str, Any] = {"reservation_id": rid, "activity_id": aid}
    if isinstance(result, dict):
        if result.get("pos_id") is not None:
            affected["pos_id"] = result.get("pos_id")
        if result.get("pos_ids") is not None:
            affected["pos_ids"] = result.get("pos_ids")
    return {"result": result, "affected_ids": affected, "message": f"Booked activity #{aid} for reservation #{rid}"}


def _exec_create_reservation(args: Mapping[str, Any], resolved: Mapping[str, Any]) -> dict[str, Any]:
    guest = resolved.get("guest") if isinstance(resolved.get("guest"), dict) else {}
    guest_res = resolved.get("guest_resolution") if isinstance(resolved.get("guest_resolution"), dict) else {}
    if not guest and isinstance(guest_res.get("guest"), dict):
        guest = guest_res.get("guest") or {}
    room = resolved.get("room") if isinstance(resolved.get("room"), dict) else {}

    guest_id = _int(args.get("guest_id") or guest.get("id"))
    guest_name = _norm_str(args.get("guest_name") or args.get("name") or guest.get("canonical_name") or guest.get("name"))
    source = _norm_str(args.get("source")) or "Direct"
    room_id = _int(args.get("room_id") or room.get("id"))
    rate_plan_id = _int(args.get("rate_plan_id") or args.get("plan_id"))
    rate = _float(args.get("rate") if args.get("rate") is not None else args.get("agreed_nightly_rate"))
    if rate is None:
        rate = _float(args.get("nightly_rate"))
    if rate is None and isinstance(room, dict):
        rate = _float(room.get("base_price"))
    ci = _norm_str(args.get("check_in") or args.get("check_in_date"))
    co = _norm_str(args.get("check_out") or args.get("check_out_date"))
    deposit = _float(args.get("deposit") if args.get("deposit") is not None else args.get("deposit_amount"), 0.0) or 0.0

    if not guest_name and not guest_id:
        raise ValueError("guest_name or guest_id is required for create_reservation")
    if room_id is None or rate is None or not ci or not co:
        raise ValueError("room_id, rate/agreed_nightly_rate, check_in, check_out are required for create_reservation")

    rid = services.create_reservation(
        guest_name=guest_name or "",
        source=source,
        room_id=room_id,
        rate_plan_id=rate_plan_id,
        agreed_nightly_rate=float(rate),
        ci=ci,
        co=co,
        deposit_amount=float(deposit),
        guest_id=guest_id,
    )
    affected: dict[str, Any] = {"reservation_id": rid}
    if room_id is not None:
        affected["room_id"] = room_id
    if guest_id is not None:
        affected["guest_id"] = guest_id
    return {"result": rid, "affected_ids": affected, "message": f"Created reservation #{rid}"}


_EXECUTORS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]]] = {
    "execute_add_pos_item": _exec_add_pos_item,
    "execute_complete_walkin_sale": _exec_complete_walkin_sale,
    "execute_record_payment": _exec_record_payment,
    "execute_book_activity": _exec_book_activity,
    "execute_create_reservation": _exec_create_reservation,
}


def execute_pending_action(
    pending_action: Mapping[str, Any] | None,
    form_proofs: Mapping[str, Any] | None = None,
    ui_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Re-validate and execute a pending action proposal via existing services.

    Returns: {ok, status, result, affected_ids, message, prechecks}
    """
    if not isinstance(pending_action, Mapping):
        return {"ok": False, "status": "invalid", "result": None, "affected_ids": {}, "message": "pending_action is required", "prechecks": None}

    status = _norm_str(pending_action.get("status") or "pending")
    if status in {"executed", "cancelled", "expired"}:
        return {
            "ok": False,
            "status": status,
            "result": None,
            "affected_ids": {},
            "message": f"Action is already {status}",
            "prechecks": pending_action.get("prechecks"),
        }

    expires_at = pending_action.get("expires_at")
    if expires_at and av._is_expired(expires_at):
        return {
            "ok": False,
            "status": "expired",
            "result": None,
            "affected_ids": {},
            "message": "Pending action has expired; create a new proposal",
            "prechecks": pending_action.get("prechecks"),
        }

    action_type = _norm_str(pending_action.get("action_type"))
    execute_type = _norm_str(pending_action.get("execute_action_type")) or _map_propose_to_execute(action_type)
    if not execute_type or execute_type not in _EXECUTORS:
        return {
            "ok": False,
            "status": "invalid",
            "result": None,
            "affected_ids": {},
            "message": f"Action type '{action_type}' is not executable",
            "prechecks": pending_action.get("prechecks"),
        }

    merged_ctx = dict(pending_action.get("ui_context") or {})
    if isinstance(ui_context, Mapping):
        merged_ctx.update(dict(ui_context))
    proofs = _extract_proofs(form_proofs, merged_ctx)
    required_proofs = pending_action.get("required_proofs") or REQUIRED_PROOFS.get(execute_type) or []
    if required_proofs and not (proofs.get("confirmed") or proofs.get("ui_confirmed") or proofs.get("approved")):
        return {
            "ok": False,
            "status": "proof_required",
            "result": None,
            "affected_ids": {},
            "message": "Confirmation/proof is required before executing this action",
            "prechecks": pending_action.get("prechecks"),
            "required_proofs": required_proofs,
            "provided_proofs": proofs,
        }

    stored_args = pending_action.get("args") or {}
    normalized_args = av.normalize_action_args(execute_type, stored_args)
    prechecks = av.build_prechecks(execute_type, normalized_args, ui_context=merged_ctx)
    if not prechecks.get("ok", False):
        return {
            "ok": False,
            "status": "validation_failed",
            "result": None,
            "affected_ids": {},
            "message": "Action re-validation failed; proposal is no longer safe to execute",
            "prechecks": prechecks,
            "errors": prechecks.get("errors") or [],
            "warnings": prechecks.get("warnings") or [],
        }

    resolved = prechecks.get("resolved") or {}
    try:
        exec_out = _EXECUTORS[execute_type](normalized_args, resolved)
        return {
            "ok": True,
            "status": "executed",
            "result": exec_out.get("result"),
            "affected_ids": exec_out.get("affected_ids") or {},
            "message": exec_out.get("message") or "Action executed",
            "prechecks": prechecks,
            "action_id": pending_action.get("action_id"),
            "action_type": action_type,
            "execute_action_type": execute_type,
            "executed_at": _now_iso_z(),
        }
    except Exception as e:
        logger.exception("execute_pending_action failed action_type=%s execute_type=%s", action_type, execute_type)
        return {
            "ok": False,
            "status": "failed",
            "result": None,
            "affected_ids": {},
            "message": str(e),
            "prechecks": prechecks,
            "action_id": pending_action.get("action_id"),
            "action_type": action_type,
            "execute_action_type": execute_type,
        }
