"""
Now-Playing info, album art + controls via Windows System Media Transport
Controls (SMTC). Drives whatever app owns media -- Spotify, a browser tab, etc.

Optional: needs 'winrt-Windows.Media.Control' (has Python 3.14 wheels). If it's
missing, every function returns a safe default so the island still runs.
"""

import asyncio
from datetime import datetime, timezone

_AVAILABLE = False
_Manager = None
_streams = None

try:
    from winrt.windows.media.control import (
        GlobalSystemMediaTransportControlsSessionManager as _Manager,
    )
    import winrt.windows.storage.streams as _streams
    _AVAILABLE = True
except Exception:
    try:
        from winsdk.windows.media.control import (
            GlobalSystemMediaTransportControlsSessionManager as _Manager,
        )
        import winsdk.windows.storage.streams as _streams
        _AVAILABLE = True
    except Exception:
        _AVAILABLE = False


def available() -> bool:
    return _AVAILABLE


async def _current_session():
    mgr = await _Manager.request_async()
    return mgr.get_current_session()


async def _info():
    session = await _current_session()
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    playback = session.get_playback_info()
    try:
        playing = int(playback.playback_status) == 4   # 4 = PLAYING
    except Exception:
        playing = False
    title = (props.title or "").strip()
    artist = (props.artist or "").strip()
    if not title and not artist:
        return None
    return {"title": title, "artist": artist, "playing": playing}


def now_playing():
    """Return {'title','artist','playing'} or None. Never raises."""
    if not _AVAILABLE:
        return None
    try:
        return asyncio.run(_info())
    except Exception:
        return None


async def _snapshot():
    session = await _current_session()
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    title = (props.title or "").strip()
    artist = (props.artist or "").strip()
    if not title and not artist:
        return None
    try:
        playing = int(session.get_playback_info().playback_status) == 4
    except Exception:
        playing = False
    pos = dur = None
    try:
        tl = session.get_timeline_properties()
        start = tl.start_time.total_seconds()
        d = tl.end_time.total_seconds() - start
        if d > 0:
            pos = max(0.0, tl.position.total_seconds() - start)
            if playing:
                try:   # position is only refreshed now and then; add the time since
                    lu = tl.last_updated_time
                    if lu.tzinfo is None:
                        lu = lu.replace(tzinfo=timezone.utc)
                    e = (datetime.now(timezone.utc) - lu).total_seconds()
                    if 0 <= e <= d:
                        pos += e
                except Exception:
                    pass
            pos, dur = min(pos, d), d
    except Exception:
        pass
    return {"title": title or "Unknown", "artist": artist, "playing": playing,
            "pos": pos, "dur": dur}


def snapshot():
    """One call: {'title','artist','playing','pos','dur'} or None. Never raises."""
    if not _AVAILABLE:
        return None
    try:
        return asyncio.run(_snapshot())
    except Exception:
        return None


async def _thumb():
    session = await _current_session()
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    ref = props.thumbnail
    if ref is None:
        return None
    stream = await ref.open_read_async()
    size = int(stream.size)
    if size <= 0:
        return None
    buf = _streams.Buffer(size)
    await stream.read_async(buf, buf.capacity, _streams.InputStreamOptions.READ_AHEAD)
    reader = _streams.DataReader.from_buffer(buf)
    n = int(buf.length)
    if n <= 0:
        return None
    out = bytearray(n)
    reader.read_bytes(out)        # fills the bytearray in place
    return bytes(out)


async def _thumb2():
    """Fallback reader: DataReader straight on the stream (works where the Buffer route fails)."""
    session = await _current_session()
    if session is None:
        return None
    props = await session.try_get_media_properties_async()
    ref = props.thumbnail
    if ref is None:
        return None
    stream = await ref.open_read_async()
    size = int(stream.size)
    if size <= 0:
        return None
    reader = _streams.DataReader(stream.get_input_stream_at(0))
    await reader.load_async(size)
    out = bytearray(size)
    reader.read_bytes(out)
    return bytes(out)


LAST_ART_ERROR = None


def album_art():
    """Return the current track's cover image as bytes (jpg/png), or None."""
    global LAST_ART_ERROR
    if not _AVAILABLE:
        return None
    for fn in (_thumb, _thumb2):
        try:
            data = asyncio.run(fn())
            if data:
                LAST_ART_ERROR = None
                return data
        except Exception as e:
            LAST_ART_ERROR = f"{fn.__name__}: {e!r}"
    return None


async def _do(action: str):
    session = await _current_session()
    if session is None:
        return
    if action == "toggle":
        await session.try_toggle_play_pause_async()
    elif action == "next":
        await session.try_skip_next_async()
    elif action == "prev":
        await session.try_skip_previous_async()


def _run(action: str):
    if not _AVAILABLE:
        return
    try:
        asyncio.run(_do(action))
    except Exception:
        pass


async def _timeline():
    session = await _current_session()
    if session is None:
        return None
    tl = session.get_timeline_properties()
    start = tl.start_time.total_seconds()
    end = tl.end_time.total_seconds()
    pos = tl.position.total_seconds()
    dur = end - start
    if dur <= 0:
        return None
    return (max(0.0, pos - start), dur)


def timeline():
    """Return (position_seconds, duration_seconds) or None."""
    if not _AVAILABLE:
        return None
    try:
        return asyncio.run(_timeline())
    except Exception:
        return None


async def _seek(fraction):
    session = await _current_session()
    if session is None:
        return
    tl = session.get_timeline_properties()
    start = tl.start_time.total_seconds()
    end = tl.end_time.total_seconds()
    dur = end - start
    if dur <= 0:
        return
    ticks = int((start + fraction * dur) * 10_000_000)
    await session.try_change_playback_position_async(ticks)


def seek(fraction):
    """Jump to a position given as a 0.0-1.0 fraction of the track."""
    if not _AVAILABLE:
        return
    try:
        asyncio.run(_seek(fraction))
    except Exception:
        pass


def toggle_play_pause():
    _run("toggle")


def skip_next():
    _run("next")


def skip_previous():
    _run("prev")
