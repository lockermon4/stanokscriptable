"""Веб-админка (/admin): рассылки, статистика, логи. Только для владельца.

Защита (несколько слоёв, ни один не полагается на скрытность пути):
  1. ADMIN_TOKEN — длинный секрет (короче 16 символов считается незаданным).
     Без токена панель отключена: ВСЕ /admin/* отдают 404, будто её нет.
  2. Неверный токен / нет сессии — тоже 404, а не 401 (не подтверждаем
     сканерам, что панель существует).
  3. Успешный логин выдаёт случайную сессию в HttpOnly-куке (SameSite=Lax,
     TTL 12 ч). Сам токен после логина нигде не хранится и не светится.
  4. Рассылка — только в два шага: превью (с числом получателей) выдаёт
     одноразовый ключ-подтверждение (TTL 10 мин), отправка — только по нему.
     Случайный даблклик / повторный POST ничего не пошлёт дважды.
  5. Отправка троттлится (~20 сообщений/с), ошибки по пользователям
     собираются в failed, а не валят всю рассылку.

Токен — только в env (.env локально, Render Dashboard в проде), в git
уходит лишь пустой плейсхолдер.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
from collections import Counter, deque
from typing import Any

from aiohttp import web

log = logging.getLogger("bot.admin")

COOKIE = "admin_session"
SESSION_TTL_S = 12 * 3600
PENDING_TTL_S = 10 * 60
TOKEN_MIN_LEN = 16
TEXT_LIMIT = 4000
SEND_PAUSE_S = 0.05
LOG_BUF_N = 300

_sessions: dict[str, float] = {}
_pending: dict[str, tuple[float, str, str, str]] = {}  # key -> (ts, text, scope, group)
_logbuf: deque[str] = deque(maxlen=LOG_BUF_N)
_buf_installed = False


class _BufHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            _logbuf.append(self.format(record))
        except Exception:
            pass


def install_log_buffer() -> None:
    """Кольцевой буфер последних логов для /admin (один раз на процесс)."""
    global _buf_installed
    if _buf_installed:
        return
    h = _BufHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                                     datefmt="%H:%M:%S"))
    logging.getLogger().addHandler(h)
    _buf_installed = True


def _token(ctx: Any) -> str:
    t = str(getattr(getattr(ctx, "settings", None), "admin_token", "") or "")
    return t if len(t) >= TOKEN_MIN_LEN else ""


def _ctx(request: web.Request) -> Any:
    from .health import API_CTX_KEY

    return request.app[API_CTX_KEY]


def _authed(request: web.Request) -> bool:
    ctx = _ctx(request)
    if not _token(ctx):
        return False
    sid = request.cookies.get(COOKIE, "")
    ts = _sessions.get(sid)
    if not ts:
        return False
    if time.monotonic() - ts > SESSION_TTL_S:
        _sessions.pop(sid, None)
        return False
    return True


def _deny() -> web.Response:
    """404 вместо 401: не подтверждаем существование панели."""
    raise web.HTTPNotFound()


def _recipients(ctx: Any, scope: str, group: str) -> list:
    try:
        users = ctx.store.all_users()
    except Exception as e:
        log.warning("admin users failed: %s", e)
        return []
    if scope == "group":
        users = [u for u in users if u.group == group]
    return users


# ---------- HTML ----------

_LOGIN_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin</title>
<style>body{{font-family:system-ui,sans-serif;max-width:420px;margin:8vh auto;padding:0 16px}}
input,button{{font-size:16px;padding:8px;width:100%;box-sizing:border-box;margin-top:8px}}</style>
</head><body><h2>🔐 Админка</h2>
<form method="post" action="/admin/login"><input type="password" name="token" autofocus
placeholder="ADMIN_TOKEN" autocomplete="off"><button>Войти</button></form></body></html>"""

