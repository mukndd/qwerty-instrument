"""Low-level Windows keyboard capture via WH_KEYBOARD_LL (ctypes, no pywin32 DLL needed).

Why this over Qt key events: Qt's key events only fire while a Qt widget
has focus, don't reliably distinguish real OS key-repeat from a fresh
press across all platforms, and go through the Qt event loop before
reaching application code. A systemwide WH_KEYBOARD_LL hook fires
synchronously as Windows dispatches the keystroke, works regardless of
which window has focus, and lets us *suppress* propagation of specific
keys (needed so CapsLock doesn't actually toggle, and so note keys don't
leak into whatever else is focused while Instrument Capture is on).

Design notes:
    - The hook procedure itself only does dict lookups and a queue put --
      no logging, no allocation of anything large, no blocking. Windows can
      silently uninstall a slow low-level hook, so keeping this fast is a
      correctness requirement, not just a latency nicety.
    - Repeat suppression happens here too: Windows re-sends WM_KEYDOWN
      continuously while a key is held. We track a `held` set of currently
      down virtual-key codes and only forward the *first* down transition
      and the matching up transition, so a musical note is never retriggered
      by OS key-repeat.
    - `nCode != 0` (not HC_ACTION) must be passed to CallNextHookEx
      immediately without inspecting the event, per Win32 hook contract.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import logging
import threading
import time
from typing import Callable

from .base import InputSource, KeyEventCallback

logger = logging.getLogger("qwerty_instrument.input")

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
WM_QUIT = 0x0012

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

# Virtual-key code -> canonical KeyId, matching music/mapping.py's key space.
VK_TO_KEYID: dict[int, str] = {}
for _vk in range(0x41, 0x5B):  # 'A'..'Z'
    VK_TO_KEYID[_vk] = chr(_vk)
for _vk in range(0x30, 0x3A):  # '0'..'9'
    VK_TO_KEYID[_vk] = chr(_vk)
for _i in range(1, 13):  # F1..F12
    VK_TO_KEYID[0x70 + _i - 1] = f"F{_i}"
VK_TO_KEYID.update(
    {
        0x20: "SPACE",
        0x1B: "ESCAPE",
        0x09: "TAB",
        0x14: "CAPSLOCK",
        0x10: "SHIFT",
        0xA0: "SHIFT",
        0xA1: "SHIFT",
        0x11: "CTRL",
        0xA2: "CTRL",
        0xA3: "CTRL",
        0x12: "ALT",
        0xA4: "ALT",
        0xA5: "ALT",
        0x0D: "ENTER",
        0x08: "BACKSPACE",
        0xBC: "COMMA",
        0xBE: "PERIOD",
        0xBF: "SLASH",
        0xBA: "SEMICOLON",
        0xDE: "APOSTROPHE",
        0xBD: "MINUS",
        0xBB: "EQUALS",
        0xDB: "LBRACKET",
        0xDD: "RBRACKET",
        0xDC: "BACKSLASH",
        0xC0: "GRAVE",
        0x21: "PAGEUP",
        0x22: "PAGEDOWN",
        0x24: "HOME",
        0x23: "END",
        0x2D: "INSERT",
        0x2E: "DELETE",
        0x26: "UP",
        0x28: "DOWN",
        0x25: "LEFT",
        0x27: "RIGHT",
        # Multimedia/consumer VKs -- the F75 knob most commonly surfaces as
        # these in its default office/multimedia mode (spec section 8).
        # Verified per-machine by tools/f75_input_probe.py, not assumed.
        0xAD: "VOLUME_MUTE",
        0xAE: "VOLUME_DOWN",
        0xAF: "VOLUME_UP",
        0xB0: "MEDIA_NEXT",
        0xB1: "MEDIA_PREV",
        0xB2: "MEDIA_STOP",
        0xB3: "MEDIA_PLAY_PAUSE",
    }
)

SuppressPredicate = Callable[[str], bool]


class WindowsLowLevelKeyboardHook(InputSource):
    def __init__(self, should_suppress: SuppressPredicate | None = None):
        self._should_suppress = should_suppress or (lambda key_id: False)
        self._on_event: KeyEventCallback | None = None
        self._held: set[int] = set()
        self._hook_handle = None
        self._hookproc_ref = HOOKPROC(self._hook_proc)
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._capture_enabled = True
        self._ready = threading.Event()

    def start(self, on_event: KeyEventCallback) -> None:
        self._on_event = on_event
        self._thread = threading.Thread(target=self._run, name="qwerty-kb-hook", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=3.0):
            raise RuntimeError("keyboard hook thread failed to start within 3s")

    def stop(self) -> None:
        if self._thread_id is not None:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = None

    def set_capture_enabled(self, enabled: bool) -> None:
        self._capture_enabled = enabled
        if not enabled:
            self._held.clear()

    def set_suppress_predicate(self, should_suppress: SuppressPredicate) -> None:
        self._should_suppress = should_suppress

    def _run(self) -> None:
        self._thread_id = kernel32.GetCurrentThreadId()
        h_instance = kernel32.GetModuleHandleW(None)
        self._hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._hookproc_ref, h_instance, 0)
        if not self._hook_handle:
            err = ctypes.get_last_error()
            logger.error("SetWindowsHookExW failed, error=%d", err)
            self._ready.set()
            return
        logger.info("low-level keyboard hook installed")
        self._ready.set()
        msg = wintypes.MSG()
        while True:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        user32.UnhookWindowsHookEx(self._hook_handle)
        self._hook_handle = None
        logger.info("low-level keyboard hook removed")

    def _hook_proc(self, nCode: int, wParam: int, lParam: int) -> int:
        if nCode != HC_ACTION:
            return user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)

        ts = time.perf_counter_ns()
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        vk = kb.vkCode
        is_down = wParam in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up = wParam in (WM_KEYUP, WM_SYSKEYUP)

        key_id = VK_TO_KEYID.get(vk)
        suppress = False

        if key_id is not None:
            if key_id == "CAPSLOCK":
                suppress = True  # always intercepted: repurposed as capture toggle
            elif self._capture_enabled and self._should_suppress(key_id):
                suppress = True

            if is_down and vk not in self._held:
                self._held.add(vk)
                if self._on_event is not None:
                    self._on_event(key_id, True, ts)
            elif is_up and vk in self._held:
                self._held.discard(vk)
                if self._on_event is not None:
                    self._on_event(key_id, False, ts)
            # else: OS key-repeat (down while already held) -- ignored

        if suppress:
            return 1
        return user32.CallNextHookEx(self._hook_handle, nCode, wParam, lParam)
