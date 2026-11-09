"""Settings catalog, validation, save, reset, import/export."""
import pytest
import services
from services import SETTINGS_CATALOG


def test_catalog_complete(clean_db):
    for key, spec in SETTINGS_CATALOG.items():
        assert len(spec) >= 5, f"{key} missing fields"
        assert spec[1] in ("str", "text", "int", "float", "pct", "bool", "enum", "internal")


def test_catalog_has_required_keys(clean_db):
    required = ["vat_rate", "service_charge_rate", "usd_to_egp", "schema_version",
                "cancel_policy", "noshow_policy", "language", "default_printer"]
    for key in required:
        assert key in SETTINGS_CATALOG


def test_get_setting(clean_db):
    assert services.get_setting("vat_rate") == "0.14"


def test_save_setting_valid(clean_db):
    count, errors = services.save_settings({"vat_rate": "0.15"}, "testpass")
    assert count == 1
    assert errors == []
    assert services.get_setting("vat_rate") == "0.15"


def test_save_invalid_pct(clean_db):
    count, errors = services.save_settings({"vat_rate": "1.5"}, "testpass")
    assert count == 0
    assert len(errors) == 1


def test_save_negative_float_rejected(clean_db):
    count, errors = services.save_settings({"city_tax_per_night": "-5"}, "testpass")
    assert count == 0
    assert len(errors) == 1


def test_save_invalid_enum(clean_db):
    count, errors = services.save_settings({"cancel_policy": "wrong_policy"}, "testpass")
    assert count == 0
    assert len(errors) == 1


def test_save_invalid_int(clean_db):
    count, errors = services.save_settings({"min_nights": "abc"}, "testpass")
    assert count == 0
    assert len(errors) == 1


def test_save_requires_password(clean_db):
    with pytest.raises(PermissionError):
        services.save_settings({"vat_rate": "0.15"}, "wrong")


def test_reset_to_defaults_keeps_language(clean_db):
    services.set_setting("language", "ar")
    services.set_setting("vat_rate", "0.20")
    services.reset_to_defaults("testpass")
    assert services.get_setting("language") == "ar"
    assert services.get_setting("vat_rate") == "0.14"


def test_reset_requires_password(clean_db):
    with pytest.raises(PermissionError):
        services.reset_to_defaults("wrong")


def test_export_import_roundtrip(clean_db, tmp_path):
    services.set_setting("vat_rate", "0.15")
    path = services.export_settings_to_json()
    assert path.endswith(".json")
    # Change it
    services.set_setting("vat_rate", "0.20")
    # Import back
    result = services.import_settings_from_json(path, "testpass")
    assert result["ok"] is True
    assert services.get_setting("vat_rate") == "0.15"


def test_import_invalid_json(clean_db, tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not valid json")
    with pytest.raises(Exception):
        services.import_settings_from_json(str(p), "testpass")


def test_import_missing_file(clean_db):
    with pytest.raises(ValueError):
        services.import_settings_from_json("/nonexistent.json", "testpass")