DASH_HTML = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Admin</title>
<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:4vh auto;padding:0 16px}
textarea,input,select,button{font-size:15px;padding:8px;box-sizing:border-box}
textarea{width:100%;height:110px}input[type=text]{width:100%}
.card{border:1px solid #ccc;border-radius:8px;padding:12px;margin:12px 0}
pre{background:#111;color:#0d0;max-height:300px;overflow:auto;padding:8px;font-size:12px}
.row{display:flex;gap:8px;margin-top:8px}button{cursor:pointer}</style>
</head><body><h2>🛠 Админка <small><a href="#" id="logout">выйти</a></small></h2>
<div class="card"><b>📊 Статистика</b><div id="stats">…</div></div>
<div class="card"><b>📣 Рассылка</b>
<textarea id="text" placeholder="Текст оповещения…"></textarea>
<div class="row"><select id="scope"><option value="all">Всем</option>
<option value="group">По группе</option></select>
<input type="text" id="group" placeholder="Группа (для scope=группа)"></div>
<div class="row"><button id="preview">1. Предпросмотр</button>
<button id="send" disabled>2. Разослать</button></div>
<div id="bcast"></div></div>
<div class="card"><b>📜 Логи</b> <button id="logs">Обновить</button><pre id="logpre">…</pre></div>
<script>
let confirmKey=null;
async function api(path,opts){let r=await fetch(path,opts);if(r.status==404){location.reload();return null}return r.json()}
async function stats(){let s=await api('/admin/api/stats');if(!s)return;
document.getElementById('stats').textContent=JSON.stringify(s,null,1)}
preview.onclick=async()=>{confirmKey=null;send.disabled=true;
let r=await api('/admin/api/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({text:text.value,scope:scope.value,group:group.value})});
if(!r)return;bcast.textContent=JSON.stringify(r,null,1);
if(r.need_confirm){confirmKey=r.key;send.disabled=false}};
send.onclick=async()=>{if(!confirmKey)return;send.disabled=true;
let r=await api('/admin/api/broadcast',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({confirm_key:confirmKey})});
confirmKey=null;bcast.textContent=JSON.stringify(r,null,1)};
logout.onclick=async(e)=>{e.preventDefault();await fetch('/admin/logout',{method:'POST'});location.reload()};
logs.onclick=async()=>{let l=await api('/admin/api/logs?limit=100');if(l)logpre.textContent=l.lines.join('\\n')};
stats();
</script></body></html>"""


# ---------- handlers ----------

async def index(request: web.Request) -> web.Response:
    if not _token(_ctx(request)):
        _deny()
    if not _authed(request):
        return web.Response(text=_LOGIN_HTML, content_type="text/html")
    return web.Response(text=DASH_HTML, content_type="text/html")


async def login(request: web.Request) -> web.Response:
    ctx = _ctx(request)
    want = _token(ctx)
    if not want:
        _deny()
    try:
        form = await request.post()
        got = str(form.get("token", "") or "")
    except Exception:
        got = ""
    if not got or not hmac.compare_digest(got, want):
        log.warning("admin login failed from %s", request.remote)
        _deny()
    sid = secrets.token_urlsafe(24)
    _sessions[sid] = time.monotonic()
    log.info("admin login ok from %s", request.remote)
    resp = web.HTTPSeeOther("/admin/")
    resp.set_cookie(COOKIE, sid, httponly=True, samesite="Lax", path="/admin/")
    raise resp


async def logout(request: web.Request) -> web.Response:
    _sessions.pop(request.cookies.get(COOKIE, ""), None)
    resp = web.json_response({"ok": True})
    resp.del_cookie(COOKIE, path="/admin/")
    return resp


async def stats(request: web.Request) -> web.Response:
    if not _authed(request):
        _deny()
    users = _recipients(_ctx(request), "all", "")
    return web.json_response({
        "users": len(users),
        "groups": dict(Counter(u.group or "—" for u in users).most_common(10)),
        "with_home": sum(1 for u in users if u.home_lat is not None),
        "langs": dict(Counter(u.lang or "ru" for u in users)),
    })


async def logs(request: web.Request) -> web.Response:
    if not _authed(request):
        _deny()
    try:
        limit = max(1, min(300, int(request.query.get("limit", "100"))))
    except ValueError:
        limit = 100
    return web.json_response({"lines": list(_logbuf)[-limit:]})


async def broadcast(request: web.Request) -> web.Response:
    if not _authed(request):
        _deny()
    ctx = _ctx(request)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "bad json"}, status=400)
    # шаг 2: отправка по одноразовому ключу
    ckey = str(body.get("confirm_key") or "")
    if ckey:
        item = _pending.pop(ckey, None)
        if item is None or time.monotonic() - item[0] > PENDING_TTL_S:
            return web.json_response({"ok": False, "error": "stale key, preview again"})
        _, text, scope, group = item
        users = _recipients(ctx, scope, group)
        bot = getattr(ctx, "bot", None)
        if bot is None:
            return web.json_response({"ok": False, "error": "bot unavailable"})
        sent, failed = 0, []
        for u in users:
            try:
                await bot.send_message(u.user_id, text)
                sent += 1
            except Exception as e:
                failed.append(u.user_id)
                log.warning("admin bcast to %s failed: %s", u.user_id, e)
            await asyncio.sleep(SEND_PAUSE_S)
        log.info("admin broadcast sent=%d failed=%d scope=%s", sent, len(failed), scope)
        return web.json_response({"ok": True, "sent": sent, "failed": failed})
    # шаг 1: превью
    text = str(body.get("text") or "").strip()
    scope = str(body.get("scope") or "all")
    group = str(body.get("group") or "").strip()
    if not text:
        return web.json_response({"ok": False, "error": "empty text"})
    if len(text) > TEXT_LIMIT:
        return web.json_response({"ok": False, "error": f"text > {TEXT_LIMIT}"})
    if scope not in ("all", "group"):
        return web.json_response({"ok": False, "error": "scope all|group"})
    if scope == "group" and not group:
        return web.json_response({"ok": False, "error": "group required"})
    users = _recipients(ctx, scope, group)
    key = secrets.token_urlsafe(16)
    _pending[key] = (time.monotonic(), text, scope, group)
    return web.json_response({"ok": True, "need_confirm": True, "key": key,
                              "recipients": len(users),
                              "preview": text[:200]})


def register_admin(app: web.Application) -> None:
    """Подключить /admin/* к приложению (только когда есть ctx)."""
    install_log_buffer()
    app.router.add_get("/admin/", index)
    app.router.add_post("/admin/login", login)
    app.router.add_post("/admin/logout", logout)
    app.router.add_get("/admin/api/stats", stats)
    app.router.add_get("/admin/api/logs", logs)
    app.router.add_post("/admin/api/broadcast", broadcast)
