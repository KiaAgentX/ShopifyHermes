#!/usr/bin/env python3
"""bot.py — Hermes Shop Telegram bot v1.1 (onboarding UI + failover brain).

New in v1.1:
- /start flow: welcome (new user) / welcome-back (returning) + inline menu cards
- Combined navigation: inline keyboards + persistent "🏠 منو" reply keyboard
- AI answers carry a [منو] button + 2 suggested-question buttons (from the LLM)
- Anti double-reply: backlog drained on start (offset=-1) + offset persisted
- Anti stars: LLM markdown -> safe HTML (escaped) with plain-text fallback
Commands: /start /menu /help /about /reset
"""
import html as _html
import json, re, threading, time, urllib.error, urllib.request
from pathlib import Path
from brain import Brain, load_env

ENV = load_env()
TOKEN = ENV["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
HERE = Path(__file__).parent
STATE_PATH = HERE / "data" / "state.json"
VERSION = "1.1.0"

SYSTEM = (
    "تو «هرمس» هستی؛ فروشنده‌ی دوست‌داشتنی و پرانرژی فروشگاه محصولات دیجیتال Hermes Shop. "
    "محصولات فروشگاه: کتاب HTML، اسکیل MD، پرامپت JSON و پک کد. "
    "کاتالوگ هنوز در حال ساخته است — اگر پرسیدند چه می‌فروشی، بگو کاتالوگ خیلی زود می‌آید "
    "و فعلاً مشاوره و راهنمایی رایگان می‌دهی. "
    "به زبان خود کاربر جواب بده. جواب‌ها کوتاه (حداکثر ۷ خط)، گرم و خودمونی؛ "
    "در هر پاراگراف حداکثر یک ایموجی. "
    "از markdown استفاده نکن: نه **، نه __، نه #، نه جدول؛ فقط متن ساده. "
    "در پایان هر جواب، دقیقاً یک خط اضافه کن:\n"
    "SUGGEST: یک سوال کوتاه مرتبط | یک سوال کوتاه مرتبط دیگر"
)

FALLBACK_SUGS = ["کاتالوگ چه چیزهایی خواهد داشت؟", "برای شروع چه پیشنهادی داری؟"]

# ---------------------------------------------------------------- state file
ST = {"offset": 0, "seen": []}
STATE_LOCK = threading.Lock()


def load_state():
    global ST
    try:
        data = json.loads(STATE_PATH.read_text())
        if isinstance(data, dict):
            ST.update({k: data[k] for k in ("offset", "seen") if k in data})
    except Exception:
        pass


def save_state():
    with STATE_LOCK:
        try:
            STATE_PATH.parent.mkdir(exist_ok=True)
            tmp = STATE_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(ST, ensure_ascii=False))
            tmp.replace(STATE_PATH)
        except Exception as e:
            print(f"[state] {type(e).__name__}: {e}", flush=True)


def mark_seen(chat_id):
    if chat_id not in ST["seen"]:
        ST["seen"].append(chat_id)
        ST["seen"] = ST["seen"][-500:]  # cap
        save_state()


# ------------------------------------------------------------- markdown -> HTML
def md_to_html(t):
    t = _html.escape(t)
    t = re.sub(r"```[a-zA-Z0-9]*\n?", "", t)                      # code fences
    t = re.sub(r"(?m)^\s*([-*_]\s*){3,}$", "———", t)               # --- rules
    t = re.sub(r"(?m)^(\s*)[-•]\s+", r"\1• ", t)                   # - bullets
    t = re.sub(r"(?m)^(\s*)\*\s+", r"\1• ", t)                     # * bullets
    t = re.sub(r"\*\*([^*]+?)\*\*", r"<b>\1</b>", t)               # **bold**
    t = re.sub(r"__([^_]+?)__", r"<b>\1</b>", t)                   # __bold__
    t = re.sub(r"(?<![*\w])\*([^*\n]+?)\*(?![*\w])", r"<i>\1</i>", t)  # *italic*
    t = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", t)             # `code`
    t = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"<b>\1</b>", t)            # # heading
    return t


