"""
Native Windows global hotkey + key sender (no 'keyboard' package needed).

RegisterHotKey is the same mechanism Windows itself uses for shortcuts. Unlike a
keyboard hook it keeps working when the foreground app is elevated (admin), and
it is not affected by key-repeat or hook timeouts.
"""

import sys
import time
import ctypes
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter

_WIN = sys.platform == "win32"
WM_HOTKEY = 0x0312
MOD_NOREPEAT = 0x4000
_MODS = {"alt": 0x0001, "ctrl": 0x0002, "control": 0x0002, "shift": 0x0004, "win": 0x0008}
_MOD_VK = {"alt": 0x12, "ctrl": 0x11, "control": 0x11, "shift": 0x10, "win": 0x5B}
_NAMED = {"space": 0x20, "tab": 0x09, "enter": 0x0D, "esc": 0x1B, "escape": 0x1B,
          "backspace": 0x08, "`": 0xC0, ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD,
          ".": 0xBE, "/": 0xBF, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE}


def _vk(name):
    name = name.lower()
    if name in _NAMED:
        return _NAMED[name]
    if len(name) == 1 and name.isalnum():
        return ord(name.upper())
    if name.startswith("f") and name[1:].isdigit() and 1 <= int(name[1:]) <= 24:
        return 0x70 + int(name[1:]) - 1
    return None


def parse(combo):
    """'ctrl+alt+space' -> (modifier_mask, virtual_key) or None."""
    mods, key = 0, None
    for part in str(combo).lower().replace(" ", "").split("+"):
        if part in _MODS:
            mods |= _MODS[part]
        elif key is None:
            key = _vk(part)
        else:
            return None
    return (mods, key) if key else None


def pretty(combo):
    return "+".join(p.capitalize() for p in str(combo).split("+"))


class GlobalHotkey(QAbstractNativeEventFilter):
    """Install with app.installNativeEventFilter(obj); calls callback() on the hotkey."""

    _ID = 0x4449  # arbitrary, unique to this app

    def __init__(self, callback):
        super().__init__()
        self._cb = callback
        self.active = None

    def register(self, combos):
        """Try each combo in order. Returns the one that worked, or None."""
        if not _WIN:
            return None
        self.unregister()
        for combo in combos:
            parsed = parse(combo)
            if not parsed:
                continue
            mods, vk = parsed
            if ctypes.windll.user32.RegisterHotKey(None, self._ID, mods | MOD_NOREPEAT, vk):
                self.active = combo
                return combo
        return None

    def unregister(self):
        if _WIN and self.active:
            ctypes.windll.user32.UnregisterHotKey(None, self._ID)
        self.active = None

    def nativeEventFilter(self, event_type, message):
        try:
            if "windows" in bytes(event_type).decode(errors="ignore"):
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY and msg.wParam == self._ID:
                    self._cb()
                    return True, 0
        except Exception:
            pass
        return False, 0


def send_combo(combo):
    """Press a key combo system-wide, e.g. 'win+shift+s'."""
    if not _WIN:
        return
    parts = str(combo).lower().replace(" ", "").split("+")
    mods = [_MOD_VK[p] for p in parts if p in _MOD_VK]
    keys = [_vk(p) for p in parts if p not in _MOD_VK]
    keys = [k for k in keys if k]
    u = ctypes.windll.user32
    for m in mods:
        u.keybd_event(m, 0, 0, 0)
    for k in keys:
        u.keybd_event(k, 0, 0, 0)
        u.keybd_event(k, 0, 2, 0)
    time.sleep(0.02)
    for m in reversed(mods):
        u.keybd_event(m, 0, 2, 0)
