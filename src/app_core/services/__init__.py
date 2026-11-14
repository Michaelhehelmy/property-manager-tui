"""Services package facade (decomposed from monolith, preserves public API)."""
from . import _monolith as _m
from ._monolith import *  # noqa: F401,F403

# Preserve/re-export module-level objects and internal helpers for backward compatibility
for _k, _v in vars(_m).items():
    if _k in {"__name__", "__package__", "__loader__", "__spec__", "__file__", "__cached__", "__builtins__"}:
        continue
    globals().setdefault(_k, _v)

# Ensure commonly imported names are present
from ._monolith import (  # noqa: F401
    SETTINGS_CATALOG, ANALYTICS_EXPORT_DIR, EXPORT_SCHEMA_VERSION, ANALYTICS_PRESETS,
    SEED_CONFIG_FORMAT, SEED_CONFIG_VERSION,
    _session, _lang,
)


# Common aliases
try:
    from . import _monolith as _m
    if 'net_types' not in globals() and hasattr(_m, 'net_types'):
        globals()['net_types'] = _m.net_types
    elif 'net_types' not in globals() and hasattr(_m, '_net_types'):
        globals()['net_types'] = _m._net_types
    if '_print_raw' not in globals() and hasattr(_m, '_print_raw'):
        globals()['_print_raw'] = _m._print_raw
    elif '_print_raw' not in globals() and hasattr(_m, '_print_raw_socket'):
        globals()['_print_raw'] = _m._print_raw_socket
except Exception:
    pass
