from pathlib import Path

from qwerty_instrument.audio.presets import apply_preset, list_presets, load_preset
from qwerty_instrument.audio.synth import SynthLead

PRESETS_DIR = Path(__file__).resolve().parent.parent / "presets"


def test_all_shipped_presets_load_without_error():
    presets = list_presets(PRESETS_DIR)
    assert len(presets) >= 5  # warm_analog, electric_keys, lead_guitar, instant_crush_synth, instant_crush_lead
    names = {p.name for p in presets}
    assert "Instant Crush Synth" in names
    assert "Instant Crush Lead" in names


def test_apply_preset_sets_params_on_matching_backend():
    preset = load_preset(PRESETS_DIR / "instant_crush_synth.json")
    assert preset is not None
    backend = SynthLead(48000, 256)
    ok = apply_preset(backend, preset)
    assert ok
    assert backend.patch["cutoff"] == preset.params["cutoff"]


def test_apply_preset_rejects_wrong_instrument():
    preset = load_preset(PRESETS_DIR / "lead_guitar.json")  # targets guitar_lead
    backend = SynthLead(48000, 256)  # synth_lead
    ok = apply_preset(backend, preset)
    assert ok is False
