"""Helpers for Textual headless TUI tests.

Uses the root ``clean_db`` fixture for DB isolation. Async Textual tests run
via ``asyncio.run`` wrappers to stay compatible with the repo's pytest config
(no pytest-asyncio/anyio plugin required).
"""

from __future__ import annotations

import asyncio
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
for p in (str(SRC), str(ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

from app_core import services  # noqa: E402


def run_async(coro):
    """Run an async coroutine from a sync pytest test."""
    return asyncio.run(coro)


class HostApp(App):
    """Minimal host for mounting a single widget/screen fragment."""

    def __init__(self, widget_factory):
        super().__init__()
        self._widget_factory = widget_factory

    def compose(self) -> ComposeResult:
        yield self._widget_factory()


@contextmanager
def tui_settings(**overrides):
    """Temporarily set settings; restores originals."""
    originals = {}
    for key, value in overrides.items():
        originals[key] = services.get_setting(key)
        services.set_setting(key, value)
    try:
        yield
    finally:
        for key, value in originals.items():
            if value is None:
                services.set_setting(key, "")
            else:
                services.set_setting(key, value)


def seed_basic_data():
    """Seed minimal records for TUI flows."""
    today = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    day_after = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

    rooms = services.get_all_rooms()
    room_id = rooms[0]["id"] if rooms else 1
    guest_id, _, _ = services.find_or_create_guest("Alice Test", "0100", "alice@test")
    guest2, _, _ = services.find_or_create_guest("Bob Test", "0200", "bob@test")
    try:
        services.update_guest(guest_id, "Alice Test", "0100", "alice@test", "note", "testpass")
    except Exception:
        pass
    plans = services.get_all_rate_plans()
    plan_id = plans[0]["id"] if plans else None
    rid = services.create_reservation(
        "Alice Test", "Direct", room_id, plan_id, 100.0, today, tomorrow, 0.0, guest_id=guest_id
    )
    rid2 = services.create_reservation(
        "Bob Test", "Direct", rooms[1]["id"] if len(rooms) > 1 else room_id, plan_id,
        120.0, tomorrow, day_after, 0.0, guest_id=guest2,
    )
    inv_id = services.add_inventory_item("Cola", "Drinks", "can", 10, 2.0, 5.0, 2, 50, "testpass")
    services.add_inventory_item("Chips", "Snacks", "bag", 20, 1.0, 3.0, 2, 50, "testpass")
    return {
        "room_id": room_id,
        "rooms": rooms,
        "guest_id": guest_id,
        "guest2": guest2,
        "plan_id": plan_id,
        "rid": rid,
        "rid2": rid2,
        "inv_id": inv_id,
        "today": today,
        "tomorrow": tomorrow,
        "day_after": day_after,
    }


async def pause(pilot, n=3):
    for _ in range(n):
        try:
            await pilot.pause()
        except Exception:
            break


async def click(pilot, selector):
    try:
        w = pilot.app.query_one(selector)
        w.focus()
        await pilot.click(selector)
    except Exception:
        w = pilot.app.screen.query_one(selector)
        w.focus()
        await pilot.click(selector)
    await pause(pilot, 2)


async def set_input(pilot, selector, value, screen=None):
    root = screen or pilot.app.screen
    w = root.query_one(selector)
    w.value = value
    await pause(pilot, 1)


async def push_and_wait(pilot, screen, *a, **k):
    pilot.app.push_screen(screen, *a, **k)
    await pause(pilot, 4)
    return screen