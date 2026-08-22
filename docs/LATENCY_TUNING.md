# Latency Tuning

## Targets

```
< 10 ms       excellent
10-15 ms      very good
15-25 ms      acceptable
> 30 ms       undesirable
```

These are the numbers `sounddevice`/PortAudio *reports* for the stream
(`AudioEngine.stats.reported_latency_s`) -- the software-path estimate for
your chosen device/buffer, not an acoustically-measured (mic loopback)
round trip. Treat it as an informed estimate, as the app itself does
everywhere it surfaces this number (Audio Settings, diagnostics, the
status bar).

## What was actually measured, on this machine

USB Audio Device output, via `tools/audio_diagnostics.py` and
`tests/_hardware_smoke.py` (real hardware, not simulated):

| Config | Reported latency | Underruns | Max callback vs. budget |
|---|---|---|---|
| 256 frames, 48kHz, WASAPI shared | 22.0 ms | 0 | comfortably under (see below) |
| 128 frames, 48kHz, WASAPI shared | 22.0 ms | 0 | under |
| 64 frames, 48kHz, WASAPI shared | 22.0 ms | 0 | occasionally at/near budget, still 0 underruns |
| 256 frames, 48kHz, **WASAPI exclusive** | **8.0 ms** | 0 | comfortably under |

Two real bugs were found and fixed while measuring this (not just
theorized -- see `docs/ARCHITECTURE.md` and git history):

1. **Leaving `device_index` unset resolved to sounddevice's cross-hostapi
   default, not WASAPI** -- on this machine that was an MME device with a
   ~186ms default buffer. `AudioEngine.start()` now explicitly resolves to
   the WASAPI default output device when no device is configured, and
   always passes `latency="low"` to `sd.OutputStream`.
2. **The very first audio callback took ~20ms** (against a 5.3ms/256-frame
   budget) purely from one-time costs (numpy ufunc dispatch, scipy
   import/JIT-ish caches) -- confirmed by instrumenting every callback's
   duration and finding the spike was exactly callback index 0, never
   recurring. `AudioEngine._warm_up()` now forces a silent note through
   every registered instrument's full DSP path *before* the stream opens,
   so that cost is paid on the calling thread instead of the real-time one.

## How to test on your machine

```
.venv\Scripts\python.exe tools\audio_diagnostics.py                    # devices + latency report only
.venv\Scripts\python.exe tools\audio_diagnostics.py --tone              # + audible test tone
.venv\Scripts\python.exe tools\audio_diagnostics.py --stress            # + rapid-note polyphony stress test
.venv\Scripts\python.exe tools\audio_diagnostics.py --block 128         # try a smaller block
.venv\Scripts\python.exe tools\audio_diagnostics.py --block 64
.venv\Scripts\python.exe tools\audio_diagnostics.py --exclusive         # try WASAPI exclusive mode
```

Look at the final stats block: `underruns` is the number that actually
matters for audible quality (max callback time briefly exceeding the
nominal per-block budget is fine as long as underruns stay at 0 -- WASAPI's
own internal buffering absorbs small overages; it's what "acceptable"
headroom looks like in practice on this hardware).

## What to do if you see underruns

1. Try a larger block size (256 -> 512) before anything else.
2. Try WASAPI shared mode if you were on exclusive (some drivers handle
   shared mode more robustly).
3. Close other audio applications -- WASAPI shared mode is still shared.
4. Check `tools\audio_diagnostics.py --stress`'s `max callback duration` --
   if it's *far* above budget (not just marginally), something in the
   active instrument's patch is too expensive; guitar polyphony is the
   most likely culprit (see `docs/ARCHITECTURE.md`'s note on the per-sample
   Karplus-Strong loop) -- try reducing
   `config.toml`'s `[instruments.polyphony] guitar_lead`.
5. If wireless (2.4GHz/Bluetooth F75), try wired -- see
   `docs/F75_SETUP.md`'s connection recommendations. No software change can
   remove wireless hardware latency.

## WASAPI shared vs. exclusive

Exclusive mode gave a real, measured 8ms vs 22ms on this machine's USB
audio interface -- but it isn't guaranteed to work on every device/driver
(some devices reject it, some drivers are unstable in exclusive mode).
`AudioEngine.start()` tries exclusive mode when requested and **falls back
to shared mode automatically** if opening the stream fails, logging a
warning rather than refusing to produce any sound at all. Toggle it from
Audio Settings.

## Block size tradeoffs

Smaller blocks = lower latency but more per-second callback overhead and
less headroom against occasional slow callbacks (GC pauses, OS scheduling
jitter). 256 is the safe default; 128 was equally stable on this machine's
hardware; 64 worked with zero underruns but had less headroom (occasional
callback times right at the nominal budget). Benchmark on your own
machine -- these numbers are specific to this USB audio interface and will
differ on other hardware, especially built-in laptop audio or Bluetooth
output.
