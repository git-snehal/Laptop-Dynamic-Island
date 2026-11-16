"""
Dynamic Island for Windows  -  v2.6
===================================
* Solid pill with soft shadow, tinted by the album cover while music plays
* Real Bluetooth on/off tile
* Global hotkey built in (Ctrl+Space by default, falls back to Ctrl+Alt+Space)
* Single instance: starting it again just toggles the running island
"""

import os
import sys
import json
import time
import threading
import subprocess
from datetime import datetime

try:
    import psutil
except ImportError:
    psutil = None

import media
import bluetooth
import winkeys

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QRect, QRectF, QPointF, QPoint,
    QEasingCurve, Signal, QObject,
)
from PySide6.QtGui import (
    QFont, QFontMetrics, QFontDatabase, QAction, QCursor, QPainter, QColor,
    QPixmap, QPainterPath, QPen, QIcon, QPolygonF, QBrush, QLinearGradient, QRadialGradient,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
    QMenu, QDialog, QTextEdit, QGraphicsOpacityEffect, QSystemTrayIcon,
)

VERSION = "3.0.0"

# ------------------------------- CONFIG -------------------------------
COLLAPSED_W, COLLAPSED_H = 236, 36
EXPANDED_W,  EXPANDED_H  = 440, 240
TOP_MARGIN   = 79
ANIM_MS      = 260
SHADOW       = 16          # transparent margin around the pill that holds the shadow
DEFAULT_ACCENT = QColor(48, 209, 88)
TXT, TXT_DIM = "#f2f2f7", "#8e8e96"
WARN, BAD    = "#ff9f0a", "#ff453a"
TEXT_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Inter", "Arial"]
IPC_NAME     = "DynamicIsland.single-instance"

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
NOTES_PATH  = os.path.join(BASE_DIR, "notes.txt")
LOG_PATH    = os.path.join(BASE_DIR, "island.log")

# Segoe Fluent Icons / MDL2 code points (both ship with Windows 10/11)
ICONS = {
    "Spotify": "\uE8D6", "Camera": "\uE722", "Snipping": "\uE8C6",
    "Recorder": "\uE714", "Bluetooth": "\uE702", "Wi-Fi": "\uE701",
    "Clock": "\uE121", "Calculator": "\uE8EF", "Settings": "\uE713",
    "Notes": "\uE70F",
}
ICON_PREV, ICON_PLAY, ICON_PAUSE, ICON_NEXT = "\uE892", "\uE768", "\uE769", "\uE893"

DEFAULT_CONFIG = {
    "position": {"mode": "center", "x": None, "y": 79},
    "hotkey": "ctrl+space",
    "clock_24h": True,
    "appearance": {"fill_opacity": 0.98, "accent_from_art": True},
    "quick_apps": [
        {"label": "Spotify", "glyph": "♫", "color": "#1DB954", "type": "uri", "target": "spotify:"},
        {"label": "Camera", "glyph": "◉", "color": "#8B9CFF", "type": "uri", "target": "microsoft.windows.camera:"},
        {"label": "Snipping", "glyph": "✂", "color": "#8B9CFF", "type": "hotkey", "target": "win+shift+s"},
        {"label": "Recorder", "glyph": "●", "color": "#EF4444", "type": "hotkey", "target": "win+alt+r"},
        {"label": "Bluetooth", "glyph": "ᛒ", "color": "#3B9EFF", "type": "bluetooth", "target": ""},
        {"label": "Wi-Fi", "glyph": "◔", "color": "#60A5FA", "type": "uri", "target": "ms-settings:network-wifi"},
        {"label": "Clock", "glyph": "◷", "color": "#F59E0B", "type": "uri", "target": "ms-clock:"},
        {"label": "Calculator", "glyph": "∑", "color": "#A78BFA", "type": "exe", "target": "calc"},
        {"label": "Settings", "glyph": "⚙", "color": "#A1A1AA", "type": "uri", "target": "ms-settings:"},
        {"label": "Notes", "glyph": "✎", "color": "#EAB308", "type": "notes", "target": ""},
    ],
}
# ----------------------------------------------------------------------

ICON_FAMILY = None


def log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%H:%M:%S}  {msg}\n")
    except Exception:
        pass


def init_icon_font():
    global ICON_FAMILY
    fams = set(QFontDatabase.families())
    for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
        if name in fams:
            ICON_FAMILY = name
            return


def text_font(pt, weight=QFont.Weight.Normal):
    f = QFont()
    f.setFamilies(TEXT_FAMILIES)
    f.setPointSizeF(pt)
    f.setWeight(weight)
    return f


def icon_font(px):
    f = QFont(ICON_FAMILY or "Segoe UI Symbol")
    f.setPixelSize(px)
    return f


