#!/usr/bin/env python3
"""bot.py — Hermes Shop Telegram bot (polling loop + LLM brain with failover).
Run: nohup python3 bot.py > bot.log 2>&1 &
"""
import json, threading, time, urllib.request
from brain import Brain, load_env

ENV = load_env()

TOKEN = ENV["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"

SYSTEM = (
    "You are Hermes Shop, the friendly sales assistant of a digital-products store "
    "(HTML books, MD skills, JSON prompts, code packs). Answer in the user's language, "
    "short and warm. The product catalog is being built right now — if asked what you sell, "
    "say the catalog is coming very soon and you can already chat and help."
)


def tg(method, payload=None, timeout=35):
    url = f"{API}/{method}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def handle(update, memory, brain):
    msg = update.get("message") or {}
    text = msg.get("text")
    if not text or msg.get("from", {}).get("is_bot"):
        return
    chat_id = msg["chat"]["id"]
    name = msg.get("from", {}).get("first_name", "friend")
    print(f"[msg] chat={chat_id} from={name}: {text[:60]}", flush=True)

    if text.startswith("/start"):
        tg("sendMessage", {"chat_id": chat_id, "text":
            f"سلام {name} 👋 من **Hermes Shop** هستم!\n\n"
            "فروشنده‌ی فروشگاه محصولات دیجیتال هستم — کتاب HTML، اسکیل MD، پرامپت JSON و بیشتر.\n"
            "کاتالوگ داره آماده می‌شه، ولی از همین حالا می‌تونی باهام حرف بزنی 🛍️"})
        return

    tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    hist = memory.setdefault(chat_id, [])
    hist.append({"role": "user", "content": text})
    try:
        messages = [{"role": "system", "content": SYSTEM}] + hist[-8:]
        answer, meta = brain.ask_messages(messages)
        hist.append({"role": "assistant", "content": answer})
        del hist[:-10]
        print(f"[ans] chat={chat_id} model={meta['model']} {meta['latency_s']}s", flush=True)
    except Exception as e:
        answer = "اوپس، الان همه‌ی مغزهایم خسته‌اند 😅 یه بار دیگر امتحان کن."
        print(f"[err] chat={chat_id}: {e}", flush=True)
    tg("sendMessage", {"chat_id": chat_id, "text": answer})


def main():
    brain = Brain()
    memory = {}
    offset = 0
    print("Hermes Shop bot is polling... (Ctrl+C to stop)", flush=True)
    while True:
        try:
            res = tg("getUpdates", {"offset": offset, "timeout": 25})
            for u in res.get("result", []):
                offset = u["update_id"] + 1
                threading.Thread(target=handle, args=(u, memory, brain), daemon=True).start()
        except Exception as e:
            print(f"[loop] {type(e).__name__}: {e}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
