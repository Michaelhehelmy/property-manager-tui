from __future__ import annotations
import logging
from pathlib import Path
from fastapi import APIRouter, Request
from app_core import services, updater
from ui.web import deps
from ui.web.rendering import render

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/unlock")
def unlock_page(request: Request, next: str = "/"):
    return render(request, "unlock.html", {"title":"Unlock", "next": next})

@router.post("/unlock")
async def unlock_post(request: Request):
    form = await deps.get_form(request)
    nxt = str(form.get("next") or "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    try:
        deps.enforce_auth_rate_limit(request, "unlock", limit=8, window_sec=300)
    except Exception as e:
        logger.warning("web_auth_route_event event=unlock_rate_limited")
        deps.add_flash(request, str(getattr(e, "detail", e)), "error")
        from urllib.parse import quote
        r = deps.redirect("/unlock?next=" + quote(nxt, safe="/"))
        deps.save_session(r, deps.get_session(request)); return r
    if deps.unlock(request, form.get("password","")):
        deps.add_flash(request, "Unlocked", "success")
        r=deps.redirect(nxt)
    else:
        deps.add_flash(request, "Invalid super password", "error")
        from urllib.parse import quote
        r=deps.redirect("/unlock?next=" + quote(nxt, safe="/"))
    deps.save_session(r, deps.get_session(request)); return r


@router.get("/unlock/recovery")
def unlock_recovery_page(request: Request, next: str = "/"):
    return render(request, "unlock_recovery.html", {"title": "Recover Access", "next": next})


@router.post("/unlock/recovery")
async def unlock_recovery_post(request: Request):
    form = await deps.get_form(request)
    nxt = str(form.get("next") or "/")
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/"
    try:
        deps.enforce_auth_rate_limit(request, "recovery", limit=5, window_sec=900)
        services.use_recovery_code(form.get("code", ""), form.get("new_password", ""), form.get("confirm_password", ""))
        deps.clear_auth_failures(request, "recovery")
        # Use new password to establish per-browser unlock (also best-effort backend compat).
        if not deps.unlock(request, form.get("new_password", "")):
            raise RuntimeError("Failed to establish unlocked session after recovery.")
        deps.add_flash(request, "Password reset with recovery code. Unlocked.", "success")
        r = deps.redirect(nxt)
    except Exception as e:
        msg = str(getattr(e, "detail", e))
        # Count likely credential/recovery failures as attempts (not validation-only 429).
        if "Too many attempts" not in msg:
            deps.record_auth_failure(request, "recovery", window_sec=900)
        deps.add_flash(request, msg, "error")
        from urllib.parse import quote
        r = deps.redirect("/unlock/recovery?next=" + quote(nxt, safe="/"))
    deps.save_session(r, deps.get_session(request)); return r

@router.post("/lock")
async def lock_post(request: Request):
    form = await deps.get_form(request)
    deps.lock(request); deps.add_flash(request, "Locked", "info")
    r=deps.redirect("/"); deps.save_session(r, deps.get_session(request)); return r

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
    return render(request, "settings/rate_plans.html", {"title": "Rate Plans", "plans": plans})


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


@router.get("/settings/health")
def settings_health(request: Request):
    deps.require_unlock(request)
    result = services.verify_ledger_integrity()
    return render(request, "settings/health.html", {"title": "Health Check", "result": result})



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
            raise ValueError("Select a seed config JSON file to upload.")
        fname = Path(str(getattr(upload, "filename", "seed.json"))).name
        if not fname.lower().endswith(".json"):
            raise ValueError("Seed config must be a .json file.")
        ctype = (getattr(upload, "content_type", "") or "").lower()
        if ctype and ctype not in ("application/json", "text/json", "text/plain", "application/octet-stream"):
            raise ValueError("Invalid upload content type for seed config.")
        import tempfile
        suffix = ".json"
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
            deps.add_flash(request, f"Seed config applied (first-time setup): {res.get('summary', {})}", "success")
        finally:
            try:
                Path(tmp_path).unlink()
            except Exception:
                pass
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/settings?tab=data"); deps.save_session(r, deps.get_session(request)); return r


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
