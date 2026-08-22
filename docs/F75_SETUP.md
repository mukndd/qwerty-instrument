# AULA F75 Setup

## Which physical keyboard mode to use

The F75's knob typically has firmware-level modes (often toggled by a
**long press** on the knob itself, independent of any software). This
project deliberately:

- only uses **short press** for its own behavior (Lead Guitar mode toggle)
- never depends on long press
- never fights the keyboard's own firmware mode switching

If your F75 has an "office" vs "gaming"/custom mode for the knob, either
should work as long as it's sending *some* recognizable Windows input --
that's exactly what the probe tool below determines.

## Step 1: run the probe (do this first)

```
.venv\Scripts\python.exe tools\f75_input_probe.py
```

With the probe running, in order:

1. Press a normal letter key (sanity check -- confirms the low-level hook
   is capturing at all)
2. Rotate the knob **clockwise**
3. Rotate the knob **counter-clockwise**
4. **Short-press** the knob (quick tap)
5. **Long-press** the knob (~1 second)

Watch the console. Each action should produce one or more lines like:

```
[23:08:41.221] [LL_HOOK    ] WM_KEYDOWN       vkCode=0xAF VK_VOLUME_UP           scanCode=... flags=0x0 os_time=...
[23:08:41.222] [RAW_INPUT  ] KEYBOARD device=0x... VKey=0xAF VK_VOLUME_UP        MakeCode=... Flags=0x0 Message=0x100
```

or, if your firmware uses the multimedia-command mechanism instead:

```
[23:08:41.221] [APPCOMMAND ] WM_APPCOMMAND APPCOMMAND_VOLUME_UP (raw lParam=0x...)
```

or, if it's a dedicated Consumer Control HID device:

```
[23:08:41.221] [RAW_INPUT  ] HID (consumer/other) device=0x... reportSize=... bytes=[...]
```

Note which channel(s) fire for rotation vs. press, and whether rotation
shows up as `VK_VOLUME_UP`/`VK_VOLUME_DOWN` (the default this project
assumes) or something else.

## Step 2: what the app assumes by default

`input/f75.py` and `input/windows_hook.py` assume the knob's default
office/multimedia mode surfaces as:

- rotate clockwise -> `VK_VOLUME_UP`
- rotate counter-clockwise -> `VK_VOLUME_DOWN`
- press -> `VK_VOLUME_MUTE`

This is a documented assumption, not a verified fact about your specific
F75 revision/firmware -- confirm it with the probe above. If your probe
output matches this pattern, the app should work with no changes. If it
doesn't (e.g. you only see `WM_APPCOMMAND` events, or a Consumer Control
HID report with no matching `VK_VOLUME_*`), the knob mode/rotation logic
in `KnobConfig`/`F75Knob` (`input/f75.py`) is the place to extend --
`handle_key()` is the single entry point that currently only recognizes
those three VK names; adding an `WM_APPCOMMAND` listener that calls the
same `F75Knob._rotate()`/short-press logic is a small, contained change.

## Step 3: how knob click works

- **Short press** (release within ~0.5s, configurable via
  `knob.short_press_max_s`): toggles Normal/Synth mode <-> Lead Guitar
  mode. The UI shows an unmissable "LEAD GUITAR" banner when active.
- **Long press**: intentionally not bound to anything by this app --
  left for your keyboard's own firmware mode switch, so the two don't
  fight each other.

## Step 4: how knob rotation works

Default mode is **pitch bend** (`knob.mode = "pitch_bend"` in
`config.toml`): each detent moves the bend by `bend_step_cents` (default
25 cents), clamped to `+/- max_bend_cents` (default 200 cents = 2
semitones). After `return_delay_s` (default 160ms) of no rotation, the
bend smoothly springs back to 0 over `return_duration_s` (default 220ms) --
because the encoder itself has no physical center detent. All of this is
configurable; see `config.example.toml`'s `[knob]` section. Other modes
(`expression`, `filter_cutoff`, `vibrato_depth`, `drive`,
`delay_feedback`, `modulation`) are continuous (no spring return) and map
to musically sensible ranges defined in `input/f75.py: PARAM_RANGES`.

## What to do if Windows volume changes

While **Instrument Capture** is on (the default -- toggle with CapsLock),
the app's low-level keyboard hook suppresses `VK_VOLUME_UP`/`DOWN`/`MUTE`
system-wide, which also prevents the OS's own volume change/OSD from
firing. If you ever see Windows volume actually changing while playing,
that means those events are reaching Windows through a path this hook
doesn't intercept (most likely: a `WM_APPCOMMAND`-based firmware mode,
which is a separate delivery mechanism the current suppression logic
doesn't yet cover -- see Step 2). **Turning Instrument Capture off**
(CapsLock) always and immediately restores normal system volume behavior,
regardless of which channel the knob uses, since the app stops intercepting
anything at all.

## Connection recommendations

Test wired, 2.4GHz, and Bluetooth if your F75 supports more than one, and
use whichever is most stable/lowest-latency for you -- this app can't
remove wireless hardware latency, only avoid adding to it in software.
Wired is the safe default if you're unsure.
