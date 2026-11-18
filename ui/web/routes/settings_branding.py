from __future__ import annotations
import logging
from pathlib import Path
from fastapi import APIRouter, Request
from app_core import services, updater
from ui.web import deps
from ui.web.rendering import render
from ui.web.route_utils import flash_redirect

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/unlock")
def unlock_page(request: Request, next: str = "/"):
    from ui.web.route_utils import safe_local_path
    return render(request, "unlock.html", {"title": "Unlock", "next": safe_local_path(next, "/")})

@router.post("/unlock")
async def unlock_post(request: Request):
    from urllib.parse import quote
    from ui.web.route_utils import safe_local_path
    form = await deps.get_form(request)
    nxt = safe_local_path(form.get("next"), "/")
    try:
        deps.enforce_auth_rate_limit(request, "unlock", limit=8, window_sec=300)
    except Exception as e:
        logger.warning("web_auth_route_event event=unlock_rate_limited")
        return flash_redirect(request, "/unlock?next=" + quote(nxt, safe="/"), str(getattr(e, "detail", e)), "error")
    if deps.unlock(request, form.get("password", "")):
        return flash_redirect(request, nxt, "Unlocked", "success")
    else:
        return flash_redirect(request, "/unlock?next=" + quote(nxt, safe="/"), "Invalid super password", "error")


@router.get("/unlock/recovery")
def unlock_recovery_page(request: Request, next: str = "/"):
    from ui.web.route_utils import safe_local_path
    return render(request, "unlock_recovery.html", {"title": "Recover Access", "next": safe_local_path(next, "/")})


@router.post("/unlock/recovery")
async def unlock_recovery_post(request: Request):
    from urllib.parse import quote
    from ui.web.route_utils import safe_local_path
    form = await deps.get_form(request)
    nxt = safe_local_path(form.get("next"), "/")
    try:
        deps.enforce_auth_rate_limit(request, "recovery", limit=5, window_sec=900)
        services.use_recovery_code(form.get("code", ""), form.get("new_password", ""), form.get("confirm_password", ""))
        deps.clear_auth_failures(request, "recovery")
        # Use new password to establish per-browser unlock (also best-effort backend compat).
        if not deps.unlock(request, form.get("new_password", "")):
            raise RuntimeError("Failed to establish unlocked session after recovery.")
        return flash_redirect(request, nxt, "Password reset with recovery code. Unlocked.", "success")
    except Exception as e:
        msg = str(getattr(e, "detail", e))
        # Count likely credential/recovery failures as attempts (not validation-only 429).
        if "Too many attempts" not in msg:
            deps.record_auth_failure(request, "recovery", window_sec=900)
        return flash_redirect(request, "/unlock/recovery?next=" + quote(nxt, safe="/"), msg, "error")

@router.post("/lock")
async def lock_post(request: Request):
    form = await deps.get_form(request)
    deps.lock(request)
    return flash_redirect(request, "/", "Locked", "info")

@router.get("/settings")
def settings(request: Request, tab: str = "general"):
    rooms = services.get_all_rooms()
    try:
        demo_presets = services.list_demo_data_presets()
    except Exception:
        demo_presets = []
    try:
        update_local = updater.get_local_state()
    except Exception:
        logger.warning("web.settings: failed to get local update state", exc_info=True)
        update_local = {}
    update_check = deps.get_session(request).pop("last_update_check", None)
    update_apply = deps.get_session(request).pop("last_update_apply", None)
    allowed = {"general","data","rooms","updates"}
    tab = (tab or "general").strip().lower()
    if tab not in allowed: tab = "general"
    return render(request, "settings/index.html", {"title":"Settings / Branding",
        "catalog": services.SETTINGS_CATALOG, "values": services.get_all_settings(),
        "rooms": rooms, "demo_presets": demo_presets,
        "update_local": update_local, "update_check": update_check, "update_apply": update_apply,
        "active_tab": tab,
        "brand": {"name": services.get_app_display_name(), "tagline": services.get_app_tagline(),
                  "animation": services.get_loading_animation(), "configured": services.is_branding_configured(),
                  "gift": services.get_gift_attribution()}})


