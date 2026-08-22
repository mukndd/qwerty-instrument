"""Audio device enumeration and WASAPI helpers.

Kept separate from engine.py because device queries do file/driver I/O and
must never run anywhere near the real-time callback.
"""

from __future__ import annotations

from dataclasses import dataclass

import sounddevice as sd


@dataclass
class DeviceInfo:
    index: int
    name: str
    hostapi_name: str
    max_output_channels: int
    default_samplerate: float


def list_host_apis() -> list[dict]:
    return list(sd.query_hostapis())


def list_output_devices() -> list[DeviceInfo]:
    hostapis = sd.query_hostapis()
    devices = sd.query_devices()
    out = []
    for i, d in enumerate(devices):
        if d["max_output_channels"] > 0:
            out.append(
                DeviceInfo(
                    index=i,
                    name=d["name"],
                    hostapi_name=hostapis[d["hostapi"]]["name"],
                    max_output_channels=d["max_output_channels"],
                    default_samplerate=d["default_samplerate"],
                )
            )
    return out


def find_wasapi_hostapi_index() -> int | None:
    for i, api in enumerate(sd.query_hostapis()):
        if "WASAPI" in api["name"]:
            return i
    return None


def default_wasapi_output_device() -> int | None:
    idx = find_wasapi_hostapi_index()
    if idx is None:
        return None
    api = sd.query_hostapis(idx)
    dev = api.get("default_output_device", -1)
    return dev if dev is not None and dev >= 0 else None


def make_wasapi_settings(exclusive: bool):
    """Return a sd.WasapiSettings for exclusive/shared mode, or None if unavailable."""
    try:
        return sd.WasapiSettings(exclusive=exclusive)
    except Exception:
        return None


def describe_device(index: int) -> DeviceInfo | None:
    for d in list_output_devices():
        if d.index == index:
            return d
    return None
