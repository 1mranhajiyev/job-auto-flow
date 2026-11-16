"""Bir elanın CV-sini hazırlayır və təsdiqlənmiş müraciəti göndərir.

Bu iş GitHub-un serverində gedir. Render-in pulsuz planında brauzer yaddaşa sığmır.
"""

import os

import telegram_bot


def main():
    message = {
        "chat": {"id": os.environ["CHAT_ID"]},
        "message_id": int(os.environ["MESSAGE_ID"]),
    }
    telegram_bot._approve(os.environ["JOB_ID"], message)


if __name__ == "__main__":
    main()
