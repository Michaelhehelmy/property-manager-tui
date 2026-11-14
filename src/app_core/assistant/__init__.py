"""Manager assistant package (compatibility facade preserving public API)."""
from __future__ import annotations

import datetime as _datetime_mod
from datetime import datetime as _dt_class, timedelta  # exported names used historically

# Expose module-like datetime for tests that monkeypatch `assistant.datetime`
datetime = _datetime_mod

from . import common, feedback, formatting, nlu, tools, engine, runtime

# star imports (won't include underscore names, but include public API)
from .common import *  # noqa: F401,F403
from .feedback import *  # noqa: F401,F403
from .formatting import *  # noqa: F401,F403
from .nlu import *  # noqa: F401,F403
from .tools import *  # noqa: F401,F403
from .engine import *  # noqa: F401,F403
from .runtime import *  # noqa: F401,F403

# Explicit exports for underscore/private APIs relied upon by callers/tests
from .common import (
    _fmt_money, _format_data, _iso, _norm_text, _parse_date_range, _parse_iso,
    _prev_period, _resolve_guest_from_text, _safe_int, _short, _sim,
    _token_set, _val, _yn,
)
from .formatting import (
    _apply_proactive, _format_for_intent, _friendly_open, _local_talking_rephrase, _proactive_suggestions,
)
from .nlu import _INTENT_CUES, classify_intent, rule_classify
from .feedback import (
    _EXEMPLAR_CACHE, _SETFIT_CACHE, _SKLEARN_CACHE,
    exemplar_match, get_correction_exemplars, list_feedback, record_feedback,
)
from .engine import _answer, _execute_compound, _execute_single, _guest_disambiguation
from .runtime import (
    _LOCAL_LLM_CACHE, _get_local_llm, _llm_config, _local_llm_generate,
    chat, deterministic_route, get_llm_config, llm_route, talking_rephrase,
)
from .tools import TOOLS, execute_tool

# Patchable helpers: ensure they observe assistant.datetime monkeypatches
def _today():
    dt = datetime
    # support both stdlib datetime module and fake modules providing now()
    if hasattr(dt, "now"):
        now = dt.now()
    elif hasattr(dt, "datetime") and hasattr(dt.datetime, "now"):
        now = dt.datetime.now()
    else:
        now = _datetime_mod.datetime.now()
    if hasattr(now, "date") and not isinstance(now, _dt_class):
        # FakeDatetime returns datetime but date() may behave; handle both
        return now.date() if callable(getattr(now, "date", None)) else now
    # if it's a datetime instance
    try:
        return now.date()
    except Exception:
        return now

# propagate to modules that use these
common._today = _today
common.datetime = datetime
# ensure re-exported names point to canonical functions
_parse_date_range = common._parse_date_range
_format_data = common._format_data
_resolve_guest_from_text = common._resolve_guest_from_text

# legacy/compat names some code may expect
_today_mod = datetime
