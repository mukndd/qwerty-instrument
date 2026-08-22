"""AULA F75 input probe.

Run this FIRST, before trusting any assumption this project makes about
what the F75's rotary knob actually sends to Windows (spec section 8: "DO
NOT ASSUME"). It logs, with timestamps, everything relevant that happens
when you:

    1. press a normal key
    2. rotate the knob clockwise
    3. rotate the knob counter-clockwise
    4. short-press the knob
    5. long-press the knob

...across three independent detection channels, since different keyboards
(and different firmware modes of the *same* keyboard) can surface the
knob through any of these:

    A) WH_KEYBOARD_LL   -- low-level keyboard hook: virtual-key code, scan
                            code, flags, up/down, OS timestamp
    B) WM_APPCOMMAND     -- the "multimedia command" message some drivers
                            use instead of (or in addition to) plain
                            VK_VOLUME_* keys
    C) Raw Input (WM_INPUT) -- registered for both the generic keyboard
                            usage page and the "Consumer Control" usage
                            page (where dedicated volume/media controls
                            usually live in the USB HID spec), including
                            which HID device handle produced the report

This is a read-only diagnostic: it does not suppress or alter any input
system-wide (unlike the real app's capture mode). Press Ctrl+C in this
console, or the window's close button if one appears, to stop. Run it
from an ordinary console window (not elevated) -- elevation is not
required and keeping privileges minimal is safer.

Usage:
    .venv\\Scripts\\python.exe tools\\f75_input_probe.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import datetime
import sys
import time

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---- shared constants -------------------------------------------------------

WH_KEYBOARD_LL = 13
HC_ACTION = 0
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0100, 0x0101, 0x0104, 0x0105
WM_APPCOMMAND = 0x0319
WM_INPUT = 0x00FF
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012

RIDEV_INPUTSINK = 0x00000100
RID_INPUT = 0x10000003
RIM_TYPEHID = 2
RIM_TYPEKEYBOARD = 1

WM_KEY_NAMES = {WM_KEYDOWN: "WM_KEYDOWN", WM_KEYUP: "WM_KEYUP", WM_SYSKEYDOWN: "WM_SYSKEYDOWN", WM_SYSKEYUP: "WM_SYSKEYUP"}

# Common VK names worth spelling out (multimedia + a few normal keys for sanity checks)
VK_NAMES = {
    0xAD: "VK_VOLUME_MUTE", 0xAE: "VK_VOLUME_DOWN", 0xAF: "VK_VOLUME_UP",
    0xB0: "VK_MEDIA_NEXT_TRACK", 0xB1: "VK_MEDIA_PREV_TRACK", 0xB2: "VK_MEDIA_STOP", 0xB3: "VK_MEDIA_PLAY_PAUSE",
    0x1B: "VK_ESCAPE", 0x20: "VK_SPACE", 0x14: "VK_CAPITAL",
}

# APPCOMMAND_* codes relevant to volume/media (from winuser.h)
APPCOMMANDS = {
    1: "APPCOMMAND_BROWSER_BACKWARD", 2: "APPCOMMAND_BROWSER_FORWARD", 8: "APPCOMMAND_VOLUME_MUTE",
    9: "APPCOMMAND_VOLUME_DOWN", 10: "APPCOMMAND_VOLUME_UP", 11: "APPCOMMAND_MEDIA_NEXTTRACK",
    12: "APPCOMMAND_MEDIA_PREVIOUSTRACK", 13: "APPCOMMAND_MEDIA_STOP", 14: "APPCOMMAND_MEDIA_PLAY_PAUSE",
    35: "APPCOMMAND_MEDIA_PLAY", 36: "APPCOMMAND_MEDIA_PAUSE",
}


def ts() -> str:
    return datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log(channel: str, msg: str) -> None:
    print(f"[{ts()}] [{channel:11s}] {msg}", flush=True)


# ---- Channel A: WH_KEYBOARD_LL ----------------------------------------------

LRESULT = ctypes.c_ssize_t
ULONG_PTR = ctypes.c_size_t
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


user32.SetWindowsHookExW.restype = wintypes.HHOOK
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.restype = wintypes.BOOL
user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]

# GetModuleHandleW's return is pointer-sized. Without an explicit restype,
# ctypes assumes a 32-bit C int and truncates the handle on 64-bit Python,
# which made SetWindowsHookExW fail with ERROR_MOD_NOT_FOUND (126) here
# during initial testing -- every Win32 call below gets an explicit
# signature for exactly this reason, not just the ones that happened to
# crash first.
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]

_hook_handle = None


def _kb_hook_proc(nCode, wParam, lParam):
    if nCode == HC_ACTION:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
        msg_name = WM_KEY_NAMES.get(wParam, hex(wParam))
        vk_name = VK_NAMES.get(kb.vkCode, "")
        log(
            "LL_HOOK",
            f"{msg_name:16s} vkCode=0x{kb.vkCode:02X} {vk_name:22s} scanCode=0x{kb.scanCode:03X} "
            f"flags=0x{kb.flags:X} os_time={kb.time}",
        )
    return user32.CallNextHookEx(_hook_handle, nCode, wParam, lParam)


_kb_hookproc_ref = HOOKPROC(_kb_hook_proc)


def install_keyboard_hook() -> None:
    global _hook_handle
    h_instance = kernel32.GetModuleHandleW(None)
    _hook_handle = user32.SetWindowsHookExW(WH_KEYBOARD_LL, _kb_hookproc_ref, h_instance, 0)
    if not _hook_handle:
        log("LL_HOOK", f"FAILED to install (error={ctypes.get_last_error()})")
    else:
        log("LL_HOOK", "installed OK")


# ---- Channels B & C: hidden window for WM_APPCOMMAND + WM_INPUT ------------

WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON), ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH), ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [("usUsagePage", wintypes.USHORT), ("usUsage", wintypes.USHORT), ("dwFlags", wintypes.DWORD), ("hwndTarget", wintypes.HWND)]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [("dwType", wintypes.DWORD), ("dwSize", wintypes.DWORD), ("hDevice", wintypes.HANDLE), ("wParam", wintypes.WPARAM)]


class RAWKEYBOARD(ctypes.Structure):
    _fields_ = [
        ("MakeCode", wintypes.USHORT), ("Flags", wintypes.USHORT), ("Reserved", wintypes.USHORT),
        ("VKey", wintypes.USHORT), ("Message", wintypes.UINT), ("ExtraInformation", wintypes.ULONG),
    ]


class RAWHID(ctypes.Structure):
    _fields_ = [("dwSizeHid", wintypes.DWORD), ("dwCount", wintypes.DWORD), ("bRawData", ctypes.c_byte * 1)]


HWND_MESSAGE = wintypes.HWND(-3)

user32.RegisterClassW.restype = wintypes.ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.RegisterRawInputDevices.restype = wintypes.BOOL
user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE), wintypes.UINT, wintypes.UINT]
user32.GetRawInputData.restype = wintypes.UINT
user32.GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(wintypes.UINT), wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT]
user32.TranslateMessage.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(wintypes.MSG)]
user32.PostQuitMessage.argtypes = [ctypes.c_int]
user32.DestroyWindow.restype = wintypes.BOOL
user32.DestroyWindow.argtypes = [wintypes.HWND]

_seen_devices: set[int] = set()


def _wnd_proc(hwnd, msg, wParam, lParam):
    if msg == WM_APPCOMMAND:
        cmd = (lParam >> 16) & ~0xF000
        name = APPCOMMANDS.get(cmd, f"cmd={cmd}")
        log("APPCOMMAND", f"WM_APPCOMMAND {name} (raw lParam=0x{lParam:08X})")
        return 1
    if msg == WM_INPUT:
        _handle_raw_input(lParam)
        return 0
    if msg in (WM_DESTROY, WM_CLOSE):
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wParam, lParam)


_wndproc_ref = WNDPROC(_wnd_proc)


def _handle_raw_input(lParam: int) -> None:
    size = wintypes.UINT(0)
    user32.GetRawInputData(lParam, RID_INPUT, None, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
    if size.value == 0:
        return
    buf = ctypes.create_string_buffer(size.value)
    got = user32.GetRawInputData(lParam, RID_INPUT, buf, ctypes.byref(size), ctypes.sizeof(RAWINPUTHEADER))
    if got != size.value:
        return
    header = ctypes.cast(buf, ctypes.POINTER(RAWINPUTHEADER)).contents
    device_handle = int(header.hDevice) if header.hDevice else 0
    new_device = device_handle not in _seen_devices
    _seen_devices.add(device_handle)
    device_note = " (new device handle this run)" if new_device else ""

    if header.dwType == RIM_TYPEKEYBOARD:
        kb_ptr = ctypes.cast(buf[ctypes.sizeof(RAWINPUTHEADER):], ctypes.POINTER(RAWKEYBOARD))
        kb = kb_ptr.contents
        vk_name = VK_NAMES.get(kb.VKey, "")
        log(
            "RAW_INPUT",
            f"KEYBOARD device=0x{device_handle:X}{device_note} VKey=0x{kb.VKey:02X} {vk_name:20s} "
            f"MakeCode=0x{kb.MakeCode:03X} Flags=0x{kb.Flags:X} Message=0x{kb.Message:X}",
        )
    elif header.dwType == RIM_TYPEHID:
        hid_offset = ctypes.sizeof(RAWINPUTHEADER)
        dw_size_hid, dw_count = ctypes.cast(buf[hid_offset:hid_offset + 8], ctypes.POINTER(wintypes.DWORD * 2)).contents
        raw_bytes = buf[hid_offset + 8: hid_offset + 8 + dw_size_hid]
        hexdump = " ".join(f"{b:02X}" for b in raw_bytes[:32])
        log(
            "RAW_INPUT",
            f"HID (consumer/other) device=0x{device_handle:X}{device_note} reportSize={dw_size_hid} count={dw_count} bytes=[{hexdump}]",
        )
        log("RAW_INPUT", "  (raw HID report bytes -- exact field meaning depends on this device's report descriptor;")
        log("RAW_INPUT", "   if this fires when you rotate/press the knob, that's strong evidence it's a Consumer Control HID device.)")


def register_raw_input(hwnd) -> None:
    devices = (RAWINPUTDEVICE * 2)(
        RAWINPUTDEVICE(usUsagePage=0x01, usUsage=0x06, dwFlags=RIDEV_INPUTSINK, hwndTarget=hwnd),  # Generic Desktop / Keyboard
        RAWINPUTDEVICE(usUsagePage=0x0C, usUsage=0x01, dwFlags=RIDEV_INPUTSINK, hwndTarget=hwnd),  # Consumer Control
    )
    ok = user32.RegisterRawInputDevices(devices, 2, ctypes.sizeof(RAWINPUTDEVICE))
    if ok:
        log("RAW_INPUT", "registered for Keyboard (page 1/usage 6) and Consumer Control (page 0x0C/usage 1)")
    else:
        log("RAW_INPUT", f"FAILED to register (error={ctypes.get_last_error()})")


def create_hidden_window():
    h_instance = kernel32.GetModuleHandleW(None)
    class_name = "QwertyInstrumentF75ProbeWindow"
    wc = WNDCLASSW(style=0, lpfnWndProc=_wndproc_ref, cbClsExtra=0, cbWndExtra=0, hInstance=h_instance, hIcon=None, hCursor=None, hbrBackground=None, lpszMenuName=None, lpszClassName=class_name)
    if not user32.RegisterClassW(ctypes.byref(wc)):
        err = ctypes.get_last_error()
        if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS
            log("APPCOMMAND", f"RegisterClassW failed (error={err})")
    hwnd = user32.CreateWindowExW(0, class_name, "f75 probe", 0, 0, 0, 0, 0, HWND_MESSAGE, None, h_instance, None)
    if not hwnd:
        log("APPCOMMAND", f"CreateWindowExW failed (error={ctypes.get_last_error()})")
    else:
        log("APPCOMMAND", "hidden message-only window created (catches WM_APPCOMMAND + WM_INPUT)")
    return hwnd


def main() -> int:
    print("=" * 78)
    print("AULA F75 input probe")
    print("=" * 78)
    print("Now: 1) press a normal letter key   2) rotate the knob clockwise")
    print("     3) rotate counter-clockwise    4) short-press the knob")
    print("     5) long-press (~1s) the knob")
    print("Watch which channel(s) light up for each action. Ctrl+C to stop.")
    print("-" * 78)

    install_keyboard_hook()
    hwnd = create_hidden_window()
    if hwnd:
        register_raw_input(hwnd)

    msg = wintypes.MSG()
    try:
        while True:
            # PeekMessage with PM_REMOVE so Ctrl+C in the console can still interrupt us
            has_msg = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
            if has_msg:
                if msg.message == WM_QUIT:
                    break
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            else:
                time.sleep(0.001)
    except KeyboardInterrupt:
        print("\nStopping probe...")
    finally:
        if _hook_handle:
            user32.UnhookWindowsHookEx(_hook_handle)
        if hwnd:
            user32.DestroyWindow(hwnd)
    return 0


if __name__ == "__main__":
    if sys.platform != "win32":
        print("This probe is Windows-only (uses Win32 hooks/raw input).")
        sys.exit(1)
    sys.exit(main())
