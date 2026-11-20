from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request
from app_core import services
from app_core.db import connect
from ui.web import deps
from ui.web.rendering import render
from ui.web.route_utils import flash_redirect

router = APIRouter()


def _rooms():
    return (
        connect()
        .execute("SELECT id, room_number, room_type, housekeeping_status FROM rooms ORDER BY room_number")
        .fetchall()
    )


@router.get("/housekeeping")
def housekeeping_page(request: Request, status: str = "", block_status: str = "active"):
    try:
        tasks = services.list_housekeeping_tasks(status=status or "")
    except Exception:
        tasks = []
    # best-effort overdue flag for open tasks
    now = datetime.now()
    enriched = []
    for t in tasks or []:
        d = dict(t)
        d["is_overdue"] = False
        try:
            if (d.get("status") in ("pending", "in_progress")) and d.get("due_at"):
                due_s = str(d.get("due_at")).strip()
                due_dt = None
                for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                    try:
                        due_dt = datetime.strptime(due_s[:19], fmt)
                        break
                    except Exception:
                        pass
                if due_dt and due_dt < now:
                    d["is_overdue"] = True
        except Exception:
            pass
        enriched.append(d)
    tasks = enriched
    block_status = (block_status or "active").strip().lower()
    if block_status not in ("active", "resolved", "all", ""):
        block_status = "active"
    try:
        blocks = services.list_room_blocks(status=block_status)
    except Exception:
        blocks = []
    return render(
        request,
        "housekeeping/index.html",
        {
            "title": "Housekeeping",
            "tasks": tasks,
            "rooms": _rooms(),
            "blocks": blocks,
            "status_filter": status or "",
            "block_status_filter": block_status or "active",
        },
    )


@router.post("/housekeeping/tasks/create")
async def create_task(request: Request):
    form = await deps.get_form(request)
    try:
        services.create_housekeeping_task(
            int(form.get("room_id")),
            task_type=form.get("task_type", "cleaning"),
            priority=form.get("priority", "normal"),
            assignee=form.get("assignee", ""),
            due_at=form.get("due_at", ""),
            notes=form.get("notes", ""),
            reservation_id=int(form["reservation_id"]) if form.get("reservation_id") else None,
        )
        deps.add_flash(request, "Housekeeping task created", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/housekeeping")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/housekeeping/tasks/{task_id}/status")
async def update_task_status(task_id: int, request: Request):
    form = await deps.get_form(request)
    try:
        services.update_housekeeping_task_status(
            task_id, form.get("status", ""), notes_append=form.get("notes_append", "")
        )
        deps.add_flash(request, "Task updated", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/housekeeping")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/housekeeping/tasks/{task_id}/edit")
async def edit_task(task_id: int, request: Request):
    form = await deps.get_form(request)
    try:
        rid = form.get("reservation_id")
        services.update_housekeeping_task(
            task_id,
            task_type=form.get("task_type"),
            priority=form.get("priority"),
            assignee=form.get("assignee"),
            due_at=form.get("due_at"),
            notes=form.get("notes"),
            reservation_id=rid if rid is not None and rid != "" else ("" if rid == "" else None),
        )
        deps.add_flash(request, "Task details updated", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    status = form.get("status_filter", "")
    block_status = form.get("block_status_filter", "active")
    url = "/housekeeping"
    params = []
    if status:
        params.append(f"status={status}")
    if block_status:
        params.append(f"block_status={block_status}")
    if params:
        url += "?" + "&".join(params)
    r = deps.redirect(url)
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/housekeeping/tasks/generate")
async def generate_tasks(request: Request):
    form = await deps.get_form(request)
    try:
        n = services.generate_housekeeping_tasks_for_dirty_rooms(due_at=form.get("due_at", ""))
        deps.add_flash(request, f"Generated {n} task(s) for dirty rooms", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/housekeeping")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/housekeeping/blocks/create")
async def create_block(request: Request):
    form = await deps.require_unlocked_form(request, permission="housekeeping.manage")
    try:
        services.create_room_block(
            int(form.get("room_id")),
            form.get("block_type", "OOO"),
            form.get("reason", ""),
            form.get("start_date", ""),
            form.get("end_date", ""),
            notes=form.get("notes", ""),
        )
        deps.add_flash(request, "Room block created", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/housekeeping")
    deps.save_session(r, deps.get_session(request))
    return r


@router.post("/housekeeping/blocks/{block_id}/resolve")
async def resolve_block(block_id: int, request: Request):
    form = await deps.require_unlocked_form(request, permission="housekeeping.manage")
    try:
        services.resolve_room_block(block_id)
        deps.add_flash(request, "Room block resolved", "success")
    except Exception as e:
        deps.add_flash(request, str(e), "error")
    r = deps.redirect("/housekeeping")
    deps.save_session(r, deps.get_session(request))
    return r
