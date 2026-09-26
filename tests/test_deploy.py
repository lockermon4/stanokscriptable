"""Deploy: health-сервер отвечает 200, PORT читается из окружения."""
import asyncio
import os
import urllib.request

from student_bot.health import build_app, health_port, start_health_server, stop_health_server


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_health_port_env_and_default():
    os.environ["PORT"] = "12345"
    try:
        assert health_port() == 12345
    finally:
        del os.environ["PORT"]
    assert health_port() == 10000
    os.environ["PORT"] = "junk"
    try:
        assert health_port() == 10000
    finally:
        del os.environ["PORT"]


def test_health_endpoint_200():
    def fetch(port, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read()

    async def go():
        loop = asyncio.get_running_loop()
        runner = await start_health_server(0)  # эфемерный порт
        try:
            port = runner.addresses[0][1]
            for path in ("/health", "/"):
                # блокирующий клиент — в executor, иначе deadlock с loop сервера
                status, body = await loop.run_in_executor(None, fetch, port, path)
                assert status == 200
                assert body == b'{"status": "ok"}'
        finally:
            await stop_health_server(runner)

    run(go())
    assert len(build_app().router.resources()) == 2


def _code_env_vars():
    import re
    src = ""
    for f in ("student_bot/config.py", "student_bot/health.py",
              "student_bot/bot.py", "student_bot/routing.py"):
        with open(f, encoding="utf-8") as fh:
            src += fh.read()
    return (set(re.findall(r'_get\("([A-Z_]+)"', src))
            | set(re.findall(r'environ\.get\("([A-Z_]+)"', src)))


def test_env_example_covers_all_code_vars():
    import re
    documented = set()
    with open(".env.example", encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^([A-Z_]+)=", line)
            if m:
                documented.add(m.group(1))
    code = _code_env_vars()
    assert code - documented == set(), f"missing in .env.example: {code - documented}"
    assert documented - code == set(), f"junk in .env.example: {documented - code}"


def test_render_yaml_valid_and_complete():
    import yaml
    d = yaml.safe_load(open("render.yaml", encoding="utf-8"))
    keys = {e["key"] for e in d["services"][0]["envVars"]}
    code = _code_env_vars() - {"PORT"}  # PORT выставляет сам Render
    assert code - keys == set(), f"missing in render.yaml: {code - keys}"
    assert d["services"][0]["startCommand"] == "python main.py"
    assert d["services"][0]["healthCheckPath"] == "/health"


def test_missing_env_vars_lists_all(monkeypatch):
    from student_bot.config import missing_env_vars
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("GIS_API_KEY", raising=False)
    missing, recommended = missing_env_vars()
    assert missing == ["BOT_TOKEN", "PUBLIC_BASE_URL"]  # всё сразу, не по одной
    assert recommended == ["GIS_API_KEY"]
    monkeypatch.setenv("BOT_TOKEN", "x")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://x")
    monkeypatch.setenv("GIS_API_KEY", "y")
    assert missing_env_vars() == ([], [])


def test_check_env_exits_with_full_list(monkeypatch, caplog):
    import logging
    import main as entry
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("GIS_API_KEY", "y")
    with caplog.at_level(logging.ERROR):
        try:
            entry.check_env()
        except SystemExit as e:
            assert "BOT_TOKEN" in str(e) and "PUBLIC_BASE_URL" in str(e)
        else:
            raise AssertionError("check_env must exit")
    assert any("BOT_TOKEN" in r.message and "PUBLIC_BASE_URL" in r.message
               for r in caplog.records if r.levelno >= logging.ERROR)