def load_config():
    cfg = None
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = None
    if cfg is None:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        save_config(cfg)
        return cfg
    cfg.pop("update", None)           # the self-updater was removed
    for k, v in DEFAULT_CONFIG.items():
        cfg.setdefault(k, json.loads(json.dumps(v)))
    # migrate: the old Bluetooth tile only opened Settings; make it a real toggle
    for e in cfg.get("quick_apps", []):
        if e.get("label") == "Bluetooth" and e.get("type") == "uri":
            e["type"], e["target"] = "bluetooth", ""
            if e.get("color") in (None, "#55D6D2"):
                e["color"] = "#3B9EFF"
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def launch(entry):
    t = entry.get("type")
    target = entry.get("target", "")
    try:
        if t == "uri":
            os.startfile(target)                    # noqa
        elif t == "exe":
            subprocess.Popen(target, shell=True, creationflags=0x08000000)  # no console flash
        elif t == "hotkey":
            winkeys.send_combo(target)
    except Exception:
        if entry.get("label") == "Spotify":
            try:
                os.startfile("https://open.spotify.com")  # noqa
            except Exception:
                pass


def open_bluetooth_settings():
    try:
        os.startfile("ms-settings:bluetooth")       # noqa
    except Exception:
        pass


def rounded_pixmap(pm, size, radius):
    if pm is None or pm.isNull():
        return None
    pm = pm.scaled(size, size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size, size, radius, radius)
    p.setClipPath(path)
    p.drawPixmap(0, 0, pm)
    p.end()
    return out


def placeholder_art(size):
    out = QPixmap(size, size)
    out.fill(Qt.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(255, 255, 255, 20))
    p.drawRoundedRect(0, 0, size, size, 10, 10)
    p.setPen(QColor(255, 255, 255, 110))
    p.setFont(icon_font(18) if ICON_FAMILY else text_font(14))
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, "\uE8D6" if ICON_FAMILY else "♪")
    p.end()
    return out


