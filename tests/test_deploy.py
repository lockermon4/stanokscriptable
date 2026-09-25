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
