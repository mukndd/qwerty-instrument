from qwerty_instrument.config import DEFAULT_CONFIG, load_config, save_config


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_config(tmp_path / "does_not_exist.toml")
    assert cfg["audio"]["sample_rate"] == 48000
    assert cfg == DEFAULT_CONFIG


def test_roundtrip_save_and_load(tmp_path):
    path = tmp_path / "config.toml"
    cfg = load_config(path)
    cfg["audio"]["block_size"] = 128
    cfg["keyboard"]["base_note"] = 45
    save_config(cfg, path)
    reloaded = load_config(path)
    assert reloaded["audio"]["block_size"] == 128
    assert reloaded["keyboard"]["base_note"] == 45


def test_malformed_toml_falls_back_to_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("this is not [ valid toml", encoding="utf-8")
    cfg = load_config(path)
    assert cfg == DEFAULT_CONFIG


def test_unknown_key_is_ignored_not_fatal(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[audio]\nsample_rate = 44100\ntotally_made_up_field = "x"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg["audio"]["sample_rate"] == 44100
    assert "totally_made_up_field" not in cfg["audio"]


def test_wrong_type_field_keeps_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[audio]\nsample_rate = "not a number"\n', encoding="utf-8")
    cfg = load_config(path)
    assert cfg["audio"]["sample_rate"] == DEFAULT_CONFIG["audio"]["sample_rate"]
