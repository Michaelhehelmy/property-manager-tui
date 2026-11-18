"""Read-only validation and preview helpers for assistant write-action proposals.

These helpers resolve entities, perform prechecks, and build structured previews
without mutating application state. They are intended to back confirmed action
proposals and re-validation before execution.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Mapping

from .common import _parse_iso, _safe_int
from .. import services

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 600
ALLOWED_RES_STATUSES_FOR_POSTING = {"Reserved", "Checked In"}
ALLOWED_PAYMENT_METHODS = {"Cash", "Visa", "Bank", "Other"}

# (proposal_action_type) -> (preview_type, execute_type)
ACTION_MAP = {
    "preview_add_pos_item": ("preview_add_pos_item", None),
    "propose_add_pos_item": ("preview_add_pos_item", "execute_add_pos_item"),
    "propose_complete_walkin_sale": ("preview_complete_walkin_sale", "execute_complete_walkin_sale"),
    "propose_record_payment": ("preview_record_payment", "execute_record_payment"),
    "propose_book_activity": ("preview_book_activity", "execute_book_activity"),
    "propose_create_reservation": ("preview_create_reservation", "execute_create_reservation"),
}

PROPOSAL_ACTION_TYPES = set(ACTION_MAP.keys())
EXECUTE_ACTION_TYPES = {v[1] for v in ACTION_MAP.values() if v[1]}


def _ok_base(preview_type: str, args: dict | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "errors": [],
        "warnings": [],
        "checks": [],
        "resolved": {},
        "preview": {"type": preview_type, "args": dict(args or {})},
    }


def _add_error(out: dict[str, Any], msg: str) -> None:
    out["errors"].append(msg)
    out["ok"] = False


def _add_warning(out: dict[str, Any], msg: str) -> None:
    out["warnings"].append(msg)


def _check(out: dict[str, Any], name: str, ok: bool, detail: str = "") -> None:
    out["checks"].append({"name": name, "ok": bool(ok), "detail": detail})


def _row_to_dict(row: Any) -> Any:
    if row is None:
        return None
    try:
        if hasattr(row, "keys"):
            return {k: row[k] for k in row.keys()}
    except Exception:
        pass
    if isinstance(row, dict):
        return dict(row)
    return row


def _float_or_none(v: Any) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    return _safe_int(v)


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "on"}
    return bool(v)


def _norm_str(v: Any) -> str:
    return str(v or "").strip()


def _get(args: Mapping[str, Any] | None, *keys: str, default: Any = None) -> Any:
    if not isinstance(args, Mapping):
        return default
    for k in keys:
        if k in args and args[k] is not None:
            return args[k]
    return default


def _now_utc() -> datetime:
    return datetime.utcnow()


def _expires_at(ttl_seconds: int | None = None) -> str:
    ttl = int(ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_SECONDS)
    if ttl < 1:
        ttl = DEFAULT_TTL_SECONDS
    return (_now_utc() + timedelta(seconds=ttl)).replace(microsecond=0).isoformat() + "Z"


def _parse_expiry(expires_at: Any) -> datetime | None:
    s = _norm_str(expires_at)
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is not None:
            dt = dt.astimezone(tz=None).replace(tzinfo=None)
        return dt
    except Exception:
        try:
            return datetime.strptime(_norm_str(expires_at).replace("Z", ""), "%Y-%m-%dT%H:%M:%S")
        except Exception:
            return None


def _is_expired(expires_at: Any, now: datetime | None = None) -> bool:
    dt = _parse_expiry(expires_at)
    if dt is None:
        return False
    now_dt = now or datetime.utcnow()
    return now_dt > dt


# ── Entity resolution helpers (read-only) ────────────────────────────────────

def resolve_reservation(args: Mapping[str, Any] | None, *, for_posting: bool = False) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    rid = _int_or_none(_get(args, "reservation_id", "rid", "res_id", "id"))
    if not rid or rid <= 0:
        _add_error(out, "reservation_id is required")
        _check(out, "reservation_id_present", False)
        return out
    _check(out, "reservation_id_present", True, str(rid))
    try:
        row = services.get_reservation(rid)
    except Exception as e:
        logger.debug("resolve_reservation failed rid=%s", rid, exc_info=True)
        _add_error(out, f"Unable to load reservation #{rid}: {e}")
        _check(out, "reservation_exists", False)
        return out
    if not row:
        _add_error(out, f"Reservation #{rid} not found")
        _check(out, "reservation_exists", False)
        return out
    res = _row_to_dict(row)
    out["resolved"]["reservation"] = res
    status = _norm_str(res.get("status"))
    _check(out, "reservation_exists", True, status or "ok")
    if for_posting:
        allowed = status in ALLOWED_RES_STATUSES_FOR_POSTING
        _check(out, "reservation_status_allows_posting", allowed, status or "unknown")
        if not allowed:
            _add_error(out, f"Reservation #{rid} status '{status or 'unknown'}' does not allow posting (allowed: Reserved, Checked In)")
    return out


def resolve_guest(args: Mapping[str, Any] | None) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    gid = _int_or_none(_get(args, "guest_id", "id"))
    name = _norm_str(_get(args, "guest_name", "name", "query"))
    if gid and gid > 0:
        try:
            guest = _row_to_dict(services.get_guest_by_id(gid))
        except Exception as e:
            logger.debug("resolve_guest get_by_id failed gid=%s", gid, exc_info=True)
            _add_error(out, f"Unable to load guest #{gid}: {e}")
            _check(out, "guest_exists", False)
            return out
        if not guest:
            _add_error(out, f"Guest #{gid} not found")
            _check(out, "guest_exists", False)
            return out
        out["resolved"]["guest"] = guest
        _check(out, "guest_exists", True, f"id={gid}")
        return out

    if not name:
        _add_warning(out, "No guest_id/guest_name provided; guest will be resolved/created by reservation flow if supported")
        _check(out, "guest_resolved", False, "not provided")
        return out

    try:
        guests = services.assistant_find_guests(name, limit=8) or []
    except Exception as e:
        logger.debug("resolve_guest search failed name=%r", name, exc_info=True)
        _add_warning(out, f"Guest lookup failed for '{name}': {e}")
        _check(out, "guest_search", False, str(e))
        out["resolved"]["guest_query"] = name
        return out

    guests = [g for g in guests if g]
    out["resolved"]["guest_query"] = name
    out["resolved"]["guest_matches"] = guests
    if len(guests) == 1:
        out["resolved"]["guest"] = guests[0]
        _check(out, "guest_resolved", True, f"id={guests[0].get('id')}")
    elif len(guests) > 1:
        _add_warning(out, f"Guest name '{name}' is ambiguous ({len(guests)} matches)")
        _check(out, "guest_disambiguation_needed", False, f"{len(guests)} matches")
    else:
        _check(out, "guest_resolved", False, "no existing match (may be created)")
        _add_warning(out, f"No existing guest matched '{name}'; reservation creation may create/find guest by name")
    return out


def _match_inventory_candidate(item: Mapping[str, Any], key: str) -> bool:
    k = key.strip().lower()
    if not k:
        return False
    for field in ("item_name", "name", "sku", "barcode"):
        v = _norm_str(item.get(field)).lower()
        if v and v == k:
            return True
    try:
        iid = _int_or_none(item.get("id"))
        if iid is not None and str(iid) == key.strip():
            return True
    except Exception:
        pass
    return False


def resolve_inventory_item(args: Mapping[str, Any] | None) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    item_id = _int_or_none(_get(args, "item_id", "inv_id", "inventory_item_id", "product_id"))
    code = _norm_str(_get(args, "barcode", "sku", "code"))
    name = _norm_str(_get(args, "item_name", "name", "product_name", "query", "desc"))
    item = None

    try:
        if item_id:
            row = services.get_inventory_item(item_id)
            item = _row_to_dict(row)
            _check(out, "item_lookup_by_id", bool(item), str(item_id))
            if not item:
                _add_error(out, f"Inventory item #{item_id} not found")
        elif code:
            resolved = services.resolve_barcode(code) or {}
            if resolved.get("type") == "item" and resolved.get("item"):
                item = _row_to_dict(resolved.get("item"))
            elif resolved.get("type") == "batch" and resolved.get("item"):
                item = _row_to_dict(resolved.get("item"))
                if resolved.get("batch"):
                    out["resolved"]["resolved_batch_hint"] = _row_to_dict(resolved.get("batch"))
            _check(out, "item_lookup_by_code", bool(item), code)
            out["resolved"]["barcode_resolution"] = _row_to_dict(resolved)
            if not item:
                _add_error(out, f"Inventory item not found for code '{code}'")
        elif name:
            candidates = []
            try:
                candidates = services.search_inventory(query=name) or []
            except Exception:
                try:
                    candidates = services.get_inventory_items(query=name) or []
                except Exception:
                    candidates = []
            normed = [_row_to_dict(c) for c in candidates if c]
            out["resolved"]["inventory_matches"] = normed[:10]
            exact = [c for c in normed if _match_inventory_candidate(c, name)]
            if len(exact) == 1:
                item = exact[0]
            elif len(normed) == 1:
                item = normed[0]
            elif len(normed) > 1:
                _add_error(out, f"Inventory item '{name}' is ambiguous ({len(normed)} matches); specify item_id/sku/barcode")
            else:
                _add_error(out, f"Inventory item '{name}' not found")
            _check(out, "item_lookup_by_name", bool(item), name)
        else:
            _add_error(out, "Inventory item identifier is required (item_id/inv_id or sku/barcode or item_name)")
            _check(out, "item_identifier_present", False)
    except Exception as e:
        logger.debug("resolve_inventory_item failed args=%r", args, exc_info=True)
        _add_error(out, f"Inventory lookup failed: {e}")

    if item:
        out["resolved"]["item"] = item
        active = item.get("active", 1)
        is_active = bool(active) if active is not None else True
        _check(out, "item_active", is_active, str(active))
        if not is_active:
            _add_error(out, f"Inventory item '{item.get('item_name') or item.get('id')}' is inactive")
    return out


def resolve_batch_or_lot(item: Mapping[str, Any] | None, args: Mapping[str, Any] | None) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    if not isinstance(item, Mapping):
        return out
    item_id = _int_or_none(item.get("id"))
    batch_id = _int_or_none(_get(args, "batch_id"))
    lot_code = _norm_str(_get(args, "lot_code", "lot"))
    requested_qty = _float_or_none(_get(args, "qty", "quantity"))
    track_batches = _bool(item.get("track_batches"))

    batch = None
    try:
        if batch_id:
            b = _row_to_dict(services.get_inventory_batch(batch_id))
            if not b:
                _add_error(out, f"Batch #{batch_id} not found")
                _check(out, "batch_exists", False)
                return out
            batch = b
            _check(out, "batch_exists", True, str(batch_id))
        elif lot_code:
            resolved = services.resolve_barcode(lot_code) or {}
            b = _row_to_dict(resolved.get("batch")) if resolved.get("type") == "batch" else None
            if b:
                batch = b
                _check(out, "batch_resolved_by_lot", True, lot_code)
            else:
                _add_error(out, f"Batch/lot '{lot_code}' not found")
                _check(out, "batch_resolved_by_lot", False, lot_code)
                out["resolved"]["barcode_resolution"] = _row_to_dict(resolved)
        elif track_batches:
            _check(out, "batch_specified", False, "auto-allocation possible")
            _add_warning(out, "Batch-tracked item without explicit batch/lot; service may auto-allocate available batches")
    except Exception as e:
        logger.debug("resolve_batch_or_lot failed", exc_info=True)
        _add_error(out, f"Batch lookup failed: {e}")

    if batch:
        b_item_id = _int_or_none(batch.get("item_id"))
        if item_id and b_item_id and b_item_id != item_id:
            _add_error(out, f"Batch #{batch.get('id') or batch_id} belongs to item #{b_item_id}, not item #{item_id}")
            _check(out, "batch_belongs_to_item", False, f"{b_item_id} != {item_id}")
        else:
            _check(out, "batch_belongs_to_item", True)
        status = _norm_str(batch.get("status")).lower()
        if status and status not in {"active"}:
            _add_warning(out, f"Batch status is '{status}'")
            _check(out, "batch_status_active", False, status)
        else:
            _check(out, "batch_status_active", True, status or "active")
        rem = _float_or_none(batch.get("quantity_remaining"))
        if rem is not None:
            if requested_qty is not None and requested_qty > rem + 1e-9:
                _add_warning(out, f"Requested qty {requested_qty} exceeds batch remaining {rem}; sale may fail or allocate across batches depending on service behavior")
                _check(out, "batch_has_requested_qty", False, f"remaining={rem}")
            else:
                _check(out, "batch_has_requested_qty", True, f"remaining={rem}")
        out["resolved"]["batch"] = batch
    return out


def resolve_room(args: Mapping[str, Any] | None) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    room_id = _int_or_none(_get(args, "room_id"))
    room_query = _norm_str(_get(args, "room", "room_number", "room_query"))
    try:
        if room_id:
            room = _row_to_dict(services.get_room(room_id))
            if not room:
                _add_error(out, f"Room #{room_id} not found")
                _check(out, "room_exists", False)
            else:
                out["resolved"]["room"] = room
                _check(out, "room_exists", True, str(room_id))
            return out
        if room_query:
            rooms = [_row_to_dict(r) for r in (services.get_all_rooms() or []) if r]
            matches = [r for r in rooms if _norm_str(r.get("room_number")).lower() == room_query.lower()]
            if not matches:
                matches = [r for r in rooms if room_query.lower() in _norm_str(r.get("room_number")).lower()]
            if len(matches) == 1:
                out["resolved"]["room"] = matches[0]
                _check(out, "room_resolved", True, _norm_str(matches[0].get("room_number")))
            elif len(matches) > 1:
                _add_error(out, f"Room '{room_query}' is ambiguous ({len(matches)} matches)")
                out["resolved"]["room_matches"] = matches[:10]
                _check(out, "room_disambiguation_needed", False)
            else:
                _add_error(out, f"Room '{room_query}' not found")
                _check(out, "room_resolved", False)
            return out
        _add_error(out, "room_id or room/room_number is required")
        _check(out, "room_identifier_present", False)
    except Exception as e:
        logger.debug("resolve_room failed", exc_info=True)
        _add_error(out, f"Room lookup failed: {e}")
    return out


def resolve_rate_plan(args: Mapping[str, Any] | None) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    rate_plan_id = _int_or_none(_get(args, "rate_plan_id", "plan_id"))
    try:
        plans = [_row_to_dict(p) for p in (services.get_all_rate_plans() or []) if p]
        out["resolved"]["rate_plans_count"] = len(plans)
        if rate_plan_id is None:
            _check(out, "rate_plan_optional", True, "none provided")
            return out
        match = next((p for p in plans if _int_or_none(p.get("id")) == rate_plan_id), None)
        if not match:
            _add_error(out, f"Rate plan #{rate_plan_id} not found")
            _check(out, "rate_plan_exists", False)
        else:
            out["resolved"]["rate_plan"] = match
            _check(out, "rate_plan_exists", True, _norm_str(match.get("name")))
    except Exception as e:
        logger.debug("resolve_rate_plan failed", exc_info=True)
        _add_error(out, f"Rate plan lookup failed: {e}")
    return out


def resolve_activity(args: Mapping[str, Any] | None) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    aid = _int_or_none(_get(args, "activity_id", "id"))
    if not aid:
        _add_error(out, "activity_id is required")
        _check(out, "activity_id_present", False)
        return out
    try:
        act = _row_to_dict(services.get_activity(aid))
        if not act:
            _add_error(out, f"Activity #{aid} not found")
            _check(out, "activity_exists", False)
            return out
        out["resolved"]["activity"] = act
        active = bool(act.get("active", 1))
        _check(out, "activity_exists", True, _norm_str(act.get("name")))
        _check(out, "activity_active", active)
        if not active:
            _add_error(out, f"Activity #{aid} is inactive")
    except Exception as e:
        logger.debug("resolve_activity failed", exc_info=True)
        _add_error(out, f"Activity lookup failed: {e}")
    return out


def check_availability(args: Mapping[str, Any] | None, room_id: int | None = None) -> dict[str, Any]:
    out = {"ok": True, "errors": [], "warnings": [], "checks": [], "resolved": {}, "preview": {}}
    rid = room_id or _int_or_none(_get(args, "room_id"))
    ci = _norm_str(_get(args, "check_in", "check_in_date", "start"))
    co = _norm_str(_get(args, "check_out", "check_out_date", "end"))
    exclude_res_id = _int_or_none(_get(args, "exclude_reservation_id", "exclude_res_id"))
    if not rid or not ci or not co:
        _check(out, "availability_inputs_present", False)
        return out
    d1 = _parse_iso(ci)
    d2 = _parse_iso(co)
    if not d1 or not d2:
        _add_error(out, "check_in/check_out must be valid YYYY-MM-DD dates")
        _check(out, "dates_parse", False)
        return out
    if d2 <= d1:
        _add_error(out, "check_out must be after check_in")
        _check(out, "dates_order", False)
        return out
    nights = (d2 - d1).days
    out["preview"] = {"nights": nights}
    _check(out, "dates_order", True, f"{nights} night(s)")
    try:
        available = bool(services.check_room_availability(rid, ci, co, exclude_res_id=exclude_res_id))
        _check(out, "room_available", available)
        if not available:
            _add_error(out, f"Room #{rid} is not available for {ci} to {co}")
        try:
            avail_rooms = services.get_rooms_available_between(ci, co) or []
            avail_ids = {_int_or_none((r["id"] if hasattr(r, "keys") else r.get("id"))) for r in [_row_to_dict(x) for x in avail_rooms]}
            out["resolved"]["available_room_ids"] = sorted([i for i in avail_ids if i])
        except Exception:
            logger.debug("check_availability: get_rooms_available_between failed", exc_info=True)
    except Exception as e:
        logger.debug("check_availability failed", exc_info=True)
        _add_error(out, f"Availability check failed: {e}")
        _check(out, "room_available", False, str(e))
    return out


# ── Preview builders / prechecks ──────────────────────────────────────────────

def _merge_child(out: dict[str, Any], child: Mapping[str, Any] | None, *, namespace: str | None = None) -> None:
    if not isinstance(child, Mapping):
        return
    out["errors"].extend(child.get("errors") or [])
    out["warnings"].extend(child.get("warnings") or [])
    checks = child.get("checks") or []
    if namespace:
        for c in checks:
            cc = dict(c)
            if cc.get("name"):
                cc["name"] = f"{namespace}.{cc['name']}"
            out["checks"].append(cc)
    else:
        out["checks"].extend(checks)
    resolved = child.get("resolved") or {}
    if namespace:
        out["resolved"].setdefault(namespace, {})
        if isinstance(out["resolved"][namespace], dict):
            out["resolved"][namespace].update(resolved)
    else:
        out["resolved"].update(resolved)
    if child.get("ok") is False:
        out["ok"] = False


def _money_round(v: float | None) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), 2)
    except Exception:
        return v


def preview_add_pos_item(args: Mapping[str, Any] | None) -> dict[str, Any]:
    args = dict(args or {})
    out = _ok_base("preview_add_pos_item", args)
    rid = _int_or_none(_get(args, "reservation_id", "rid", "res_id"))
    qty = _float_or_none(_get(args, "qty", "quantity"))
    price_arg = _float_or_none(_get(args, "price", "unit_price", "selling_price"))
    desc = _norm_str(_get(args, "desc", "item_description", "description", "item_name", "name"))

    res = resolve_reservation({"reservation_id": rid}, for_posting=True)
    _merge_child(out, res)
    item_res = resolve_inventory_item(args)
    _merge_child(out, item_res)
    item = (item_res.get("resolved") or {}).get("item") if isinstance(item_res.get("resolved"), dict) else None
    batch_res = resolve_batch_or_lot(item or {}, args)
    _merge_child(out, batch_res)

    if qty is None or qty <= 0:
        _add_error(out, "qty must be a positive number")
        _check(out, "qty_positive", False)
    else:
        _check(out, "qty_positive", True, str(qty))

    allow_negative = False
    stock_qty = None
    try:
        allow_negative = str(services.get_setting("allow_negative_stock", "false")).lower() == "true"
    except Exception:
        logger.debug("preview_add_pos_item: failed to read allow_negative_stock", exc_info=True)
    if isinstance(item, dict):
        stock_qty = _float_or_none(item.get("stock_qty"))
        if desc == "":
            desc = _norm_str(item.get("item_name") or item.get("name"))
        if price_arg is None:
            price_arg = _float_or_none(item.get("selling_price"))
    out["resolved"]["allow_negative_stock"] = allow_negative

    if not desc:
        _add_error(out, "desc/item description is required")
        _check(out, "description_present", False)
    else:
        _check(out, "description_present", True)

    batch = (batch_res.get("resolved") or {}).get("batch") if isinstance(batch_res.get("resolved"), dict) else None
    if isinstance(item, dict) and qty is not None and qty > 0:
        if batch and _float_or_none(batch.get("quantity_remaining")) is not None:
            pass
        elif stock_qty is not None and qty > stock_qty + 1e-9 and not allow_negative:
            _add_error(out, f"Insufficient stock: {stock_qty} available, {qty} requested")
            _check(out, "stock_sufficient", False, f"available={stock_qty}")
        else:
            _check(out, "stock_sufficient", True, f"available={stock_qty}")

    subtotal = None
    if qty is not None and price_arg is not None:
        try:
            subtotal = _money_round(float(price_arg) * float(qty))
        except Exception:
            subtotal = None

    tax_preview = None
    if subtotal is not None:
        try:
            t = services.calculate_tax(subtotal, 0)
            if isinstance(t, dict):
                tax_preview = {
                    "net": _money_round(_float_or_none(t.get("net"))),
                    "service": _money_round(_float_or_none(t.get("service"))),
                    "vat": _money_round(_float_or_none(t.get("vat"))),
                    "city_tax": _money_round(_float_or_none(t.get("city_tax"))),
                    "gross": _money_round(_float_or_none(t.get("gross"))),
                }
        except Exception:
            logger.debug("preview_add_pos_item: tax preview unavailable", exc_info=True)

    out["preview"] = {
        "type": "preview_add_pos_item",
        "reservation_id": rid,
        "item_id": _int_or_none(item.get("id")) if isinstance(item, dict) else _int_or_none(_get(args, "item_id", "inv_id")),
        "item_name": (item.get("item_name") if isinstance(item, dict) else None) or desc or None,
        "description": desc or None,
        "qty": qty,
        "unit_price": _money_round(price_arg),
        "estimated_subtotal": subtotal,
        "tax_preview": tax_preview,
        "batch_id": _int_or_none((batch or {}).get("id")) if isinstance(batch, dict) else _int_or_none(_get(args, "batch_id")),
        "lot_code": _norm_str((batch or {}).get("lot_code")) if isinstance(batch, dict) else (_norm_str(_get(args, "lot_code")) or None),
        "stock_qty": stock_qty,
        "allow_negative_stock": allow_negative,
    }
    return out


def preview_complete_walkin_sale(args: Mapping[str, Any] | None) -> dict[str, Any]:
    args = dict(args or {})
    out = _ok_base("preview_complete_walkin_sale", args)
    items = _get(args, "items") or []
    method = _norm_str(_get(args, "method", "payment_method"))
    notes = _norm_str(_get(args, "notes"))
    payment_type = _norm_str(_get(args, "payment_type")) or "Payment"
    if not isinstance(items, list) or not items:
        _add_error(out, "items must be a non-empty list")
        _check(out, "items_present", False)
        items = []
    else:
        _check(out, "items_present", True, str(len(items)))
    if not method:
        _add_error(out, "payment method is required")
        _check(out, "payment_method_present", False)
    else:
        _check(out, "payment_method_present", True, method)
        if method not in ALLOWED_PAYMENT_METHODS:
            _add_warning(out, f"Payment method '{method}' is uncommon; ensure it is supported")

    normalized_items = []
    total = 0.0
    for idx, it in enumerate(items):
        it = dict(it or {})
        it_args = {
            "item_id": _get(it, "inv_id", "item_id", "inventory_item_id"),
            "barcode": _get(it, "barcode", "sku"),
            "item_name": _get(it, "desc", "item_name", "name"),
            "batch_id": _get(it, "batch_id"),
            "lot_code": _get(it, "lot_code"),
            "qty": _get(it, "qty", "quantity"),
        }
        child = _ok_base("walkin_item", it_args)
        item_res = resolve_inventory_item(it_args)
        _merge_child(child, item_res)
        item = (item_res.get("resolved") or {}).get("item") if isinstance(item_res.get("resolved"), dict) else None
        batch_res = resolve_batch_or_lot(item or {}, it_args)
        _merge_child(child, batch_res)
        qty = _float_or_none(_get(it, "qty", "quantity"))
        price = _float_or_none(_get(it, "price", "unit_price"))
        desc = _norm_str(_get(it, "desc", "description"))
        if isinstance(item, dict):
            if not desc:
                desc = _norm_str(item.get("item_name"))
            if price is None:
                price = _float_or_none(item.get("selling_price"))
        if qty is None or qty <= 0:
            _add_error(child, f"Item {idx + 1}: qty must be positive")
        if not desc:
            _add_error(child, f"Item {idx + 1}: desc is required")
        if price is None or price < 0:
            _add_error(child, f"Item {idx + 1}: price is required")
        line_total = None
        if qty is not None and price is not None:
            line_total = _money_round(qty * price)
            if line_total is not None:
                total += float(line_total)
        batch_resolved = (batch_res.get("resolved") or {}) if isinstance(batch_res.get("resolved"), dict) else {}
        batch_obj = batch_resolved.get("batch") if isinstance(batch_resolved.get("batch"), dict) else {}
        child_preview = {
            "index": idx,
            "inv_id": _int_or_none((item or {}).get("id")) if isinstance(item, dict) else _int_or_none(_get(it, "inv_id", "item_id")),
            "desc": desc or None,
            "qty": qty,
            "price": _money_round(price),
            "line_total": line_total,
            "batch_id": _int_or_none(batch_obj.get("id")) or _int_or_none(_get(it, "batch_id")),
            "lot_code": _norm_str(batch_obj.get("lot_code")) or (_norm_str(_get(it, "lot_code")) or None),
            "errors": list(child.get("errors") or []),
            "warnings": list(child.get("warnings") or []),
        }
        normalized_items.append(child_preview)
        out["errors"].extend([f"Item {idx + 1}: {e}" if not str(e).startswith("Item ") else str(e) for e in (child.get("errors") or [])])
        out["warnings"].extend([f"Item {idx + 1}: {w}" if not str(w).startswith("Item ") else str(w) for w in (child.get("warnings") or [])])
        for c in (child.get("checks") or []):
            cc = dict(c)
            cc["name"] = f"item[{idx}].{cc.get('name')}"
            out["checks"].append(cc)

    if out["errors"]:
        out["ok"] = False
    out["preview"] = {
        "type": "preview_complete_walkin_sale",
        "items": normalized_items,
        "item_count": len(normalized_items),
        "estimated_total": _money_round(total),
        "method": method or None,
        "payment_type": payment_type,
        "notes": notes or "",
    }
    return out


def preview_record_payment(args: Mapping[str, Any] | None) -> dict[str, Any]:
    args = dict(args or {})
    out = _ok_base("preview_record_payment", args)
    rid = _int_or_none(_get(args, "reservation_id", "rid", "res_id"))
    amount = _float_or_none(_get(args, "amount"))
    method = _norm_str(_get(args, "method", "payment_method"))
    notes = _norm_str(_get(args, "notes"))
    payment_type = _norm_str(_get(args, "payment_type")) or "Payment"

    res = resolve_reservation({"reservation_id": rid}, for_posting=True)
    _merge_child(out, res)
    if amount is None or amount <= 0:
        _add_error(out, "amount must be positive")
        _check(out, "amount_positive", False)
    else:
        _check(out, "amount_positive", True, str(amount))
    if not method:
        _add_error(out, "method is required")
        _check(out, "method_present", False)
    else:
        _check(out, "method_present", True, method)

    balance = None
    try:
        if rid:
            bd = services.get_balance_breakdown(rid)
            if isinstance(bd, dict):
                balance = _float_or_none(bd.get("balance_due") if bd.get("balance_due") is not None else bd.get("balance"))
            elif bd is not None:
                balance = _float_or_none(bd)
    except Exception:
        try:
            if rid:
                balance = _float_or_none(services.calculate_guest_balance(rid))
        except Exception:
            logger.debug("preview_record_payment: balance lookup failed rid=%s", rid, exc_info=True)
    if amount is not None and balance is not None and amount - balance > 0.01:
        _add_warning(out, f"Payment amount {amount} exceeds current balance due {balance}")

    out["preview"] = {
        "type": "preview_record_payment",
        "reservation_id": rid,
        "amount": _money_round(amount),
        "method": method or None,
        "payment_type": payment_type,
        "notes": notes or "",
        "current_balance_due": _money_round(balance),
        "projected_balance_due": _money_round((balance - amount) if (balance is not None and amount is not None) else None),
    }
    return out


def preview_book_activity(args: Mapping[str, Any] | None) -> dict[str, Any]:
    args = dict(args or {})
    out = _ok_base("preview_book_activity", args)
    rid = _int_or_none(_get(args, "reservation_id", "rid", "res_id"))
    aid = _int_or_none(_get(args, "activity_id", "id"))
    qty = _int_or_none(_get(args, "quantity", "qty")) or 1
    scheduled_at = _norm_str(_get(args, "scheduled_at"))
    notes = _norm_str(_get(args, "notes"))

    res = resolve_reservation({"reservation_id": rid}, for_posting=True)
    _merge_child(out, res)
    act_res = resolve_activity({"activity_id": aid})
    _merge_child(out, act_res)
    if qty <= 0:
        _add_error(out, "quantity must be positive")
        _check(out, "quantity_positive", False)
    else:
        _check(out, "quantity_positive", True, str(qty))

    activity = (act_res.get("resolved") or {}).get("activity") if isinstance(act_res.get("resolved"), dict) else None
    unit_price = _float_or_none(activity.get("unit_price")) if isinstance(activity, dict) else None
    est = _money_round(unit_price * qty) if unit_price is not None else None
    out["preview"] = {
        "type": "preview_book_activity",
        "reservation_id": rid,
        "activity_id": aid,
        "activity_name": _norm_str(activity.get("name")) if isinstance(activity, dict) else None,
        "quantity": qty,
        "unit_price": _money_round(unit_price),
        "estimated_total": est,
        "scheduled_at": scheduled_at or None,
        "notes": notes or "",
    }
    return out


def preview_create_reservation(args: Mapping[str, Any] | None) -> dict[str, Any]:
    args = dict(args or {})
    out = _ok_base("preview_create_reservation", args)
    guest_name = _norm_str(_get(args, "guest_name", "name"))
    guest_id = _int_or_none(_get(args, "guest_id"))
    source = _norm_str(_get(args, "source")) or "Direct"
    room_id = _int_or_none(_get(args, "room_id"))
    rate_plan_id = _int_or_none(_get(args, "rate_plan_id"))
    rate = _float_or_none(_get(args, "rate", "agreed_nightly_rate", "nightly_rate"))
    ci = _norm_str(_get(args, "check_in", "check_in_date"))
    co = _norm_str(_get(args, "check_out", "check_out_date"))
    deposit = _float_or_none(_get(args, "deposit", "deposit_amount")) or 0.0

    if not guest_name and not guest_id:
        _add_error(out, "guest_name or guest_id is required")
        _check(out, "guest_identifier_present", False)
    else:
        _check(out, "guest_identifier_present", True)
        _merge_child(out, resolve_guest({"guest_id": guest_id, "guest_name": guest_name}), namespace="guest_resolution")

    room_res = resolve_room({"room_id": room_id, "room_number": _get(args, "room_number", "room")})
    _merge_child(out, room_res)
    resolved_room = (room_res.get("resolved") or {}).get("room") if isinstance(room_res.get("resolved"), dict) else None
    if resolved_room and not room_id:
        room_id = _int_or_none(resolved_room.get("id"))
        out["resolved"]["room_id"] = room_id

    rp_res = resolve_rate_plan({"rate_plan_id": rate_plan_id})
    _merge_child(out, rp_res)

    if rate is None or rate < 0:
        if isinstance(resolved_room, dict):
            base = _float_or_none(resolved_room.get("base_price"))
            if base is not None:
                rate = base
                _add_warning(out, "Rate not provided; using room base_price for preview (service may require explicit rate)")
    if rate is None:
        _add_error(out, "rate/agreed_nightly_rate is required")
        _check(out, "rate_present", False)
    else:
        _check(out, "rate_present", True, str(rate))

    avail_res = check_availability({"room_id": room_id, "check_in": ci, "check_out": co})
    _merge_child(out, avail_res)
    nights = ((avail_res.get("preview") or {}).get("nights") if isinstance(avail_res.get("preview"), dict) else None)
    if ci and not _parse_iso(ci):
        _add_error(out, "check_in must be YYYY-MM-DD")
    if co and not _parse_iso(co):
        _add_error(out, "check_out must be YYYY-MM-DD")
    if not ci or not co:
        _add_error(out, "check_in and check_out are required")

    est_total = _money_round(rate * nights) if (rate is not None and isinstance(nights, int) and nights > 0) else None
    out["preview"] = {
        "type": "preview_create_reservation",
        "guest_id": guest_id or (((out.get("resolved") or {}).get("guest_resolution") or {}).get("guest") or {}).get("id") if isinstance(out.get("resolved"), dict) else None,
        "guest_name": guest_name or None,
        "source": source,
        "room_id": room_id,
        "room_number": _norm_str(resolved_room.get("room_number")) if isinstance(resolved_room, dict) else None,
        "rate_plan_id": rate_plan_id,
        "rate": _money_round(rate),
        "check_in": ci or None,
        "check_out": co or None,
        "nights": nights,
        "estimated_room_total": est_total,
        "deposit": _money_round(deposit),
    }
    return out


_PREVIEW_BUILDERS = {
    "preview_add_pos_item": preview_add_pos_item,
    "propose_add_pos_item": preview_add_pos_item,
    "execute_add_pos_item": preview_add_pos_item,
    "preview_complete_walkin_sale": preview_complete_walkin_sale,
    "propose_complete_walkin_sale": preview_complete_walkin_sale,
    "execute_complete_walkin_sale": preview_complete_walkin_sale,
    "preview_record_payment": preview_record_payment,
    "propose_record_payment": preview_record_payment,
    "execute_record_payment": preview_record_payment,
    "preview_book_activity": preview_book_activity,
    "propose_book_activity": preview_book_activity,
    "execute_book_activity": preview_book_activity,
    "preview_create_reservation": preview_create_reservation,
    "propose_create_reservation": preview_create_reservation,
    "execute_create_reservation": preview_create_reservation,
}


def build_prechecks(action_type: str, args: Mapping[str, Any] | None = None, ui_context: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Build read-only prechecks/preview for a supported action type."""
    action_type = _norm_str(action_type)
    args = dict(args or {})
    builder = _PREVIEW_BUILDERS.get(action_type)
    if not builder:
        return {
            "ok": False,
            "errors": [f"Unsupported action type '{action_type}'"],
            "warnings": [],
            "checks": [],
            "resolved": {},
            "preview": {"type": action_type, "args": args},
        }
    try:
        out = builder(args)
    except Exception as e:
        logger.exception("build_prechecks failed action_type=%s", action_type)
        out = _ok_base(action_type, args)
        _add_error(out, f"Precheck failed: {e}")
    out.setdefault("ok", not bool(out.get("errors")))
    out.setdefault("errors", [])
    out.setdefault("warnings", [])
    out.setdefault("checks", [])
    out.setdefault("resolved", {})
    preview = out.setdefault("preview", {})
    if isinstance(preview, dict):
        preview.setdefault("type", action_type)
    if ui_context:
        out["resolved"]["ui_context"] = dict(ui_context)
    return out


