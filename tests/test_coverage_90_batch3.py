from __future__ import annotations

import importlib
import json
import time
from types import SimpleNamespace

import pytest


def test_services_init_covers_fallback_alias_line(monkeypatch):
    import app_core.services as svc
    import app_core.services._monolith as m
    # remove primary attr, keep fallback attr, ensure not brought into globals by star import path
    svc.__dict__.pop('net_types', None)
    if hasattr(m, 'net_types'):
        monkeypatch.delattr(m, 'net_types', raising=False)
    monkeypatch.setattr(m, '_net_types', lambda: 'x', raising=False)
    svc2 = importlib.reload(svc)
    assert callable(getattr(svc2, 'net_types', None)) or hasattr(svc2, 'net_types')
    importlib.reload(svc)
