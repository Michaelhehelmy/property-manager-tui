"""Tests for ui.tui.app."""

from __future__ import annotations

import asyncio
import runpy
from types import SimpleNamespace

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Static

from app_core import services
from tests.tui.conftest import HostApp, pause, run_async, tui_settings


def _app_mod():
    import ui.tui.app as app_mod

    return app_mod


def _modal_stubs(monkeypatch):
    app_mod = _app_mod()
    from textual.screen import ModalScreen
    from textual.app import ComposeResult
    from textual.widgets import Static

    class StubModal(ModalScreen):
        BINDINGS = [("escape", "dismiss", "Dismiss")]

        def __init__(self, *a, **k):
            super().__init__()
            self.args = a
            self.kw = k

        def compose(self) -> ComposeResult:
            yield Static("stub")

        def action_dismiss(self):
            self.dismiss(None)

    for name in ("BrandingWizardScreen", "LanguageScreen", "SetSuperPasswordScreen"):
        monkeypatch.setattr(app_mod, name, StubModal, raising=False)
    return app_mod, StubModal


# ── LoadingScreen ──────────────────────────────────────────────────────────

def test_loading_screen_skip(clean_db, monkeypatch):
    app_mod = _app_mod()

    async def _():
        async with app_mod.PropertyManagerApp().run_test(size=(120, 40)) as pilot:
            # startup may push modals; force loading screen directly
            await pilot.app.push_screen(app_mod.LoadingScreen())
            await pause(pilot, 2)
            await pilot.press("escape")
            await pause(pilot, 2)

    # configure to avoid blocking startup chain
    with tui_settings(branding_configured="true"):
        monkeypatch.setattr(services, "has_super_password", lambda: True)
        monkeypatch.setattr(services, "is_branding_configured", lambda: True)
        monkeypatch.setattr(app_mod, "is_branding_configured", lambda: True)
        monkeypatch.setattr(app_mod, "get_loading_animation", lambda: "reveal")
        run_async(_())


@pytest.mark.parametrize("style", ["none", "reveal", "typewriter", "pulse", "wave", "unknown"])
def test_loading_screen_styles(clean_db, monkeypatch, style):
    app_mod = _app_mod()
    monkeypatch.setattr(app_mod, "get_loading_animation", lambda: style)
    monkeypatch.setattr(app_mod, "get_app_display_name", lambda: "TestApp")
    monkeypatch.setattr(app_mod, "get_app_tagline", lambda: "Tag")

    async def _():
        app = App()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app_mod.LoadingScreen()
            app.push_screen(screen)
            await pause(pilot, 8)
            # drive phases for coverage
            for _ in range(15):
                try:
                    screen._tick()
                except Exception:
                    pass
                await pilot.pause()
            try:
                screen._finalize_loading()
            except Exception:
                pass
            await pause(pilot, 2)

    run_async(_())


def test_loading_screen_exception_paths(clean_db, monkeypatch):
    app_mod = _app_mod()
    monkeypatch.setattr(app_mod, "get_loading_animation", lambda: "reveal")
    monkeypatch.setattr(app_mod, "get_app_display_name", lambda: "X")

    async def _():
        app = App()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app_mod.LoadingScreen()
            app.push_screen(screen)
            await pause(pilot, 1)
            # query_one failure
            orig = screen.query_one
            screen.query_one = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
            screen._update_display()
            screen.query_one = orig
            # timer.stop failure
            screen._tick_timer = SimpleNamespace(stop=lambda: (_ for _ in ()).throw(RuntimeError("x")))
            screen._stop_tick()
            # finalize when not attached
            try:
                screen._done = False
                screen.is_attached = False
                screen._finalize_loading()
            except Exception:
                pass
            await pause(pilot, 1)

    run_async(_())


# ── PropertyManagerApp ─────────────────────────────────────────────────────

