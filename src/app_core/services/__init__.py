"""Services package facade (decomposed from monolith, preserves public API)."""
from ._monolith import *  # noqa: F401,F403

# Preserve module-level objects commonly imported/mutated
from ._monolith import (
    SETTINGS_CATALOG, ANALYTICS_EXPORT_DIR, EXPORT_SCHEMA_VERSION, ANALYTICS_PRESETS,
    SEED_CONFIG_FORMAT, SEED_CONFIG_VERSION,
    _session, _lang,
)