@router.get("/setup")
def setup_wizard(request: Request):
    # First-run branding wizard (web). Accessible without unlock.
    if services.is_branding_configured():
        return flash_redirect(request, "/", "Setup already completed.", "info")
    brand = {
        "name": services.get_app_display_name(),
        "tagline": services.get_app_tagline(),
        "animation": services.get_loading_animation(),
        "configured": services.is_branding_configured(),
        "gift": services.get_gift_attribution(),
    }
    return render(request, "setup_wizard.html", {"title": "Setup Wizard", "brand": brand, "allow_skip": False})


@router.post("/setup")
async def setup_wizard_post(request: Request):
    form = await deps.get_form(request)
    try:
        name = (form.get("display_name", "") or "").strip()
        if not name:
            raise ValueError("Display name is required.")
        services.apply_branding_settings(name, form.get("tagline", ""), form.get("animation", "reveal"), sync_property_name=True)
        deps.add_flash(request, "Setup saved. Welcome!", "success")
        r = deps.redirect("/")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        r = deps.redirect("/setup")
    deps.save_session(r, deps.get_session(request))
    return r

@router.post("/settings/branding")
async def branding(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.apply_branding_settings(form.get("display_name",""), form.get("tagline",""), form.get("animation","reveal"))
        deps.add_flash(request, "Branding updated", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings?tab=general"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/settings/save")
async def save_settings(request: Request):
    form = await deps.require_unlocked_form(request)
    changes = {}
    for k in services.SETTINGS_CATALOG.keys():
        if k in form and k != "schema_version":
            changes[k] = form.get(k)
    try:
        count, errs = services.save_settings(changes, deps.service_password(request, form))
        if errs: deps.add_flash(request, "; ".join(errs), "error")
        else: deps.add_flash(request, f"Saved {count} settings", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings?tab=general"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/settings/backup")
async def backup(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        path = services.backup_now()
        deps.add_flash(request, f"Backup: {Path(path).name}", "success")
        logger.info("web_backup_event event=backup_success filename=%s", Path(path).name)
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        logger.warning("web_backup_event event=backup_failure error=%s", str(e)[:240])
    r=deps.redirect("/settings?tab=general"); deps.save_session(r, deps.get_session(request)); return r

@router.post("/settings/demo-preset")
async def demo_preset(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        res = services.apply_demo_data_preset(form.get("preset","analysis_small"), deps.service_password(request, form), form.get("confirm",""), allow_existing=False)
        deps.add_flash(request, f"Demo preset applied: {res['created']}", "success")
    except Exception as e: deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings?tab=data"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/reset")
async def reset_settings(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        if str(form.get("confirm", "")).strip().upper() != "RESET DEFAULTS":
            raise ValueError("Confirmation text must be exactly: RESET DEFAULTS")
        services.reset_to_defaults(deps.service_password(request, form))
        deps.add_flash(request, "Settings reset to defaults (schema/language preserved).", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r=deps.redirect("/settings?tab=data"); deps.save_session(r, deps.get_session(request)); return r


@router.get("/settings/rate-plans")
def rate_plans(request: Request):
    plans = []
    for p in services.get_all_rate_plans():
        try:
            usage = services.count_reservations_using_plan(p["id"])
        except Exception:
            usage = None
        plans.append({"id": p["id"], "name": p["name"], "price_modifier": p["price_modifier"], "usage_count": usage})
    try:
        rules = services.list_rate_rules(active_only=False)
    except Exception:
        rules = []
    try:
        room_types = sorted({(r["room_type"] or "").strip() for r in services.get_all_rooms()} - {""})
    except Exception:
        room_types = []
    return render(request, "settings/rate_plans.html", {"title": "Rate Plans & Pricing Rules", "plans": plans, "rules": rules, "room_types": room_types})


@router.post("/settings/rate-plans/add")
async def rate_plan_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.add_rate_plan(form.get("name", ""), float(form.get("modifier") or 0), deps.service_password(request, form))
        deps.add_flash(request, "Rate plan added.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/rate-plans"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/rate-plans/{plan_id}/edit")
async def rate_plan_edit(request: Request, plan_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.update_rate_plan(plan_id, form.get("name", ""), float(form.get("modifier") or 0), deps.service_password(request, form))
        deps.add_flash(request, "Rate plan updated.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/rate-plans"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/rate-plans/{plan_id}/delete")
async def rate_plan_delete(request: Request, plan_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.delete_rate_plan(plan_id, deps.service_password(request, form))
        deps.add_flash(request, "Rate plan deleted.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/rate-plans"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/rate-rules/add")
async def rate_rule_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.add_rate_rule(
            name=form.get("name", "Rate rule"),
            adjustment_type=form.get("adjustment_type", "amount"),
            adjustment_value=float(form.get("adjustment_value") or 0),
            rate_plan_id=int(form["rate_plan_id"]) if form.get("rate_plan_id") else None,
            room_type=form.get("room_type", ""),
            source=form.get("source", ""),
            market_segment=form.get("market_segment", ""),
            start_date=form.get("start_date", ""),
            end_date=form.get("end_date", ""),
            min_nights=int(form["min_nights"]) if form.get("min_nights") else None,
            max_nights=int(form["max_nights"]) if form.get("max_nights") else None,
            priority=int(form.get("priority") or 100),
            active=bool(form.get("active")),
        )
        deps.add_flash(request, "Rate rule added.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/rate-plans"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/rate-rules/{rule_id}/edit")
async def rate_rule_edit(request: Request, rule_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.update_rate_rule(
            rule_id,
            name=form.get("name", "Rate rule"),
            adjustment_type=form.get("adjustment_type", "amount"),
            adjustment_value=float(form.get("adjustment_value") or 0),
            rate_plan_id=int(form["rate_plan_id"]) if form.get("rate_plan_id") else None,
            room_type=form.get("room_type", ""),
            source=form.get("source", ""),
            market_segment=form.get("market_segment", ""),
            start_date=form.get("start_date", ""),
            end_date=form.get("end_date", ""),
            min_nights=int(form["min_nights"]) if form.get("min_nights") else None,
            max_nights=int(form["max_nights"]) if form.get("max_nights") else None,
            priority=int(form.get("priority") or 100),
            active=bool(form.get("active")),
        )
        deps.add_flash(request, "Rate rule updated.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/rate-plans"); deps.save_session(r, deps.get_session(request)); return r



@router.post("/settings/users/login")
async def settings_users_login(request: Request):
    form = await deps.require_csrf_form(request)
    try:
        user = services.verify_user_password(form.get("username", ""), form.get("user_password", ""))
        if not user:
            deps.add_flash(request, "Invalid user credentials or inactive user.", "error")
        else:
            deps.set_current_user(request, user)
            try:
                services._audit("user_session_login", "user", user.get("id"), user.get("username"), actor=user.get("username"), actor_user_id=user.get("id"), actor_role=user.get("role") or "")
            except Exception:
                pass
            deps.add_flash(request, f"Logged in as {user.get('display_name') or user.get('username')} ({user.get('role')}). Audit actor context updated for this session.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/users"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/users/logout")
async def settings_users_logout(request: Request):
    form = await deps.require_csrf_form(request)
    try:
        u = deps.get_current_user(request)
        deps.clear_current_user(request)
        if u:
            try:
                services._audit("user_session_logout", "user", u.get("id"), u.get("username"), actor=u.get("username"), actor_user_id=u.get("id"), actor_role=u.get("role") or "")
            except Exception:
                pass
        deps.add_flash(request, "User session cleared. Existing unlock flow unchanged.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/users"); deps.save_session(r, deps.get_session(request)); return r


@router.get("/settings/health")
def settings_health(request: Request):
    deps.require_unlock(request)
    result = services.verify_ledger_integrity()
    return render(request, "settings/health.html", {"title": "Health Check", "result": result})


@router.get("/settings/users")
def settings_users(request: Request):
    deps.require_unlock(request)
    try:
        users = services.list_users(active_only=False)
    except Exception:
        users = []
    try:
        roles = services.list_roles()
    except Exception:
        roles = []
    return render(request, "settings/users.html", {"title": "Users & Roles", "users": users, "roles": roles})


@router.post("/settings/users/add")
async def settings_users_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.create_user(
            form.get("username", ""),
            display_name=form.get("display_name", ""),
            role=form.get("role", "staff"),
            password=form.get("new_password", ""),
            active=bool(form.get("active")),
        )
        deps.add_flash(request, "User created (RBAC groundwork; existing unlock flow unchanged).", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/users"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/users/{user_id}/edit")
async def settings_users_edit(request: Request, user_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.update_user(
            user_id,
            display_name=form.get("display_name"),
            role=form.get("role"),
            active=bool(form.get("active")),
            password=(form.get("new_password") or None),
        )
        deps.add_flash(request, "User updated.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/users"); deps.save_session(r, deps.get_session(request)); return r



@router.post("/settings/seed/export")
async def seed_export(request: Request):
    deps.require_unlock(request)
    form = await deps.get_form(request)
    try:
        path = services.export_seed_config()
        name = Path(path).name
        deps.add_flash(request, f"Seed config exported: {name}. Download it, edit JSON, then upload for first-time seed.", "success")
        r = deps.redirect(f"/files/export/{name}")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        r = deps.redirect("/settings?tab=data")
    deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/seed/import")
async def seed_import(request: Request):
    deps.require_unlock(request)
    form = await deps.get_form(request)
    upload = form.get("seed_file")
    try:
        if upload is None or not getattr(upload, "filename", ""):
            raise ValueError("Select a seed config file to upload (.json or .csv).")
        fname = Path(str(getattr(upload, "filename", "seed.json"))).name
        lower = fname.lower()
        if not (lower.endswith(".json") or lower.endswith(".csv")):
            raise ValueError("Seed config must be a .json or .csv file.")
        ctype = (getattr(upload, "content_type", "") or "").lower()
        allowed = {
            "application/json", "text/json", "text/plain", "application/octet-stream",
            "text/csv", "application/csv", "application/vnd.ms-excel",
        }
        if ctype and ctype not in allowed:
            raise ValueError("Invalid upload content type for seed config.")
        import tempfile
        suffix = ".csv" if lower.endswith(".csv") else ".json"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            data = await upload.read()
            if data is None:
                data = b""
            if len(data) > 5 * 1024 * 1024:
                raise ValueError("Seed config file is too large (max 5 MB).")
            tmp.write(data)
            tmp_path = tmp.name
        try:
            res = services.import_seed_config(tmp_path, deps.service_password(request, form), form.get("confirm", ""))
            msg_kind = "Inventory CSV seed" if lower.endswith('.csv') else "Seed config"
            deps.add_flash(request, f"{msg_kind} applied (first-time setup, pre-operational DB): {res.get('summary', {})}", "success")
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings?tab=data"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/seed/export-inventory-csv")
async def seed_export_inventory_csv(request: Request):
    deps.require_unlock(request)
    form = await deps.get_form(request)
    try:
        template = str(form.get("template") or "").strip().lower() in ("1", "true", "yes", "on")
        path = services.export_inventory_seed_csv(template=template)
        name = Path(path).name
        deps.add_flash(request, f"Inventory {'template ' if template else ''}CSV exported: {name}", "success")
        r = deps.redirect(f"/files/export/{name}")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        r = deps.redirect("/settings?tab=data")
    deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/rooms/add")
async def room_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        rid = services.add_room(form.get("room_number", ""), form.get("room_type", ""), form.get("base_price", 0), form.get("housekeeping_status", "Clean"))
        deps.add_flash(request, f"Room added (#{rid}).", "success")
        r = deps.redirect("/settings?tab=rooms"); deps.save_session(r, deps.get_session(request)); return r
    except Exception as e:
        deps.add_flash(request, str(e), "error")
        r = deps.redirect("/settings?tab=rooms"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/rooms/{room_id}/delete")
async def room_delete(request: Request, room_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.delete_room(room_id)
        deps.add_flash(request, "Room deleted.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings?tab=rooms"); deps.save_session(r, deps.get_session(request)); return r



@router.post("/settings/test-label")
async def test_label(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        ok, msg, p = services.test_label_print()
        deps.add_flash(request, f"Test label: {msg} ({p})", "success" if ok else "warning")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings?tab=general"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/updates/check")
async def updates_check(request: Request):
    form = await deps.get_form(request)
    try:
        result = updater.check_for_updates()
        deps.get_session(request)["last_update_check"] = result
        if result.get("error"):
            deps.add_flash(request, result["error"], "error")
            logger.warning("web_update_event event=check_error error=%s", str(result.get("error"))[:240])
        elif result.get("update_available"):
            n = result.get("commits_behind")
            deps.add_flash(request, f"Update available ({n} commit(s) behind). Review changelog, back up/verify runtime data, then apply during a maintenance window." if n is not None else "Update available. Review changelog and back up runtime data before applying.", "info")
            logger.info("web_update_event event=check_available commits_behind=%s", n)
        else:
            deps.add_flash(request, "You are up to date. Runtime data (DB/exports/invoices) remains preserved.", "success")
            logger.info("web_update_event event=check_up_to_date")
    except Exception as e:
        deps.get_session(request)["last_update_check"] = {"ok": False, "error": str(e)}
        deps.add_flash(request, f"Update check failed: {e}", "error")
        logger.warning("web_update_event event=check_exception error=%s", str(e)[:240])
    r = deps.redirect("/settings?tab=updates"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/updates/apply")
async def updates_apply(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        if str(form.get("confirm", "")).strip().upper() != "UPDATE APP":
            raise ValueError("Confirmation text must be exactly: UPDATE APP")
        result = updater.apply_update()
        deps.get_session(request)["last_update_apply"] = result
        if result.get("ok"):
            deps.add_flash(request, "Update applied (git fast-forward). Runtime data (DB/exports/invoices/PM_DATA_DIR) was preserved. Restart the app/service to use the new version and run doctor/health checks after restart.", "success")
            logger.info("web_update_event event=apply_success")
        else:
            deps.add_flash(request, result.get("error") or "Update failed. Runtime data was not deleted; review git status/logs before retrying.", "error")
            logger.warning("web_update_event event=apply_failure error=%s", str(result.get("error") or "unknown")[:240])
    except Exception as e:
        deps.get_session(request)["last_update_apply"] = {"ok": False, "error": str(e)}
        deps.add_flash(request, str(e), "error")
        logger.warning("web_update_event event=apply_exception error=%s", str(e)[:240])
    r = deps.redirect("/settings?tab=updates"); deps.save_session(r, deps.get_session(request)); return r





@router.get("/settings/vouchers")
def vouchers(request: Request):
    try:
        items = services.list_discount_vouchers(active_only=False)
    except Exception:
        items = []
    return render(request, "settings/vouchers.html", {"title": "Discount Vouchers", "items": items})


@router.post("/settings/vouchers/add")
async def voucher_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        mu = form.get("max_uses")
        services.create_discount_voucher(
            form.get("code", ""),
            discount_type=form.get("discount_type", "amount"),
            value=float(form.get("value") or 0),
            name=form.get("name", ""),
            description=form.get("description", ""),
            max_uses=(int(mu) if mu not in (None, "") else None),
            valid_from=form.get("valid_from", ""),
            valid_to=form.get("valid_to", ""),
            active=bool(form.get("active")),
        )
        deps.add_flash(request, "Voucher created.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/vouchers"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/vouchers/{voucher_id}/edit")
async def voucher_edit(request: Request, voucher_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        mu = form.get("max_uses")
        services.update_discount_voucher(
            voucher_id,
            code=form.get("code"),
            discount_type=form.get("discount_type"),
            value=float(form.get("value") or 0),
            name=form.get("name"),
            description=form.get("description"),
            max_uses=(mu if mu == "" else (int(mu) if mu not in (None, "") else "")),
            valid_from=form.get("valid_from"),
            valid_to=form.get("valid_to"),
            active=("active" in form),
        )
        deps.add_flash(request, "Voucher updated.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/vouchers"); deps.save_session(r, deps.get_session(request)); return r



@router.get("/settings/service-catalog")
def service_catalog(request: Request):
    try:
        items = services.list_service_catalog(active_only=False)
    except Exception:
        items = []
    return render(request, "settings/service_catalog.html", {"title": "Service Catalog", "items": items})


@router.post("/settings/service-catalog/add")
async def service_catalog_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.create_service_catalog_item(
            form.get("name", ""),
            category=form.get("category", "Service"),
            unit_price=float(form.get("unit_price") or 0),
            description=form.get("description", ""),
            taxable=bool(form.get("taxable")),
            active=bool(form.get("active")),
        )
        deps.add_flash(request, "Service catalog item created.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/service-catalog"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/service-catalog/{item_id}/edit")
async def service_catalog_edit(request: Request, item_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.update_service_catalog_item(
            item_id,
            name=form.get("name"),
            category=form.get("category"),
            unit_price=float(form.get("unit_price") or 0),
            description=form.get("description"),
            taxable=("taxable" in form),
            active=("active" in form),
        )
        deps.add_flash(request, "Service catalog item updated.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/service-catalog"); deps.save_session(r, deps.get_session(request)); return r



@router.get("/settings/packages")
def packages(request: Request):
    try:
        pkgs = services.list_packages(active_only=False)
    except Exception:
        pkgs = []
    try:
        catalog = services.list_service_catalog(active_only=True)
    except Exception:
        catalog = []
    return render(request, "settings/packages.html", {"title": "Packages & Bundles", "packages": pkgs, "catalog": catalog})


@router.post("/settings/packages/add")
async def package_add(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        services.create_package(form.get("name", ""), form.get("description", ""), active=bool(form.get("active", "1")))
        deps.add_flash(request, "Package created.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/packages"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/packages/{package_id}/edit")
async def package_edit(request: Request, package_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.update_package(package_id, name=form.get("name"), description=form.get("description"), active=bool(form.get("active")))
        deps.add_flash(request, "Package updated.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/packages"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/packages/{package_id}/items/add")
async def package_item_add(request: Request, package_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        sid = form.get("service_id") or None
        up = form.get("unit_price")
        tax_raw = form.get("taxable")
        taxable = None if tax_raw in (None, "", "inherit") else (str(tax_raw) in ("1","true","on","yes"))
        services.add_package_item(
            package_id,
            service_id=int(sid) if sid not in (None, "", "0") else None,
            description=form.get("description", ""),
            quantity=float(form.get("quantity") or 1),
            unit_price=(float(up) if up not in (None, "") else None),
            taxable=taxable,
            sort_order=int(form.get("sort_order") or 0),
        )
        deps.add_flash(request, "Package item added.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/packages"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/packages/items/{item_id}/delete")
async def package_item_delete(request: Request, item_id: int):
    await deps.require_unlocked_form(request)
    try:
        services.delete_package_item(item_id)
        deps.add_flash(request, "Package item removed.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/packages"); deps.save_session(r, deps.get_session(request)); return r




@router.get("/settings/approvals")
def settings_approvals(request: Request, status: str = ""):
    deps.require_unlock(request)
    try:
        approvals = services.list_approvals(status=status or "", limit=200)
    except Exception:
        approvals = []
    return render(request, "settings/approvals.html", {
        "title": "Approvals",
        "approvals": approvals,
        "status_filter": status or "",
    })


@router.post("/settings/approvals/request")
async def settings_approvals_request(request: Request):
    form = await deps.require_unlocked_form(request)
    try:
        payload_raw = (form.get("payload_json") or "").strip()
        payload = {}
        if payload_raw:
            try:
                payload = __import__("json").loads(payload_raw)
            except Exception:
                raise ValueError("payload_json must be valid JSON.")
        aid = services.create_approval_request(
            form.get("action_type", ""),
            entity_type=form.get("entity_type", ""),
            entity_id=form.get("entity_id", ""),
            payload=payload,
            reason=form.get("reason", ""),
            notes=form.get("notes", ""),
        )
        deps.add_flash(request, f"Approval request #{aid} created (pending).", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings/approvals"); deps.save_session(r, deps.get_session(request)); return r


@router.post("/settings/approvals/{approval_id}/decide")
async def settings_approvals_decide(request: Request, approval_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        services.decide_approval(
            approval_id,
            form.get("decision", ""),
            notes=form.get("notes", ""),
        )
        deps.add_flash(request, f"Approval #{approval_id} updated.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    status = form.get("status_filter", "")
    r = deps.redirect("/settings/approvals" + (f"?status={status}" if status else ""))
    deps.save_session(r, deps.get_session(request)); return r




@router.post("/settings/approvals/{approval_id}/execute")
async def settings_approvals_execute(request: Request, approval_id: int):
    form = await deps.require_unlocked_form(request)
    try:
        result_raw = (form.get("execution_result_json") or "").strip()
        result = {}
        if result_raw:
            try:
                result = __import__("json").loads(result_raw)
            except Exception:
                raise ValueError("execution_result_json must be valid JSON.")
        services.mark_approval_executed(approval_id, result=result, notes=form.get("notes", ""))
        deps.add_flash(request, f"Approval #{approval_id} marked as executed.", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    status = form.get("status_filter", "")
    r = deps.redirect("/settings/approvals" + (f"?status={status}" if status else ""))
    deps.save_session(r, deps.get_session(request)); return r

