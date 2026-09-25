"""Единая точка входа (Render Start Command: python main.py).

Запускает health-сервер (:$PORT/health) и aiogram long polling параллельно,
graceful shutdown на SIGTERM — см. student_bot.bot.main().
"""
import asyncio

from student_bot.bot import main

if __name__ == "__main__":
    asyncio.run(main())