def extract_sugs(raw):
    """Strip the trailing SUGGEST line; return (plain_text, [sug1, sug2])."""
    lines = raw.splitlines()
    sugs, idx = [], None
    n = len(lines)
    for j in range(n - 1, max(n - 3, -1), -1):   # only last two lines count
        m = re.match(r"(?i)^\s*(?:SUGGEST|پیشنهاد)\s*[:：]\s*(.+?)\s*$", lines[j] or "")
        if m:
            idx = j
            sugs = [p.strip()[:28] for p in m.group(1).split("|") if p.strip()][:2]
            break
    if idx is not None:
        lines = lines[:idx]
    plain = "\n".join(lines).strip()
    if not sugs:
        sugs = list(FALLBACK_SUGS)
    return plain, sugs


# ---------------------------------------------------------------- telegram
def tg(method, payload=None, timeout=35):
    url = f"{API}/{method}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def send_text(chat_id, text, kb=False, inline=None):
    """HTML parse with plain-text fallback. kb installs the persistent reply
    keyboard; inline attaches an inline keyboard."""
    payload = {"chat_id": chat_id, "text": text}
    markup = inline if inline is not None else (REPLY_KB if kb else None)
    if markup is not None:
        payload["reply_markup"] = markup
    try:
        payload["parse_mode"] = "HTML"
        tg("sendMessage", payload)
    except urllib.error.HTTPError:
        payload.pop("parse_mode", None)
        payload["text"] = _html.unescape(re.sub(r"<[^>]+>", "", text))
        try:
            tg("sendMessage", payload)
        except Exception as e:
            print(f"[send] chat={chat_id}: {e}", flush=True)
    except Exception as e:
        print(f"[send] chat={chat_id}: {e}", flush=True)


def answer_cb(cb_id, text=None):
    try:
        p = {"callback_query_id": cb_id}
        if text:
            p["text"] = text
        tg("answerCallbackQuery", p)
    except Exception as e:
        print(f"[cb-ack] {type(e).__name__}: {e}", flush=True)


# ---------------------------------------------------------------- keyboards
REPLY_KB = {
    "keyboard": [[{"text": "🏠 منو"}]],
    "resize_keyboard": True, "is_persistent": True,
    "input_field_placeholder": "سوالت را بنویس…",
}

INLINE_MENU = {"inline_keyboard": [
    [{"text": "💬 سوال بپرس", "callback_data": "menu:ask"},
     {"text": "📚 کاتالوگ", "callback_data": "menu:catalog"}],
    [{"text": "✨ درباره", "callback_data": "menu:about"},
     {"text": "🆘 راهنما", "callback_data": "menu:help"}],
]}
INLINE_HOME = {"inline_keyboard": [[{"text": "🏠 منوی اصلی", "callback_data": "menu:main"}]]}
INLINE_WB = {"inline_keyboard": [[
    {"text": "💬 ادامه گفتگو", "callback_data": "wb:continue"},
    {"text": "🏠 منوی اصلی", "callback_data": "menu:main"}]]}

