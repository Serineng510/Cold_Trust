"""Telegram setup helper.

    python -m backend.telegram_setup chats   # list chats that have pressed Start on your bot
    python -m backend.telegram_setup test    # send a test alert to TELEGRAM_CHAT_IDS

Reads TELEGRAM_BOT_TOKEN from the environment or cold-chain/.env. Never prints the token.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from .config import load
from .notify import Notifier


def _api(token: str, method: str) -> dict:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/{method}", timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}") or {"ok": False, "description": f"HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"ok": False, "description": f"network error: {e.reason}"}
    except (TimeoutError, OSError) as e:  # slow links time out mid-read, outside URLError
        return {"ok": False, "description": f"network error: {e}"}


def chats(token: str) -> int:
    me = _api(token, "getMe")
    if not me.get("ok"):
        desc = me.get("description", "unknown error")
        if desc.startswith("network error"):
            print(f"Could not reach Telegram ({desc}). Check the internet connection and run it again.")
        else:
            print(f"Token rejected by Telegram ({desc}). Copy it again from @BotFather.")
        return 1
    print(f"Bot: @{me['result']['username']}")
    res = _api(token, "getUpdates")
    if not res.get("ok"):
        print(f"Could not read updates: {res.get('description')}")
        return 1
    found: dict[int, str] = {}
    for upd in res.get("result", []):
        msg = upd.get("message") or upd.get("edited_message") or upd.get("my_chat_member") or {}
        chat = msg.get("chat") or {}
        if "id" in chat:
            found[chat["id"]] = chat.get("title") or " ".join(
                x for x in (chat.get("first_name"), chat.get("last_name")) if x) or chat.get("username", "?")
    if not found:
        print("No chats yet. On each phone, open your bot in Telegram, press Start (or send any message), then run this again.")
        return 1
    for cid, name in found.items():
        print(f"  {cid:>14}  {name}")
    print("\nAdd this line to cold-chain/.env (keep only the chats you want alerted):")
    print("TELEGRAM_CHAT_IDS=" + ",".join(str(c) for c in found))
    return 0


def test(cfg) -> int:
    n = Notifier(cfg)
    if not n.chat_ids:
        print("TELEGRAM_CHAT_IDS is empty. Run `python -m backend.telegram_setup chats` first.")
        return 1
    sent = n.send("ColdTrust test alert: if you can read this, the driver/admin channel works.")
    print(f"Delivered to {sent} of {len(n.chat_ids)} chat(s).")
    return 0 if sent == len(n.chat_ids) else 1


def main(argv: list[str]) -> int:
    cfg = load()
    if not cfg.telegram_token:
        print("TELEGRAM_BOT_TOKEN is not set. Put it in cold-chain/.env (see .env.example).")
        return 1
    cmd = argv[0] if argv else "chats"
    if cmd == "chats":
        return chats(cfg.telegram_token)
    if cmd == "test":
        return test(cfg)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
