"""
Real Bluetooth on/off via the Windows Radio API (WinRT). Optional + guarded.

Needs:  pip install winrt-Windows.Devices.Radios winrt-Windows.Foundation winrt-Windows.Foundation.Collections
If missing (or Windows refuses), every function returns None and the island
falls back to opening the Bluetooth settings page.
"""

import asyncio

_AVAILABLE = False
try:
    from winrt.windows.devices.radios import Radio, RadioKind, RadioState, RadioAccessStatus
    _AVAILABLE = True
except Exception:
    try:
        from winsdk.windows.devices.radios import Radio, RadioKind, RadioState, RadioAccessStatus
        _AVAILABLE = True
    except Exception:
        _AVAILABLE = False

_allowed = False


def available() -> bool:
    return _AVAILABLE


async def _bt_radio():
    global _allowed
    if not _allowed:
        status = await Radio.request_access_async()
        if status != RadioAccessStatus.ALLOWED:
            return None
        _allowed = True
    for r in await Radio.get_radios_async():
        if r.kind == RadioKind.BLUETOOTH:
            return r
    return None


async def _get():
    r = await _bt_radio()
    return None if r is None else (r.state == RadioState.ON)


async def _toggle():
    r = await _bt_radio()
    if r is None:
        return None
    turn_on = r.state != RadioState.ON
    status = await r.set_state_async(RadioState.ON if turn_on else RadioState.OFF)
    if status != RadioAccessStatus.ALLOWED:
        return None
    return turn_on


def get_state():
    """True = on, False = off, None = unknown / unavailable."""
    if not _AVAILABLE:
        return None
    try:
        return asyncio.run(_get())
    except Exception:
        return None


def toggle():
    """Flip Bluetooth. Returns the new state (True/False) or None if it failed."""
    if not _AVAILABLE:
        return None
    try:
        return asyncio.run(_toggle())
    except Exception:
        return None
