"""Bir elanın CV-sini hazırlayır və təsdiqlənmiş müraciəti göndərir.

Bu iş GitHub-un serverində gedir. Render-in pulsuz planında brauzer yaddaşa sığmır.
"""

import os

import profiles
import telegram_bot


def main():
    profiles.restore()
    message = {
        "chat": {"id": os.environ["CHAT_ID"]},
        "message_id": int(os.environ["MESSAGE_ID"]),
    }
    telegram_bot._approve(os.environ["JOB_ID"], message)


def _main():
    try:
        main()
    except (SystemExit, Exception) as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        if telegram_bot.report_failure("Göndəriş", detail):
            from pathlib import Path
            Path("failure-reported").write_text("1", encoding="utf-8")
        raise


if __name__ == "__main__":
    _main()
