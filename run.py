import asyncio
import inspect
import os
import threading

from waitress import serve

from web import app
import bot


def run_web():
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    # Admin site runs in the background; the bot runs in the main thread (unchanged).
    threading.Thread(target=run_web, daemon=True).start()
    result = bot.main()
    if inspect.iscoroutine(result):
        asyncio.run(result)
