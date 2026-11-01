import os
print("FILES:", os.getcwd(), sorted(os.listdir(".")), flush=True)
import asyncio
import inspect
import logging
import os
import threading

from waitress import serve

logging.getLogger("httpx").setLevel(logging.WARNING)

from web import app
import bot
import marketing_bot


def run_web():
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    marketing_bot.start()
    result = bot.main()
    if inspect.iscoroutine(result):
        asyncio.run(result)