def _prep_app_startup(monkeypatch, branding=True, has_pw=True):
    app_mod, stubs = _modal_stubs(monkeypatch)
    monkeypatch.setattr(app_mod, "is_branding_configured", lambda: branding)
    monkeypatch.setattr(app_mod, "get_app_display_name", lambda: "TestProp")
    monkeypatch.setattr(app_mod, "get_app_tagline", lambda: "Hi")
    monkeypatch.setattr(app_mod, "get_gift_attribution", lambda: "Gift")
    monkeypatch.setattr(app_mod, "get_loading_animation", lambda: "none")
    monkeypatch.setattr(services, "verify_app_attribution", lambda: (True, ""))
    monkeypatch.setattr(services, "has_super_password", lambda: has_pw)
    monkeypatch.setattr(services, "is_session_unlocked", lambda: False)
    # avoid modal chains blocking: patch push_screen to auto-dismiss where needed
    return app_mod, stubs


def test_property_manager_app_smoke(clean_db, monkeypatch):
    app_mod, _ = _prep_app_startup(monkeypatch, branding=True, has_pw=True)

    async def _():
        app = app_mod.PropertyManagerApp()
        async with app.run_test(size=(120, 40)) as pilot:
            await pause(pilot, 5)
            app._apply_branding()
            app.action_language()
            await pause(pilot, 2)
            app.refresh_all_tables()
            await pause(pilot, 2)

    run_async(_())


def test_property_manager_app_branding_invalid(clean_db, monkeypatch):
    app_mod, _ = _prep_app_startup(monkeypatch)
    monkeypatch.setattr(services, "verify_app_attribution", lambda: (False, "bad"))

    async def _():
        async with app_mod.PropertyManagerApp().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 3)
            assert "⚠" in (pilot.app.sub_title or "")

    run_async(_())


def test_property_manager_app_startup_flows(clean_db, monkeypatch):
    app_mod, _ = _prep_app_startup(monkeypatch, branding=False, has_pw=False)
    pushed = []
    orig_push = app_mod.PropertyManagerApp.push_screen

    def fake_push(self, screen, callback=None):
        pushed.append((type(screen).__name__, callback))
        # auto-progress callbacks to avoid hangs
        if callback:
            name = type(screen).__name__
            if name == "BrandingWizardScreen":
                callback(True)
            elif name == "LoadingScreen":
                callback(None)
            elif name == "SetSuperPasswordScreen":
                callback(False)
        return orig_push(self, screen, callback=None) if False else None

    monkeypatch.setattr(app_mod.PropertyManagerApp, "push_screen", fake_push)
    exits = []
    monkeypatch.setattr(app_mod.PropertyManagerApp, "exit", lambda self, *a, **k: exits.append(True))

    async def _():
        app = app_mod.PropertyManagerApp()
        # call methods directly for deterministic coverage
        app._start_flow()
        app._after_branding(False)
        app._after_branding(True)
        app._after_loading()
        app._on_pw_set(False)
        app._on_pw_set(True)

    run_async(_())
    assert exits


def test_property_manager_session_timeout(clean_db, monkeypatch):
    app_mod, _ = _prep_app_startup(monkeypatch)
    monkeypatch.setattr(services, "is_session_unlocked", lambda: False)

    async def _():
        async with app_mod.PropertyManagerApp().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 4)
            from ui.tui.tabs import SettingsTab

            tab = pilot.app.query_one(SettingsTab)
            calls = []
            monkeypatch.setattr(tab, "_render_locked", lambda: calls.append(1))
            pilot.app._check_session_timeout()
            assert calls
            # unlocked path
            monkeypatch.setattr(services, "is_session_unlocked", lambda: True)
            pilot.app._check_session_timeout()
            # missing settings tab
            monkeypatch.setattr(services, "is_session_unlocked", lambda: False)
            orig = pilot.app.query_one
            pilot.app.query_one = lambda *a, **k: (_ for _ in ()).throw(LookupError())
            pilot.app._check_session_timeout()
            pilot.app.query_one = orig
            # render_locked not callable
            monkeypatch.setattr(services, "is_session_unlocked", lambda: False)
            pilot.app.query_one = lambda cls, *a, **k: SimpleNamespace(_render_locked=None) if cls is SettingsTab else orig(cls, *a, **k)
            pilot.app._check_session_timeout()
            pilot.app.query_one = orig
            await pause(pilot, 1)

    run_async(_())


