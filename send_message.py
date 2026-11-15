#!/usr/bin/env python3
"""Tiny Telegram send-message helper. Usage: python3 send_message.py <chat_id> <text>"""
import json, sys, urllib.request
from pathlib import Path

def load_token():
    for line in Path(__file__).parent.joinpath(".env").read_text().splitlines():
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("token not found in .env")

def send(chat_id: int, text: str):
    token = load_token()
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        result = json.loads(r.read())
    if not result.get("ok"):
        raise SystemExit(f"Telegram error: {result}")
    print("sent OK -> message_id", result["result"]["message_id"])

if __name__ == "__main__":
    send(int(sys.argv[1]), sys.argv[2])