def normalize_action_args(action_type: str, args: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Best-effort normalization for common arg aliases (non-destructive)."""
    a = dict(args or {})
    at = _norm_str(action_type)
    if "reservation_id" not in a:
        for k in ("rid", "res_id"):
            if a.get(k) is not None:
                a["reservation_id"] = _int_or_none(a.get(k)) or a.get(k)
                break
    if "guest_id" not in a and a.get("id") is not None and at in {"propose_create_reservation", "preview_create_reservation"}:
        a["guest_id"] = _int_or_none(a.get("id")) or a.get("id")
    if at in {"propose_add_pos_item", "preview_add_pos_item", "execute_add_pos_item"}:
        if "item_id" not in a and a.get("inv_id") is not None:
            a["item_id"] = _int_or_none(a.get("inv_id")) or a.get("inv_id")
        if "desc" not in a:
            for k in ("item_description", "description", "item_name", "name"):
                if a.get(k):
                    a["desc"] = a.get(k)
                    break
        if "qty" not in a and a.get("quantity") is not None:
            a["qty"] = a.get("quantity")
    if at in {"propose_record_payment", "execute_record_payment"}:
        if "payment_type" not in a or not a.get("payment_type"):
            a["payment_type"] = "Payment"
    if at in {"propose_complete_walkin_sale", "execute_complete_walkin_sale"}:
        if "payment_type" not in a or not a.get("payment_type"):
            a["payment_type"] = "Payment"
        items = a.get("items")
        if isinstance(items, list):
            norm_items = []
            for it in items:
                if not isinstance(it, dict):
                    norm_items.append(it)
                    continue
                it2 = dict(it)
                if "inv_id" not in it2 and it2.get("item_id") is not None:
                    it2["inv_id"] = it2.get("item_id")
                if "desc" not in it2 and it2.get("item_name") is not None:
                    it2["desc"] = it2.get("item_name")
                norm_items.append(it2)
            a["items"] = norm_items
    return a