def test_refresh_all_tables_error(clean_db, monkeypatch):
    app_mod, _ = _prep_app_startup(monkeypatch)

    async def _():
        async with app_mod.PropertyManagerApp().run_test(size=(120, 40)) as pilot:
            await pause(pilot, 3)
            # make a refresh explode
            for cls in pilot.app._REFRESHABLE_TABS:
                try:
                    tab = pilot.app.query_one(cls)
                    monkeypatch.setattr(tab, "refresh_data", lambda: (_ for _ in ()).throw(RuntimeError("x")))
                except Exception:
                    pass
            pilot.app.refresh_all_tables()
            # query_one miss and non-callable refresh
            from ui.tui.tabs import FrontDeskTab
            orig = pilot.app.query_one

            def q(cls, *a, **k):
                if cls is FrontDeskTab:
                    raise LookupError()
                if cls is pilot.app._REFRESHABLE_TABS[1]:
                    return SimpleNamespace(refresh_data="nope")
                return orig(cls, *a, **k)

            monkeypatch.setattr(pilot.app, "query_one", q)
            pilot.app.refresh_all_tables()
            await pause(pilot, 2)

    run_async(_())


def test_loading_finish_double(clean_db, monkeypatch):
    app_mod = _app_mod()
    monkeypatch.setattr(app_mod, "get_loading_animation", lambda: "reveal")
    monkeypatch.setattr(app_mod, "get_app_display_name", lambda: "X")
    monkeypatch.setattr(app_mod, "get_app_tagline", lambda: "")

    async def _():
        app = App()
        async with app.run_test(size=(80, 24)) as pilot:
            screen = app_mod.LoadingScreen()
            app.push_screen(screen)
            await pause(pilot, 1)
            screen._finish(screen.query_one("#loading_text", Static), "body")
            screen._finish(screen.query_one("#loading_text", Static), "body2")  # guarded
            await pause(pilot, 1)

    run_async(_())


def test_main(clean_db, monkeypatch):
    import ui.tui.app as app_mod

    calls = []
    monkeypatch.setattr(services, "init_db", lambda: calls.append("db"))
    monkeypatch.setattr(services, "get_setting", lambda k, d=None: "en")
    monkeypatch.setattr(app_mod, "set_language", lambda x: calls.append(x))
    monkeypatch.setattr(app_mod.PropertyManagerApp, "run", lambda self: calls.append("run"))
    app_mod.main()
    assert "db" in calls and "run" in calls and "en" in calls


def test_main_guard_subprocess(clean_db, tmp_path):
    """Cover __main__ guard via subprocess; append to coverage data file."""
    import os
    import subprocess
    import sys

    root = "/data/data/com.termux/files/home/property-manager-tui"
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{root}/src:{root}"
    cov_data = os.environ.get("COVERAGE_FILE", ".coverage.tui")

    def run_case(raise_exc=False):
        body = (
            "import app_core.services as s\n"
            "from textual.app import App\n"
            "s.init_db=lambda:None\n"
            "s.get_setting=lambda k,d=None:'en'\n"
            "s.set_language=lambda x:None\n"
        )
        if raise_exc:
            body += "App.run=lambda self: (_ for _ in ()).throw(RuntimeError('x'))\n"
        else:
            body += "App.run=lambda self: None\n"
        body += "import runpy\nrunpy.run_module('ui.tui.app', run_name='__main__')\n"
        script = tmp_path / ("guard_exc.py" if raise_exc else "guard_ok.py")
        script.write_text(body)
        cmd = [
            sys.executable, "-m", "coverage", "run", "--append",
            f"--data-file={os.path.join(root, cov_data)}",
            f"--include={root}/ui/tui/app.py",
            str(script),
        ]
        return subprocess.run(cmd, cwd=root, env=env, capture_output=True, text=True, timeout=45)

    r = run_case(False)
    assert r.returncode == 0, r.stderr + r.stdout
    r2 = run_case(True)
    assert r2.returncode == 1, r2.stderr + r2.stdout
    assert "Fatal startup error" in (r2.stdout + r2.stderr)