def accent_from(pm):
    try:
        img = pm.toImage().scaled(1, 1, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        c = img.pixelColor(0, 0)
        h, s, v, a = c.getHsv()
        if v < 150:
            c.setHsv(h, s, 190, a)
        return c
    except Exception:
        return DEFAULT_ACCENT


def tile_qss(color, active):
    c = QColor(color)
    rgb = f"{c.red()},{c.green()},{c.blue()}"
    hi = c.lighter(130).name()
    if active:
        return (f"QPushButton{{background:rgba({rgb},26%);color:{hi};border:none;border-radius:10px;}}"
                f"QPushButton:hover{{background:rgba({rgb},38%);}}")
    return ("QPushButton{background:rgba(255,255,255,7%);color:#d0d0d8;border:none;border-radius:10px;}"
            f"QPushButton:hover{{background:rgba({rgb},24%);color:{hi};}}")


# ----------------------------- small widgets -----------------------------

class BatteryIcon(QWidget):
    """Battery drawn with QPainter, so it never depends on a font having the glyph."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(27, 12)
        self.pct, self.plugged = None, False

    def set_state(self, pct, plugged):
        if (pct, plugged) != (self.pct, self.plugged):
            self.pct, self.plugged = pct, plugged
            self.update()

    def level_color(self):
        if self.plugged:
            return QColor(DEFAULT_ACCENT)
        if self.pct is not None and self.pct <= 10:
            return QColor(BAD)
        if self.pct is not None and self.pct <= 20:
            return QColor(WARN)
        return QColor(235, 235, 240)

    def paintEvent(self, _e):
        if self.pct is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        body = QRectF(0.5, 0.5, 23, 11)
        p.setPen(QPen(QColor(255, 255, 255, 120), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(body, 3.2, 3.2)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 120))
        p.drawRoundedRect(QRectF(24.5, 4, 2, 4), 1, 1)
        inner = body.adjusted(2, 2, -2, -2)
        w = max(1.5, inner.width() * self.pct / 100.0)
        p.setBrush(self.level_color())
        p.drawRoundedRect(QRectF(inner.x(), inner.y(), w, inner.height()), 1.6, 1.6)
        if self.plugged:
            p.setBrush(QColor(10, 10, 12, 230))
            p.drawPolygon(QPolygonF([QPointF(13.2, 2.2), QPointF(8.6, 6.6), QPointF(11.4, 6.6),
                                     QPointF(10.2, 9.8), QPointF(14.8, 5.3), QPointF(12.0, 5.3)]))
        p.end()


class EqIcon(QWidget):
    PATTERN = [(0.45, 1.0, 0.6), (0.9, 0.5, 0.85), (0.6, 0.9, 0.4), (1.0, 0.6, 0.75)]

    def __init__(self):
        super().__init__()
        self.setFixedSize(14, 12)
        self.phase, self.color = 0, QColor(DEFAULT_ACCENT)

    def step(self):
        self.phase = (self.phase + 1) % len(self.PATTERN)
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        p.setBrush(self.color)
        for i, f in enumerate(self.PATTERN[self.phase]):
            h = 3 + 9 * f
            p.drawRoundedRect(QRectF(i * 5, 12 - h, 3, h), 1.5, 1.5)
        p.end()


class ProgressBar(QWidget):
    seek = Signal(float)

    def __init__(self):
        super().__init__()
        self.setFixedHeight(10)
        self.frac, self.color = 0.0, QColor(DEFAULT_ACCENT)
        self.setCursor(Qt.PointingHandCursor)

    def set_value(self, frac, color):
        self.frac, self.color = max(0.0, min(1.0, frac)), color
        self.update()

    def mousePressEvent(self, e):
        self.seek.emit(max(0.0, min(1.0, e.position().x() / max(1, self.width()))))
        e.accept()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        y = (self.height() - 3) / 2
        p.setBrush(QColor(255, 255, 255, 30))
        p.drawRoundedRect(QRectF(0, y, self.width(), 3), 1.5, 1.5)
        if self.frac > 0:
            p.setBrush(self.color)
            p.drawRoundedRect(QRectF(0, y, max(3.0, self.width() * self.frac), 3), 1.5, 1.5)
        p.end()


class Card(QWidget):
    def __init__(self):
        super().__init__()
        self.tint = None

    def set_tint(self, color):
        self.tint = QColor(color) if color is not None else None
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        p.fillPath(path, QColor(255, 255, 255, 15))
        if self.tint is not None:
            a, b = QColor(self.tint), QColor(self.tint)
            a.setAlpha(46)
            b.setAlpha(6)
            g = QLinearGradient(rect.topLeft(), rect.topRight())
            g.setColorAt(0.0, a)
            g.setColorAt(1.0, b)
            p.fillPath(path, QBrush(g))
        p.end()


# ------------------------------- workers --------------------------------

class MediaWorker(QObject):
    track = Signal(object)
    art = Signal(object)

    def __init__(self):
        super().__init__()
        self._running = True
        self.active = True
        self._last_key = None
        self._have_art = False
        self._art_tries = 0

    def stop(self):
        self._running = False

    def run(self):
        while self._running:
            if not self.active:
                time.sleep(0.4)
                continue
            try:
                info = media.snapshot()
                if info:
                    self.track.emit(info)
                    key = info["title"] + "|" + info["artist"]
                    if key != self._last_key:
                        self._last_key, self._have_art, self._art_tries = key, False, 0
                    if not self._have_art and self._art_tries < 4:   # cover can arrive late
                        self._art_tries += 1
                        data = media.album_art()
                        self._have_art = bool(data)
                        if not data and self._art_tries == 4:
                            log(f"no cover for {info['title']!r}: {media.LAST_ART_ERROR or 'app sent no thumbnail'}")
                        self.art.emit(data)
                else:
                    if self._last_key is not None:
                        self.art.emit(None)
                    self._last_key = None
                    self.track.emit(None)
            except Exception:
                pass
            time.sleep(1.2)


class IPCServer(QObject):
    """Lets a second launch (hotkey script, double-click) talk to the running island."""
    command = Signal(str)

    def __init__(self):
        super().__init__()
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_conn)

    def start(self):
        QLocalServer.removeServer(IPC_NAME)
        return self.server.listen(IPC_NAME)

    def close(self):
        self.server.close()

    def _on_conn(self):
        sock = self.server.nextPendingConnection()
        if sock is None:
            return

        def read():
            self.command.emit(bytes(sock.readAll()).decode(errors="ignore").strip() or "toggle")
            sock.disconnectFromServer()
        sock.readyRead.connect(read)


def signal_running_instance(cmd=b"toggle"):
    sock = QLocalSocket()
    sock.connectToServer(IPC_NAME)
    if sock.waitForConnected(300):
        sock.write(cmd)
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


class NotesDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Quick Notes")
        self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
        self.resize(380, 340)
        self.setStyleSheet(
            "QDialog{background:#17171b;}"
            "QTextEdit{background:#0e0e11;color:#f0f0f0;border:1px solid #2a2a30;"
            "border-radius:12px;padding:12px;font-size:13px;selection-background-color:#3a3a40;}"
        )
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        self.edit = QTextEdit()
        self.edit.setFont(text_font(11))
        lay.addWidget(self.edit)
        try:
            with open(NOTES_PATH, "r", encoding="utf-8") as f:
                self.edit.setPlainText(f.read())
        except FileNotFoundError:
            pass
        self.edit.textChanged.connect(self._save)

    def _save(self):
        try:
            with open(NOTES_PATH, "w", encoding="utf-8") as f:
                f.write(self.edit.toPlainText())
        except Exception:
            pass


# -------------------------------- island ---------------------------------

class DynamicIsland(QWidget):
    bt_state = Signal(object)

    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.appearance = cfg.get("appearance", {})
        self._state = "collapsed"
        self.pinned = False
        self._press_global = None
        self._moved = False
        self._notes = None
        self._media = None
        self._prog = None              # (pos, dur, stamp, playing)
        self._accent = QColor(DEFAULT_ACCENT)
        self._has_art = False
        self._bt_btn = None
        self._bt_on = None
        self._bt_busy = False
        self.ipc = None

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # Two pages placed by hand (no layout on the window itself), so the pill can
        # animate to any size without the content fighting a minimum size.
        self.page_col = self._build_collapsed()
        self.page_exp = self._build_expanded()
        self.page_col.setParent(self)
        self.page_exp.setParent(self)
        self.page_exp.setFixedSize(EXPANDED_W, EXPANDED_H)
        self.page_exp.hide()
        self.fx_exp = QGraphicsOpacityEffect(self.page_exp)
        self.page_exp.setGraphicsEffect(self.fx_exp)
        self.fx_exp.setEnabled(False)

        self._anim = QPropertyAnimation(self, b"geometry")
        self._anim.setDuration(ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self._fade = QPropertyAnimation(self.fx_exp, b"opacity")
        self._fade.setEasingCurve(QEasingCurve.OutCubic)
        self._fade.finished.connect(self._on_fade_done)

        self._enter_timer = QTimer(self)
        self._enter_timer.setSingleShot(True)
        self._enter_timer.timeout.connect(self._check_enter)
        self._leave_timer = QTimer(self)
        self._leave_timer.setSingleShot(True)
        self._leave_timer.timeout.connect(self._check_leave)

        self.bt_state.connect(self._set_bt_state)

        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass

        self._anchor_cx, self._anchor_y = 0, TOP_MARGIN
        self._init_anchor()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._bt_timer = QTimer(self)
        self._bt_timer.timeout.connect(self._poll_bt)

        self._mworker = MediaWorker()
        self._mworker.track.connect(self._apply_track)
        self._mworker.art.connect(self._apply_art)
        threading.Thread(target=self._mworker.run, daemon=True).start()
        self._apply_track(None)
        self._apply_art(None)

    # ---------------------------- building UI ----------------------------

    def _build_collapsed(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(18, 0, 16, 0)
        lay.setSpacing(8)
        self.c_art = QLabel()
        self.c_art.setFixedSize(22, 22)
        self.c_art.hide()
        self.c_time = QLabel("--:--")
        self.c_time.setFont(text_font(10.5, QFont.Weight.DemiBold))
        self.c_time.setStyleSheet(f"color:{TXT};")
        self.c_eq = EqIcon()
        self.c_eq.hide()
        self.c_batt = BatteryIcon()
        self.c_pct = QLabel("")
        self.c_pct.setFont(text_font(9, QFont.Weight.DemiBold))
        self.c_pct.setStyleSheet(f"color:{TXT_DIM};")
        lay.addWidget(self.c_art)
        lay.addWidget(self.c_time)
        lay.addStretch(1)
        lay.addWidget(self.c_eq)
        lay.addWidget(self.c_batt)
        lay.addWidget(self.c_pct)
        return w

    def _build_expanded(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(20, 16, 20, 14)
        lay.setSpacing(10)

        # header: time + date on the left, battery on the right
        head = QHBoxLayout()
        left = QVBoxLayout()
        left.setSpacing(0)
        self.e_time = QLabel("--:--")
        self.e_time.setFont(text_font(23, QFont.Weight.DemiBold))
        self.e_time.setStyleSheet(f"color:{TXT};")
        self.e_date = QLabel("")
        self.e_date.setFont(text_font(9))
        self.e_date.setStyleSheet(f"color:{TXT_DIM};")
        left.addWidget(self.e_time)
        left.addWidget(self.e_date)
        head.addLayout(left)
        head.addStretch(1)
        self.e_batt = BatteryIcon()
        self.e_pct = QLabel("")
        self.e_pct.setFont(text_font(10, QFont.Weight.DemiBold))
        head.addWidget(self.e_batt, 0, Qt.AlignTop | Qt.AlignRight)
        head.addSpacing(6)
        head.addWidget(self.e_pct, 0, Qt.AlignTop | Qt.AlignRight)
        lay.addLayout(head)

        # now playing card
        card = Card()
        self.card = card
        card.setFixedHeight(80)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(10, 10, 10, 6)
        cl.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.np_art = QLabel()
        self.np_art.setFixedSize(44, 44)
        row.addWidget(self.np_art)
        txt = QVBoxLayout()
        txt.setSpacing(1)
        txt.addStretch(1)
        self.np_title = QLabel("Nothing playing")
        self.np_title.setFont(text_font(10.5, QFont.Weight.DemiBold))
        self.np_title.setStyleSheet(f"color:{TXT};")
        self.np_artist = QLabel("")
        self.np_artist.setFont(text_font(9))
        self.np_artist.setStyleSheet(f"color:{TXT_DIM};")
        txt.addWidget(self.np_title)
        txt.addWidget(self.np_artist)
        txt.addStretch(1)
        row.addLayout(txt, 1)
        self.btn_prev = self._media_btn(ICON_PREV, "⏮", media.skip_previous, 30, 14)
        self.btn_play = self._media_btn(ICON_PLAY, "▶", media.toggle_play_pause, 34, 14)
        self.btn_next = self._media_btn(ICON_NEXT, "⏭", media.skip_next, 30, 14)
        for b in (self.btn_prev, self.btn_play, self.btn_next):
            row.addWidget(b)
        cl.addLayout(row)
        bar_row = QHBoxLayout()
        bar_row.setSpacing(8)
        self.np_pos, self.np_dur = QLabel("0:00"), QLabel("-:--")
        for lbl, al in ((self.np_pos, Qt.AlignRight), (self.np_dur, Qt.AlignLeft)):
            lbl.setFont(text_font(7.5, QFont.Weight.DemiBold))
            lbl.setStyleSheet(f"color:{TXT_DIM};")
            lbl.setFixedWidth(30)
            lbl.setAlignment(al | Qt.AlignVCenter)
        self.np_bar = ProgressBar()
        self.np_bar.seek.connect(lambda f: threading.Thread(target=media.seek, args=(f,), daemon=True).start())
        bar_row.addWidget(self.np_pos)
        bar_row.addWidget(self.np_bar, 1)
        bar_row.addWidget(self.np_dur)
        cl.addLayout(bar_row)
        lay.addWidget(card)
        self._style_play_button(False)

        # quick apps
        qa = QHBoxLayout()
        qa.setSpacing(8)
        for entry in self.cfg.get("quick_apps", []):
            label = entry.get("label", "")
            color = entry.get("color", "#64748B")
            btn = QPushButton()
            btn.setFixedSize(32, 32)
            if ICON_FAMILY and label in ICONS:
                btn.setText(ICONS[label])
                btn.setFont(icon_font(15))
            else:
                btn.setText(entry.get("glyph", "?"))
                btn.setFont(QFont("Segoe UI Symbol", 12))
            btn.setToolTip(label)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet(tile_qss(color, False))
            btn.clicked.connect(lambda _=False, e=entry: self._on_quick_app(e))
            if entry.get("type") == "bluetooth":
                self._bt_btn, self._bt_color = btn, color
                btn.setContextMenuPolicy(Qt.CustomContextMenu)
                btn.customContextMenuRequested.connect(lambda _p: open_bluetooth_settings())
            qa.addWidget(btn)
        lay.addLayout(qa)

        # stats
        stats = QHBoxLayout()
        self.e_cpu, self.e_ram = QLabel("CPU --"), QLabel("RAM --")
        for s in (self.e_cpu, self.e_ram):
            s.setFont(text_font(8.5, QFont.Weight.DemiBold))
            s.setStyleSheet(f"color:{TXT_DIM};")
        stats.addWidget(self.e_cpu)
        stats.addStretch(1)
        stats.addWidget(self.e_ram)
        lay.addLayout(stats)
        return w

    def _media_btn(self, icon, fallback, slot, size, px):
        b = QPushButton(icon if ICON_FAMILY else fallback)
        b.setFixedSize(size, size)
        b.setFont(icon_font(px) if ICON_FAMILY else QFont("Segoe UI Symbol", 11))
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.setStyleSheet("QPushButton{background:transparent;color:#e6e6ec;border:none;border-radius:%dpx;}"
                        "QPushButton:hover{background:rgba(255,255,255,12%%);}" % (size // 2))
        b.clicked.connect(lambda: threading.Thread(target=slot, daemon=True).start())
        return b

    def _style_play_button(self, playing):
        self.btn_play.setText((ICON_PAUSE if playing else ICON_PLAY) if ICON_FAMILY else ("⏸" if playing else "▶"))
        self.btn_play.setStyleSheet(
            "QPushButton{background:#f2f2f7;color:#0a0a0c;border:none;border-radius:17px;}"
            "QPushButton:hover{background:#ffffff;}")

    # ------------------------------ painting ------------------------------

    def resizeEvent(self, e):
        S = SHADOW
        w = self.width() - 2 * S
        self.page_col.setGeometry(S, S, w, COLLAPSED_H)
        self.page_exp.move(S + (w - EXPANDED_W) // 2, S)
        super().resizeEvent(e)

    def _pill_rect(self):
        return self.geometry().adjusted(SHADOW, SHADOW, -SHADOW, -SHADOW)

    def paintEvent(self, _e):
        S = SHADOW
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(S + 0.5, S + 0.5, -S - 0.5, -S - 0.5)
        radius = min(r.height() / 2.0, 30.0)

        # soft drop shadow, built from fading outlines
        p.setBrush(Qt.NoBrush)
        reach = S - 4
        for i in range(1, reach):
            t = 1.0 - i / reach
            p.setPen(QPen(QColor(0, 0, 0, int(85 * t * t)), 1))
            p.drawRoundedRect(r.translated(0, 4).adjusted(-i, -i, i, i), radius + i, radius + i)

        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        alpha = max(225, min(255, int(255 * float(self.appearance.get("fill_opacity", 0.98)))))
        p.fillPath(path, QColor(10, 10, 12, alpha))

        # a faint wash of the cover colour in the top-left corner
        if self._has_art and self._media is not None:
            p.save()
            p.setClipPath(path)
            wash = QRadialGradient(QPointF(r.left() + 70, r.top() + 90), r.width() * 0.8)
            c1, c2 = QColor(self._accent), QColor(self._accent)
            c1.setAlpha(70)
            c2.setAlpha(0)
            wash.setColorAt(0.0, c1)
            wash.setColorAt(1.0, c2)
            p.fillRect(r, QBrush(wash))
            p.restore()

        # edge light: bright on top, fading down (accent coloured while playing)
        g = QLinearGradient(r.topLeft(), r.bottomLeft())
        if self._media and self._media.get("playing"):
            top, bot = QColor(self._accent), QColor(self._accent)
            top.setAlpha(150)
            bot.setAlpha(36)
        else:
            top, bot = QColor(255, 255, 255, 70), QColor(255, 255, 255, 16)
        g.setColorAt(0.0, top)
        g.setColorAt(1.0, bot)
        p.setPen(QPen(QBrush(g), 1))
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
        p.end()

    # ------------------------------ updating ------------------------------

    def _refresh(self):
        now = datetime.now()
        t = now.strftime("%H:%M") if self.cfg.get("clock_24h", True) else now.strftime("%I:%M").lstrip("0")
        self.c_time.setText(t)
        self.e_time.setText(t)
        self.e_date.setText(now.strftime("%A, %d %B"))

        pct, plugged = self._battery()
        for icon, label in ((self.c_batt, self.c_pct), (self.e_batt, self.e_pct)):
            icon.set_state(pct, plugged)
            icon.setVisible(pct is not None)
            label.setText("" if pct is None else f"{pct}%")
        color = TXT if pct is None else self.e_batt.level_color().name()
        self.e_pct.setStyleSheet(f"color:{color};")
        self.c_pct.setStyleSheet(f"color:{TXT_DIM};")

        if psutil is not None and self._state == "expanded":
            try:
                self.e_cpu.setText(f"CPU {int(psutil.cpu_percent(interval=None))}%")
                self.e_ram.setText(f"RAM {int(psutil.virtual_memory().percent)}%")
            except Exception:
                pass

        playing = bool(self._media and self._media.get("playing"))
        self.c_eq.setVisible(playing)
        self.c_art.setVisible(playing and self._has_art)
        self.c_eq.color = self._accent
        if playing:
            self.c_eq.step()

    def _battery(self):
        if psutil is None:
            return None, False
        try:
            b = psutil.sensors_battery()
            if b is None:
                return None, False
            return int(b.percent), bool(b.power_plugged)
        except Exception:
            return None, False

    def _tick(self):
        self._refresh()
        self._update_progress()

    @staticmethod
    def _fmt(sec):
        sec = max(0, int(sec))
        return f"{sec // 60}:{sec % 60:02d}"

    def _update_progress(self):
        if not self._prog:
            self.np_bar.set_value(0, self._accent)
            self.np_pos.setText("0:00")
            self.np_dur.setText("-:--")
            return
        pos, dur, stamp, playing = self._prog
        if playing:
            pos += time.monotonic() - stamp
        pos = min(pos, dur)
        self.np_bar.set_value(pos / dur if dur else 0, self._accent)
        self.np_pos.setText(self._fmt(pos))
        self.np_dur.setText(self._fmt(dur))

    def _elide(self, label, text, width):
        label.setText(QFontMetrics(label.font()).elidedText(text, Qt.ElideRight, width))

    def _apply_track(self, info):
        was_playing = bool(self._media and self._media.get("playing"))
        self._media = info
        if info:
            self._elide(self.np_title, info["title"], 200)
            self._elide(self.np_artist, info["artist"] or "Unknown artist", 200)
            self._style_play_button(info["playing"])
            if info.get("dur"):
                self._prog = (info["pos"], info["dur"], time.monotonic(), info["playing"])
            else:
                self._prog = None
        else:
            self._prog = None
            self.np_title.setText("Nothing playing")
            self.np_artist.setText("Play music or a video anywhere")
            self._style_play_button(False)
        if was_playing != bool(info and info["playing"]):
            self.update()
        self._update_progress()

    def _apply_art(self, data):
        pm = QPixmap()
        if data and pm.loadFromData(data) and not pm.isNull():
            self._has_art = True
            self.np_art.setPixmap(rounded_pixmap(pm, 44, 10))
            self.c_art.setPixmap(rounded_pixmap(pm, 22, 6))
            if self.appearance.get("accent_from_art", True):
                self._set_accent(accent_from(pm))
            self.card.set_tint(self._accent)
        else:
            self._has_art = False
            self.np_art.setPixmap(placeholder_art(44))
            self.card.set_tint(None)
            self._set_accent(QColor(DEFAULT_ACCENT))
        self.update()

    def _set_accent(self, color):
        if color.name() == self._accent.name():
            return
        self._accent = color
        if self._has_art:
            self.card.set_tint(color)
        self.update()

    # ------------------------------ bluetooth ------------------------------

    def _on_quick_app(self, entry):
        t = entry.get("type")
        if t == "notes":
            if self._notes is None:
                self._notes = NotesDialog(self)
            self._notes.show()
            self._notes.raise_()
            self._notes.activateWindow()
        elif t == "bluetooth":
            self._toggle_bt()
        else:
            threading.Thread(target=launch, args=(entry,), daemon=True).start()

    def _toggle_bt(self):
        if self._bt_busy:
            return
        self._bt_busy = True

        def work():
            res = bluetooth.toggle() if bluetooth.available() else None
            if res is None:
                open_bluetooth_settings()      # no API / no permission: fall back to Settings
            self.bt_state.emit(res if res is not None else bluetooth.get_state())
        threading.Thread(target=work, daemon=True).start()

    def _poll_bt(self):
        if self._bt_busy or not bluetooth.available():
            return
        threading.Thread(target=lambda: self.bt_state.emit(bluetooth.get_state()), daemon=True).start()

    def _set_bt_state(self, state):
        self._bt_busy = False
        self._bt_on = state
        if self._bt_btn is None:
            return
        self._bt_btn.setStyleSheet(tile_qss(self._bt_color, state is True))
        tip = {True: "Bluetooth: on  (click to turn off)", False: "Bluetooth: off  (click to turn on)",
               None: "Bluetooth  (click to open settings)"}[state]
        self._bt_btn.setToolTip(tip + "\nRight-click: Bluetooth settings")

    # ------------------------------ geometry ------------------------------

    def _init_anchor(self):
        geo = QApplication.primaryScreen().availableGeometry()
        pos = self.cfg.get("position", {})
        if pos.get("mode") == "custom" and pos.get("x") is not None:
            self._anchor_cx = int(pos["x"]) + COLLAPSED_W // 2
            self._anchor_y = int(pos["y"])
        else:
            self._anchor_cx = geo.center().x()
            self._anchor_y = geo.y() + TOP_MARGIN

    def _screen_geo(self):
        scr = QApplication.screenAt(QPoint(self._anchor_cx, self._anchor_y)) or QApplication.primaryScreen()
        return scr.availableGeometry()

    def _rect_for(self, w, h):
        """Window rect for a pill of size w x h (pill plus its transparent shadow margin)."""
        scr = self._screen_geo()
        x = max(scr.left() + 4, min(self._anchor_cx - w // 2, scr.right() - w - 4))
        y = max(scr.top(), min(self._anchor_y, scr.bottom() - h - 4))
        return QRect(x, y, w, h).adjusted(-SHADOW, -SHADOW, SHADOW, SHADOW)

    def _animate(self, w, h, expanding=False):
        curve = QEasingCurve(QEasingCurve.OutBack if expanding else QEasingCurve.OutCubic)
        if expanding:
            curve.setOvershoot(0.9)       # a touch of spring, like the real thing
        self._anim.setEasingCurve(curve)
        self._anim.stop()
        self._anim.setStartValue(self.geometry())
        self._anim.setEndValue(self._rect_for(w, h))
        self._anim.start()

    def expand(self):
        if self._state == "expanded":
            return
        self._state = "expanded"
        self.page_col.hide()
        self.page_exp.show()
        self.fx_exp.setEnabled(True)
        self._fade.stop()
        self._fade.setDuration(ANIM_MS + 70)
        self._fade.setKeyValues([(0.0, 0.0), (0.4, 0.0), (1.0, 1.0)])
        self._fade.start()
        self._animate(EXPANDED_W, EXPANDED_H, True)
        self._refresh()
        self._poll_bt()
        self._bt_timer.start(5000)

    def collapse(self):
        if self._state == "collapsed":
            return
        self._state = "collapsed"
        self._bt_timer.stop()
        self._fade.stop()
        self._fade.setDuration(110)
        self._fade.setKeyValues([(0.0, self.fx_exp.opacity()), (1.0, 0.0)])
        self._fade.start()
        self._animate(COLLAPSED_W, COLLAPSED_H)

    def _on_fade_done(self):
        if self._state == "collapsed":
            self.page_exp.hide()
            self.page_col.show()
        else:
            self.fx_exp.setEnabled(False)

    # ------------------------- show / hide (hotkey) -------------------------

    def show_island(self):
        self._state = "collapsed"
        self.pinned = False
        self._anim.stop()
        self._fade.stop()
        self.page_exp.hide()
        self.page_col.show()
        self.setGeometry(self._rect_for(COLLAPSED_W, COLLAPSED_H))
        self._refresh()
        self.show()
        self.raise_()
        self._set_active(True)

    def hide_island(self):
        self.pinned = False
        self._state = "collapsed"
        self._anim.stop()
        self.hide()
        self._set_active(False)

    def toggle_visible(self):
        if self.isVisible():
            self.hide_island()
        else:
            self.show_island()

    def on_command(self, cmd):
        if cmd == "show":
            if not self.isVisible():
                self.show_island()
        elif cmd == "hide":
            self.hide_island()
        elif cmd == "quit":
            QApplication.quit()
        else:
            self.toggle_visible()

    def _set_active(self, on):
        """Pause all polling while hidden so a hidden island costs almost nothing."""
        self._mworker.active = on
        if on:
            self._timer.start(1000)
        else:
            self._timer.stop()
            self._bt_timer.stop()

    # ------------------------------- mouse -------------------------------

    def enterEvent(self, event):
        self._leave_timer.stop()
        self._enter_timer.start(110)      # small delay: no accidental expands when passing by
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._enter_timer.stop()
        self._leave_timer.start(160)
        super().leaveEvent(event)

    def _check_enter(self):
        if self.isVisible() and self._pill_rect().contains(QCursor.pos()):
            self.expand()

    def _check_leave(self):
        if not self.pinned and not self._pill_rect().contains(QCursor.pos()):
            self.collapse()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._pill_rect().contains(event.globalPosition().toPoint()):
            self._press_global = event.globalPosition().toPoint()
            self._press_winpos = self.pos()
            self._moved = False

    def mouseMoveEvent(self, event):
        if self._press_global is not None and (event.buttons() & Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self._press_global
            if delta.manhattanLength() > 6:
                self._moved = True
                self._anim.stop()
                self.move(self._press_winpos + delta)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._press_global is not None:
            if self._moved:
                g = self.geometry()
                self._anchor_cx, self._anchor_y = g.center().x(), g.y() + SHADOW
                self._save_position()
            else:
                self.pinned = not self.pinned
                if self.pinned:
                    self.expand()
                elif not self._pill_rect().contains(QCursor.pos()):
                    self.collapse()
            self._press_global = None

    def _save_position(self):
        self.cfg["position"] = {"mode": "custom", "x": self._anchor_cx - COLLAPSED_W // 2, "y": self._anchor_y}
        save_config(self.cfg)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and (self._state == "expanded" or self.pinned):
            self.pinned = False
            self.collapse()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:#17171b;color:#f0f0f0;border:1px solid #2a2a30;padding:4px;border-radius:10px;}"
            "QMenu::item{padding:6px 20px;border-radius:6px;}"
            "QMenu::item:selected{background:#2c2c34;}")
        hide = QAction("Hide", self)
        hide.triggered.connect(self.hide_island)
        reset = QAction("Reset position to center", self)
        reset.triggered.connect(self._reset_position)
        quit_a = QAction(f"Quit  (v{VERSION})", self)
        quit_a.triggered.connect(QApplication.quit)
        menu.addAction(hide)
        menu.addAction(reset)
        menu.addSeparator()
        menu.addAction(quit_a)
        menu.exec(event.globalPos())

    def _reset_position(self):
        self.cfg["position"] = {"mode": "center", "x": None, "y": TOP_MARGIN}
        save_config(self.cfg)
        self._init_anchor()
        self.setGeometry(self._rect_for(self.width() - 2 * SHADOW, self.height() - 2 * SHADOW))


def make_tray_icon():
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(245, 245, 250))
    p.drawRoundedRect(3, 11, 26, 10, 5, 5)
    p.end()
    return QIcon(pm)


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setStyleSheet("QToolTip{background:#17171b;color:#f2f2f7;border:1px solid #2a2a30;"
                      "padding:5px 8px;border-radius:6px;}")

    # Already running? Then this launch just toggles it and exits.
    if signal_running_instance(b"toggle"):
        return

    open(LOG_PATH, "w").close()
    init_icon_font()
    log(f"v{VERSION} starting; icon font = {ICON_FAMILY}; bluetooth api = {bluetooth.available()}; "
        f"media api = {media.available()}")
    cfg = load_config()
    island = DynamicIsland(cfg)
    island.show_island()

    ipc = IPCServer()
    log("single-instance server: " + ("ok" if ipc.start() else "FAILED"))
    ipc.command.connect(island.on_command)
    island.ipc = ipc

    hk = winkeys.GlobalHotkey(island.toggle_visible)
    wanted = cfg.get("hotkey", "ctrl+space")
    used = hk.register([wanted, "ctrl+alt+space"])
    log(f"hotkey wanted={wanted} registered={used}")
    app.installNativeEventFilter(hk)
    app.aboutToQuit.connect(hk.unregister)

    tray = QSystemTrayIcon(make_tray_icon(), app)
    tray.setToolTip("Dynamic Island  -  " + (winkeys.pretty(used) if used else "no hotkey (use this icon)"))
    tmenu = QMenu()
    tmenu.addAction("Show / hide").triggered.connect(island.toggle_visible)
    tmenu.addSeparator()
    tmenu.addAction("Quit").triggered.connect(app.quit)
    tray.setContextMenu(tmenu)
    tray.activated.connect(lambda r: island.toggle_visible() if r == QSystemTrayIcon.Trigger else None)
    tray.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