# ---------------------------------------------------------------- card texts
MENU_TXT = "خب، از کجا شروع کنیم؟ 😊"
ASK_TXT = "آماده‌ام! 🎤 هر چی توی ذهنته بپرس — مشاوره، معرفی، هرچی. فقط بنویس و بفرست 🚀"
CATALOG_TXT = (
    "🛒 کاتالوگ Hermes Shop\n\n"
    "کاتالوگ دارد ساخته می‌شود 🚧🔨\n\n"
    "اولین موج محصولات:\n"
    "📕 کتاب‌های HTML — راهنماهای کاربردی\n"
    "🧩 اسکیل‌های MD — مهارت‌های آماده برای ایجنت‌ها\n"
    "🪄 پرامپت‌های JSON — جعبه‌ابزار هوش مصنوعی\n\n"
    "تا افتتاح من همه‌جا هستم؛ بپرس و قدم بزن 😄"
)
ABOUT_TXT = (
    "✨ درباره Hermes Shop\n\n"
    "Hermes Shop فروشگاه محصولات دیجیتال است؛ ساخته‌شده با عشق و کمی جادوی هوش مصنوعی 🪄\n"
    "من هرمس هستم؛ دستیار فروش اینجا، تا راهنماییت کنم 🤝\n\n"
    f"نسخه {VERSION} 🚀 · روی Railway 🚂"
)
HELP_TXT = (
    "🆘 راهنما\n\n"
    "💬 هر سوال داری همان تایپ کن — جواب می‌دهم\n"
    "🧭 /menu باز کردن منو\n"
    "🧹 /reset پاک‌کردن حافظه‌ی گفتگوی این چت\n"
    "🎬 /start شروع دوباره\n\n"
    "📌 دکمه «🏠 منو» پایین صفحه همیشه کنارته"
)


def welcome_new(name):
    return (
        f"سلام {_html.escape(name)}! 👋✨\n\n"
        "من هرمس هستم؛ فروشنده‌ی فروشگاه Hermes Shop 🛍️\n"
        "اینجا به‌زودی خونه‌ی محصولات دیجیتال می‌شود:\n"
        "📕 کتاب HTML · 🧩 اسکیل MD · 🪄 پرامپت JSON · 💻 پک کد\n\n"
        "تا باز شدن کاتالوگ من همین‌جام؛ بپرس یا از دکمه‌ها استفاده کن 👇"
    )


def welcome_back(name):
    return f"خوش برگشتی {_html.escape(name)}! 👋😄\nاز همان‌جا ادامه بدهیم؟"


# ---------------------------------------------------------------- handlers
MEMORY = {}          # chat_id -> [{role, content}]
SUGS = {}            # chat_id -> [q1, q2]
BRAIN = None


def show_menu(chat_id):
    send_text(chat_id, MENU_TXT, inline=INLINE_MENU)


def answer_user(chat_id, text, kb=False):
    """User text (typed or via suggestion button) -> AI answer with buttons."""
    tg("sendChatAction", {"chat_id": chat_id, "action": "typing"})
    hist = MEMORY.setdefault(chat_id, [])
    hist.append({"role": "user", "content": text})
    try:
        messages = [{"role": "system", "content": SYSTEM}] + hist[-8:]
        answer, meta = BRAIN.ask_messages(messages)
    except Exception as e:
        print(f"[err] chat={chat_id}: {e}", flush=True)
        send_text(chat_id, "اوپس، الان همه‌ی مغزهایم خسته‌اند 😅 یه بار دیگر امتحان کن.", kb=kb)
        return
    plain, sugs = extract_sugs(answer)
    hist.append({"role": "assistant", "content": plain})
    del hist[:-10]
    SUGS[chat_id] = sugs
    print(f"[ans] chat={chat_id} prov={meta['provider']} model={meta['model']} "
          f"{meta['latency_s']}s sugs={len(sugs)}", flush=True)
    if kb:   # first contact: install the persistent keyboard this once
        send_text(chat_id, md_to_html(plain), kb=True)
    else:
        rows = [[{"text": "🏠 منو", "callback_data": "menu:main"}]]
        if sugs:
            rows.append([{"text": s, "callback_data": f"sug:{i}"} for i, s in enumerate(sugs)])
        send_text(chat_id, md_to_html(plain), inline={"inline_keyboard": rows})


