# Manual Test Checklist

Automated tests (`pytest`) cover the logic that doesn't need physical
hardware -- 82 tests as of this writing (mapping, note lifecycle, chords,
sustain, panic, knob math, song timing/scoring, MIDI import, config,
presets). The items below need a human at the real keyboard/audio device
and haven't been exercised in this dev environment (no physical F75 or
speaker output was available to *listen* to, only to open the stream and
measure callback timing).

Run `python -m qwerty_instrument`, enable Instrument Capture (on by
default), and go through:

- [ ] Single notes across the full mapped range (Z through P/0)
- [ ] Fast repeated notes on the same key (verify no OS key-repeat
      retriggering -- should sound like a single sustained note, not a
      motorboat effect)
- [ ] Long-held notes (10s+) -- verify no unexpected cutoff or drift
- [ ] Chords (3-5 keys at once)
- [ ] 8+ simultaneous keys -- verify voice stealing sounds reasonable, not
      broken
- [ ] Octave switching (PageUp/PageDown) mid-phrase
- [ ] Sustain (Space): hold-to-sustain behavior, and notes released while
      sustained keep ringing until Space is released
- [ ] Synth mode (Tab to cycle to synth_lead)
- [ ] Electric piano mode
- [ ] Guitar mode -- especially: does a struck note ring out naturally,
      and does it eventually go silent (not literally forever)?
- [ ] Knob clockwise (pitch bend rises, or whatever mode is configured)
- [ ] Knob counter-clockwise
- [ ] Knob short-click (toggles Lead Guitar mode -- banner should appear)
- [ ] Knob spring-return: rotate, stop, confirm it eases back toward
      center after the configured delay
- [ ] Panic (Escape) during a chord -- should go quiet fast, no leftover
      ringing notes
- [ ] CapsLock toggles capture off -- normal typing should work again in
      another application; toggling back on should resume instrument
      behavior
- [ ] Song loop: load a song, Start, let it reach the section end with
      Loop checked -- should restart cleanly, no audio glitch at the seam
- [ ] Speed 50% vs 100% -- pitch should be unaffected, only tempo
- [ ] Audio device change (Audio Settings -> pick a different device,
      Apply) -- should reopen cleanly with no crash, even mid-note
- [ ] 128-frame block size (Audio Settings)
- [ ] 64-frame block size
- [ ] Reference track (if a legally-obtained local audio file is placed
      per project instructions -- not implemented as a separate feature in
      V1; treat as a documented gap, not a passed check)

## F75-specific (see `docs/F75_SETUP.md` for the diagnostic procedure)

- [ ] `tools\f75_input_probe.py` run against the physical keyboard,
      output captured/reviewed for all 5 actions (normal key, CW, CCW,
      short press, long press)
- [ ] Confirm whether Windows system volume changes while capture is on
      (it shouldn't, for the VK_VOLUME_* path this app suppresses -- if it
      does, that's a real finding, see F75_SETUP.md's troubleshooting
      section)
- [ ] Confirm normal volume behavior returns when capture is toggled off
