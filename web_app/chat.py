# -*- coding: utf-8 -*-
"""Realtime anonymous chat backed by SQLite.

This module degrades gracefully when `flask_socketio` is unavailable so the
rest of the Flask app can still boot and serve scan APIs.
"""

import logging
import os
import random
import sqlite3
import threading
import time

try:
    from flask_socketio import SocketIO, emit
    socketio = SocketIO(cors_allowed_origins="*")
except Exception as e:
    logging.warning("flask_socketio unavailable; chat disabled: %s", e)

    def emit(*_args, **_kwargs):
        return None

    class _SocketIODummy:
        def init_app(self, app, *args, **kwargs):
            app.logger.warning("SocketIO disabled; chat features are unavailable.")

        def on(self, *_args, **_kwargs):
            def _decorator(fn):
                return fn
            return _decorator

        def run(self, app, *args, **kwargs):
            kwargs.pop("allow_unsafe_werkzeug", None)
            app.run(*args, **kwargs)

    socketio = _SocketIODummy()


_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "chat.db")
_db_lock = threading.Lock()

_ADMIN_PW = os.environ.get("CHAT_ADMIN_PW", "admin1234")

_ADJ = [
    "급등노리는",
    "차분한",
    "눌림보는",
    "돌파추적",
    "관망중인",
    "분할매수",
    "추세추종",
    "모멘텀찾는",
]

_NOUN = [
    "트레이더",
    "투자자",
    "고수",
    "개미",
    "차트러",
    "스캐너",
    "매매러",
    "관찰자",
]


def _random_nick() -> str:
    return f"{random.choice(_ADJ)}{random.choice(_NOUN)}"


def _get_db():
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts REAL NOT NULL,
        sid TEXT NOT NULL,
        nick TEXT NOT NULL,
        msg TEXT NOT NULL
    )"""
    )
    conn.commit()
    return conn


_get_db().close()

_nicks: dict[str, str] = {}
_online: int = 0
_online_lock = threading.Lock()
_admins: set[str] = set()
_banned: set[str] = set()


def _recent_messages(limit=50) -> list[dict]:
    with _db_lock:
        conn = _get_db()
        rows = conn.execute(
            "SELECT id, ts, nick, msg FROM messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        conn.close()
    return [{"id": r[0], "ts": r[1], "nick": r[2], "msg": r[3]} for r in reversed(rows)]


def _save_message(sid: str, nick: str, msg: str) -> int:
    with _db_lock:
        conn = _get_db()
        cur = conn.execute(
            "INSERT INTO messages (ts, sid, nick, msg) VALUES (?, ?, ?, ?)",
            (time.time(), sid, nick, msg),
        )
        msg_id = cur.lastrowid
        conn.commit()
        conn.close()
    return msg_id


@socketio.on("connect")
def on_connect():
    from flask import request

    global _online
    sid = request.sid
    nick = _random_nick()
    _nicks[sid] = nick
    with _online_lock:
        _online += 1
        cnt = _online
    emit("init", {"nick": nick, "history": _recent_messages(), "online": cnt})
    emit("system", {"msg": f"{nick} 입장", "online": cnt}, broadcast=True)


@socketio.on("disconnect")
def on_disconnect():
    from flask import request

    global _online
    sid = request.sid
    nick = _nicks.pop(sid, "?")
    _admins.discard(sid)
    _banned.discard(sid)
    with _online_lock:
        _online = max(0, _online - 1)
        cnt = _online
    emit("system", {"msg": f"{nick} 퇴장", "online": cnt}, broadcast=True)


@socketio.on("chat")
def on_chat(data):
    from flask import request

    sid = request.sid
    if sid in _banned:
        emit("system_private", {"msg": "채팅이 차단되었습니다."})
        return
    nick = _nicks.get(sid, "익명")
    msg = (data.get("msg") or "").strip()
    if not msg or len(msg) > 500:
        return
    msg_id = _save_message(sid, nick, msg)
    emit("chat", {"id": msg_id, "ts": time.time(), "nick": nick, "msg": msg}, broadcast=True)


@socketio.on("admin_login")
def on_admin_login(data):
    from flask import request

    pw = (data.get("pw") or "").strip()
    sid = request.sid
    if pw == _ADMIN_PW:
        _admins.add(sid)
        emit("admin_ok", {"ok": True})
    else:
        emit("admin_ok", {"ok": False})


@socketio.on("admin_delete")
def on_admin_delete(data):
    from flask import request

    if request.sid not in _admins:
        return
    msg_id = data.get("id")
    if not msg_id:
        return
    with _db_lock:
        conn = _get_db()
        conn.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
        conn.commit()
        conn.close()
    emit("msg_deleted", {"id": msg_id}, broadcast=True)


@socketio.on("admin_ban")
def on_admin_ban(data):
    from flask import request

    if request.sid not in _admins:
        return
    target_nick = data.get("nick")
    if not target_nick:
        return
    target_sid = None
    for sid, nick in list(_nicks.items()):
        if nick == target_nick:
            target_sid = sid
            break
    if target_sid:
        _banned.add(target_sid)
        emit("banned", {}, to=target_sid)
        emit("system", {"msg": f"{target_nick} 차단됨", "online": _online}, broadcast=True)


@socketio.on("admin_unban")
def on_admin_unban(data):
    from flask import request

    if request.sid not in _admins:
        return
    target_nick = data.get("nick")
    if not target_nick:
        return
    target_sid = None
    for sid, nick in list(_nicks.items()):
        if nick == target_nick:
            target_sid = sid
            break
    if target_sid:
        _banned.discard(target_sid)
        emit("unbanned", {}, to=target_sid)
        emit("system", {"msg": f"{target_nick} 차단 해제", "online": _online}, broadcast=True)


@socketio.on("admin_announce")
def on_admin_announce(data):
    from flask import request

    if request.sid not in _admins:
        return
    msg = (data.get("msg") or "").strip()
    if not msg or len(msg) > 500:
        return
    _save_message("system", "공지", msg)
    emit("announce", {"msg": msg, "ts": time.time()}, broadcast=True)


@socketio.on("admin_clear")
def on_admin_clear(_data=None):
    from flask import request

    if request.sid not in _admins:
        return
    with _db_lock:
        conn = _get_db()
        conn.execute("DELETE FROM messages")
        conn.commit()
        conn.close()
    emit("chat_cleared", {}, broadcast=True)