def handle_message(msg):
    text = msg.get("text")
    if not text or msg.get("from", {}).get("is_bot"):
        return
    chat_id = msg["chat"]["id"]
    name = msg.get("from", {}).get("first_name", "friend")
    first = chat_id not in ST["seen"]
    if first:
        mark_seen(chat_id)
    print(f"[msg] chat={chat_id} from={name}: {text[:60]}", flush=True)

    cmd = text.split()[0].split("@")[0] if text.startswith("/") else None

    if cmd == "/start":
        if first:
            send_text(chat_id, welcome_new(name), kb=True)   # installs keyboard
            show_menu(chat_id)
        else:
            send_text(chat_id, welcome_back(name), inline=INLINE_WB)
        return
    if cmd == "/menu" or text == "🏠 منو":
        show_menu(chat_id)
        return
    if cmd == "/help":
        send_text(chat_id, HELP_TXT, inline=INLINE_HOME)
        return
    if cmd == "/about":
        send_text(chat_id, ABOUT_TXT, inline=INLINE_HOME)
        return
    if cmd == "/reset":
        MEMORY.pop(chat_id, None)
        SUGS.pop(chat_id, None)
        send_text(chat_id, "حافظه‌ی گفتگوی این چت پاک شد 🧹 از نو شروع می‌کنیم!", inline=INLINE_HOME)
        return

    answer_user(chat_id, text, kb=first)


def handle_cb(cb):
    chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
    answer_cb(cb["id"])
    if chat_id is None:
        return
    mark_seen(chat_id)
    data = cb.get("data", "")
    print(f"[cb] chat={chat_id} data={data}", flush=True)

    if data == "menu:main":
        show_menu(chat_id)
    elif data == "menu:ask":
        send_text(chat_id, ASK_TXT, inline=INLINE_HOME)
    elif data == "menu:catalog":
        send_text(chat_id, CATALOG_TXT, inline=INLINE_HOME)
    elif data == "menu:about":
        send_text(chat_id, ABOUT_TXT, inline=INLINE_HOME)
    elif data == "menu:help":
        send_text(chat_id, HELP_TXT, inline=INLINE_HOME)
    elif data == "wb:continue":
        send_text(chat_id, "باشه! پس بگو چی توی ذهنته 😊", inline=INLINE_HOME)
    elif data.startswith("sug:"):
        try:
            q = SUGS.get(chat_id, [])[int(data.split(":", 1)[1])]
        except Exception:
            q = None
        if q:
            answer_user(chat_id, q)


def dispatch(update):
    if "callback_query" in update:
        handle_cb(update["callback_query"])
    elif "message" in update:
        handle_message(update["message"])


def install_commands():
    cmds = [
        {"command": "start", "description": "شروع / خوش‌آمد 🎬"},
        {"command": "menu", "description": "منوی اصلی 🧭"},
        {"command": "help", "description": "راهنما 🆘"},
        {"command": "about", "description": "درباره هرمس ✨"},
        {"command": "reset", "description": "پاک‌کردن حافظه گفتگو 🧹"},
    ]
    try:
        tg("setMyCommands", {"commands": cmds})
    except Exception as e:
        print(f"[cmds] {type(e).__name__}: {e}", flush=True)


def drain_backlog():
    """Confirm every pending update without processing it (anti double-reply)."""
    try:
        res = tg("getUpdates", {"offset": -1, "timeout": 0}, timeout=15)
        rs = res.get("result", [])
        if rs:
            return rs[-1]["update_id"] + 1
    except Exception as e:
        print(f"[drain] {type(e).__name__}: {e}", flush=True)
    return ST.get("offset", 0)


def main():
    global BRAIN
    BRAIN = Brain()
    load_state()
    offset = drain_backlog()
    install_commands()
    print(f"Hermes Shop v{VERSION} polling... backlog drained to offset={offset} "
          f"(pool={len(BRAIN.pool)})", flush=True)
    while True:
        try:
            res = tg("getUpdates", {"offset": offset, "timeout": 25})
            for u in res.get("result", []):
                uid = u["update_id"]
                if uid < offset:
                    continue
                offset = uid + 1
                ST["offset"] = offset
                save_state()
                threading.Thread(target=dispatch, args=(u,), daemon=True).start()
        except Exception as e:
            print(f"[loop] {type(e).__name__}: {e}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()
