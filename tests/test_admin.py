"""Админка: 404 без токена/сессии, логин, рассылка в 2 шага, stats, logs."""
import asyncio
import http.cookiejar
import json
import logging
import urllib.request
import urllib.error
from types import SimpleNamespace

from student_bot import admin
from student_bot.health import start_health_server, stop_health_server

TOKEN = "test-admin-token-0123456789abcdef"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text):
        if uid == 999:
            raise RuntimeError("blocked")
        self.sent.append((uid, text))


def _user(uid, group="G1"):
    return SimpleNamespace(user_id=uid, group=group, home_lat=55.0, home_lon=37.0,
                           lang="ru")


class FakeStore:
    def __init__(self, users):
        self._users = users

    def all_users(self):
        return self._users


def _ctx(token=TOKEN, users=(1, 2, 999)):
    admin._sessions.clear()
    admin._pending.clear()
    bot = FakeBot()
    ctx = SimpleNamespace(settings=SimpleNamespace(admin_token=token),
                          store=FakeStore([_user(u, "G1" if u != 2 else "G2") for u in users]),
                          bot=bot)
    return ctx, bot


class Client:
    def __init__(self, port):
        self.port = port
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))

    def _call(self, path, data=None):
        url = f"http://127.0.0.1:{self.port}{path}"
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(url, data=body,
                                     headers={"Content-Type": "application/json"})
        try:
            with self.opener.open(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()

    def get(self, path):
        return self._call(path)

    def post(self, path, data):
        return self._call(path, data)

    def login(self, token):
        url = f"http://127.0.0.1:{self.port}/admin/login"
        body = f"token={token}".encode()
        req = urllib.request.Request(url, data=body)
        try:
            with self.opener.open(req, timeout=5) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()


def _serve(ctx):
    # поднимаем сервер, выполняем функцию в executor, гасим
    async def run_with(fn):
        runner = await start_health_server(0, ctx)
        try:
            loop = asyncio.get_running_loop()
            port = runner.addresses[0][1]
            return await loop.run_in_executor(None, fn, port)
        finally:
            await stop_health_server(runner)
    return run_with


def _run_case(ctx, fn):
    return run(_serve(ctx)(fn))


def test_no_token_configured_everything_404():
    ctx, _ = _ctx(token="")
    def fn(port):
        c = Client(port)
        assert c.get("/admin/")[0] == 404
        assert c.login("anything")[0] == 404
        assert c.get("/admin/api/stats")[0] == 404
        assert c.post("/admin/api/broadcast", {"text": "x"})[0] == 404
    _run_case(ctx, fn)


def test_wrong_token_and_no_session_404():
    ctx, _ = _ctx()
    def fn(port):
        c = Client(port)
        assert c.login("wrong-token")[0] == 404  # не 401: не палим наличие панели
        assert c.get("/admin/api/stats")[0] == 404
        assert c.post("/admin/api/broadcast", {"text": "x"})[0] == 404
        assert "ADMIN_TOKEN" in c.get("/admin/")[1]  # форма логина видна
    _run_case(ctx, fn)


def test_login_dashboard_stats_logs():
    ctx, _ = _ctx()
    def fn(port):
        c = Client(port)
        status, body = c.login(TOKEN)
        assert status == 200 and "Рассылка" in body  # 303 -> дашборд по куке
        status, body = c.get("/admin/api/stats")
        s = json.loads(body)
        assert status == 200 and s["users"] == 3 and s["groups"] == {"G1": 2, "G2": 1}
        logging.getLogger("bot.test").warning("probe-line-123")
        status, body = c.get("/admin/api/logs?limit=50")
        assert status == 200 and "probe-line-123" in body
    _run_case(ctx, fn)


def test_broadcast_two_steps_no_double_send():
    ctx, bot = _ctx()
    def fn(port):
        c = Client(port)
        c.login(TOKEN)
        status, body = c.post("/admin/api/broadcast", {"text": "Всем привет", "scope": "all"})
        p = json.loads(body)
        assert status == 200 and p["need_confirm"] is True and p["recipients"] == 3
        assert bot.sent == []  # превью ничего не шлёт
        status, body = c.post("/admin/api/broadcast", {"confirm_key": p["key"]})
        r = json.loads(body)
        assert r["ok"] is True and r["sent"] == 2 and r["failed"] == [999]
        assert [t for _, t in bot.sent] == ["Всем привет"] * 2
        # повтор тем же ключом — ключ одноразовый
        status, body = c.post("/admin/api/broadcast", {"confirm_key": p["key"]})
        assert "stale" in body and len(bot.sent) == 2
    _run_case(ctx, fn)


def test_broadcast_group_scope_and_validation():
    ctx, bot = _ctx()
    def fn(port):
        c = Client(port)
        c.login(TOKEN)
        _, body = c.post("/admin/api/broadcast",
                         {"text": "Группе", "scope": "group", "group": "G2"})
        p = json.loads(body)
        assert p["recipients"] == 1
        _, body = c.post("/admin/api/broadcast", {"confirm_key": p["key"]})
        assert json.loads(body)["sent"] == 1 and bot.sent[0][0] == 2
        _, body = c.post("/admin/api/broadcast", {"text": "", "scope": "all"})
        assert "empty" in body
        _, body = c.post("/admin/api/broadcast", {"text": "x", "scope": "group"})
        assert "group required" in body
    _run_case(ctx, fn)
