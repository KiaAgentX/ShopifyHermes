#!/usr/bin/env python3
"""bot.py — Hermes Shop Telegram bot v1.2 (quality & UX overhaul).

New in v1.2 (10-issue fix pack):
- Quality gate in brain + Coarena first (fastest pool) -> no more garbled text
- Fixed FAQ: suggestion buttons are curated, short, grammatical — and answer
  INSTANTLY (no LLM roundtrip, no truncation, no LLM-made labels)
- Single source of truth for products: products.json (facts injected into the
  system prompt -> no hallucinated products/prices)
- No more stacked "منو" row under every answer (persistent 🏠 keyboard covers it)
- Typing indicator loops while the LLM thinks (not just one 5s shot)
- Structured answers: direct first line + up to 3 bullets (system prompt rule)
- RTL-safe rule: Latin words mid-sentence, sentences never end on a Latin token
- Locked persona: the bot is «هرمس» and invents no other names
- Catalog card built from products.json + «🔔 خبرم کن» waitlist button
  (saved to data/waitlist.json, deduped)

v1.1 (still true):
- /start flow: welcome / welcome-back + inline menu cards
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
WAITLIST_PATH = HERE / "data" / "waitlist.json"
PRODUCTS_PATH = HERE / "products.json"
VERSION = "1.2.0"

# ------------------------------------------------------------------ products
def load_products():
    """products.json is the single source of truth for store facts."""
    try:
        d = json.loads(PRODUCTS_PATH.read_text())
        if isinstance(d, dict) and d.get("categories"):
            return d
    except Exception as e:
        print(f"[products] {type(e).__name__}: {e}", flush=True)
    return {"status": "coming_soon", "categories": [], "launch_note": "کاتالوگ به‌زودی افتتاح می‌شود."}

PRODUCTS = load_products()
CAT_NAMES = " · ".join(f"{c['icon']} {c['name']}" for c in PRODUCTS["categories"])
CAT_FACTS = "\n".join(f"- {c['icon']} {c['name']}: {c['desc']}" for c in PRODUCTS["categories"])

SYSTEM = (
    "تو «هرمس» هستی؛ فروشنده‌ی دوست‌داشتنی فروشگاه محصولات دیجیتال «Hermes Shop». "
    "این تنها هویت توست — هیچ اسم، لقب یا شخصیت دیگری از خودت نساز و مطمئن نباشی، ننویس.\n\n"
    "فکت‌های فروشگاه (فقط همین‌ها؛ هیچ محصول، عدد، قیمت یا تاریخ دیگری از خودت اختراع نکن):\n"
    f"{CAT_FACTS}\n"
    "- وضعیت کاتالوگ: به‌زودی افتتاح می‌شود؛ قیمت‌ها با افتتاح اعلام می‌شوند.\n"
    "- در کارت کاتالوگ دکمه‌ی «خبرم کن» هست تا علاقه‌مندان را به‌محض افتتاح خبر دهیم.\n\n"
    "قواعد نوشتن:\n"
    "- فارسیِ روان و کاملاً درست بنویس؛ هیچ کلمه‌ی ناقص، قاطی یا بی‌معنی نساز.\n"
    "- واژه‌ی انگلیسی (مثل HTML یا JSON) را وسطِ جمله بیاور و جمله را با نقطه یا "
    "علامتِ فارسی تمام کن؛ هیچ‌گاه جمله با واژه‌ی انگلیسی تمام نشود.\n"
    "- ساختار جواب: خط اول، جوابِ کوتاه و مستقیم؛ بعد حداکثر ۳ بولت با «•»؛ "
    "جمعاً حداکثر ۶ خط.\n"
    "- لحن گرم و خودمونی؛ در کل جواب حداکثر ۳ ایموجی.\n"
    "- از markdown استفاده نکن: نه **، نه __، نه #، نه جدول؛ فقط متن ساده."
)

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


def add_waitlist(chat_id):
    """Register interest in the launch. Returns True if already registered,
    False on fresh add, None on storage failure."""
    try:
        data = {"chat_ids": []}
        if WAITLIST_PATH.exists():
            data = json.loads(WAITLIST_PATH.read_text())
        ids = data.get("chat_ids", [])
        if chat_id in ids:
            return True
        ids.append(chat_id)
        data = {"chat_ids": ids[-5000:], "updated": int(time.time())}
        WAITLIST_PATH.parent.mkdir(exist_ok=True)
        tmp = WAITLIST_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.replace(WAITLIST_PATH)
        return False
    except Exception as e:
        print(f"[waitlist] {type(e).__name__}: {e}", flush=True)
        return None


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


def typing_until(chat_id, stop):
    """Keep the 'typing…' indicator alive for the whole LLM call."""
    while not stop.is_set():
        try:
            tg("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=8)
        except Exception:
            pass
        stop.wait(4.0)


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
INLINE_CATALOG = {"inline_keyboard": [
    [{"text": "🔔 خبرم کن", "callback_data": "notify:catalog"}],
    [{"text": "🏠 منوی اصلی", "callback_data": "menu:main"}]],
}

# ---------------------------------------------------------------- FAQ (instant answers)
# Curated, short, grammatical labels — no LLM-generated button text anymore.
# Answers are structured (**bold** headline + bullets) and go through md_to_html.
FAQ = {
    "when": {
        "label": "کاتالوگ کی میاد؟",
        "answer": ("**کاتالوگ به‌زودی افتتاح می‌شه! 🎉**\n\n"
                   "• الان در حال آماده‌سازی نهایی‌ایم 🚧\n"
                   f"• اولین موج: {CAT_NAMES}\n"
                   "• قیمت‌ها هم با افتتاح اعلام می‌شن\n\n"
                   "می‌خوای اول از همه خبرت کنم؟ دکمه‌ی «🔔 خبرم کن» رو بزن 👇"),
        "sugs": ["what", "price", "start"],
        "notify": True,
    },
    "what": {
        "label": "چه محصولاتی داریم؟",
        "answer": ("**اولین موج کاتالوگ — ۴ دسته محصول دیجیتال 📦**\n\n"
                   + "\n".join(f"{c['icon']} **{c['name']}** — {c['desc']}" for c in PRODUCTS["categories"])
                   + "\n\nیکی از این‌ها رو می‌خوای برات باز کنم؟"),
        "sugs": ["html", "md", "when"],
    },
    "html": {
        "label": "کتاب HTML چیه؟",
        "answer": ("**کتاب HTML — راهنمای ساخت وب از صفر 📕**\n\n"
                   "• ساخت صفحه‌های وب رو گام‌به‌گام و کاربردی آموزش می‌ده\n"
                   "• مخصوص مبتدی‌هاست؛ هیچ پیش‌زمینه‌ای لازم نیست\n"
                   "• با تموم شدنش می‌تونی سایتت رو خودت بسازی و مدیریت کنی"),
        "sugs": ["prereq", "learn", "when"],
    },
    "md": {
        "label": "اسکیل MD چیه؟",
        "answer": ("**اسکیل MD — مهارت آماده برای ایجنت‌ها 🧩**\n\n"
                   "• قالب‌ها و مهارت‌های آماده برای ایجنت‌های هوش مصنوعیه\n"
                   "• به‌جای ساختن از صفر، یه پایه‌ی تمیز و تست‌شده می‌گیری\n"
                   "• برای اتوماسیون و کارهای تکراری عالیه"),
        "sugs": ["what", "prompt", "when"],
    },
    "prompt": {
        "label": "پرامپت JSON چیه؟",
        "answer": ("**پرامپت JSON — جعبه‌ابزار هوش مصنوعی 🪄**\n\n"
                   "• یه پکیج از پرامپت‌های ساختاریافته و آماده‌ست\n"
                   "• برای چت‌بات، مشاور و کارهای AI روزمره به کار میاد\n"
                   "• کافیه جاهای خالی رو پر کنی و استفاده کنی"),
        "sugs": ["what", "md", "when"],
    },
    "code": {
        "label": "پک کد چیه؟",
        "answer": ("**پک کد — کدهای آماده‌ی کاربردی 💻**\n\n"
                   "• اسکریپت‌های تمیز برای پروژه‌های واقعیه\n"
                   "• هر پک با راهنمای نصب و استفاده میاد\n"
                   "• وقتت رو برای ساختن از صفر هدر نمی‌ده"),
        "sugs": ["what", "start", "when"],
    },
    "prereq": {
        "label": "پیش‌نیاز لازمه؟",
        "answer": ("**نه، اصلاً! ✌️**\n\n"
                   "• کتاب HTML از صفرِ صفر شروع می‌کنه\n"
                   "• فقط یه کامپیوتر یا گوشی و یه‌کم کنجکاوی کافیه\n"
                   "• هیچ زبان برنامه‌نویسی قبلی لازم نیست"),
        "sugs": ["learn", "html", "when"],
    },
    "learn": {
        "label": "چقدر طول می‌کشه؟",
        "answer": ("**بستگی به خودت داره، ولی سریعه! ⏱️**\n\n"
                   "• با روزی یک ساعت، توی چند هفته مباحث اصلی رو می‌گیری\n"
                   "• فصل‌ها کوتاه و کاربردی‌ان؛ هر فصل یه چیز واقعی می‌سازه\n"
                   "• اولویت با تمرین عملیه، نه حفظ کردن"),
        "sugs": ["html", "prereq", "when"],
    },
    "price": {
        "label": "قیمت‌ها چطوره؟",
        "answer": ("**قیمت‌ها با افتتاح کاتالوگ اعلام می‌شن! 💰**\n\n"
                   "• هنوز قیمت نهایی بسته نشده و نمی‌خوام الکی عدد بگم\n"
                   "• به‌محض اعلام، همین‌جا اول از همه بهت می‌گم\n"
                   "• دکمه‌ی «🔔 خبرم کن» رو بزن تا جا نمونی"),
        "sugs": ["when", "what", "start"],
        "notify": True,
    },
    "start": {
        "label": "از کجا شروع کنم؟",
        "answer": ("**ساده‌ترین راه: همین بات! 🚀**\n\n"
                   "• هر سوال داری همون‌جا تایپ کن؛ من راهنماییت می‌کنم\n"
                   "• از منو، «📚 کاتالوگ» رو بزن تا اولین موج محصول رو ببینی\n"
                   "• با دکمه‌ی «🔔 خبرم کن» توی لیست افتتاح ثبت‌نام کن"),
        "sugs": ["when", "what", "html"],
    },
    "guide": {
        "label": "راهنماییم می‌کنی؟",
        "answer": ("**البته! این کارِ منه 🤝**\n\n"
                   "• سوالت رو تایپ کن؛ مشاوره‌ی رایگان می‌دم\n"
                   "• برای محصولات، از کارت «📚 کاتالوگ» شروع کن\n"
                   "• هر جای راه گم شدی، /menu خونه‌ی منه"),
        "sugs": ["start", "what", "when"],
    },
}
DEFAULT_SUGS = ["when", "what"]


def pick_sugs(question):
    """Contextual suggestion keys by keyword — deterministic, always valid."""
    q = (question or "").lower()
    if "html" in q:
        return ["html", "prereq", "learn"]
    if "کتاب" in q:
        return ["html", "prereq", "when"]
    if "اسکیل" in q or " md" in q or q.endswith("md"):
        return ["md", "what", "when"]
    if "پرامپت" in q or "json" in q:
        return ["prompt", "what", "when"]
    if "اسکریپت" in q or "پک کد" in q or "کدنویسی" in q:
        return ["code", "what", "when"]
    if "قیمت" in q or "تومان" in q or "پول" in q:
        return ["price", "when", "what"]
    if "شروع" in q or "از کجا" in q:
        return ["start", "what", "when"]
    if "راهنما" in q or "کمک" in q:
        return ["guide", "start", "what"]
    if "کاتالوگ" in q or "محصول" in q or "میاد" in q or "فروشگاه" in q or "می‌فروش" in q:
        return ["when", "what", "price"]
    return list(DEFAULT_SUGS)


def sug_rows(keys):
    """Curated suggestion buttons: 2 per row, max 3 — short labels, no truncation."""
    ks = [k for k in keys if k in FAQ][:3]
    rows = []
    for i in range(0, len(ks), 2):
        rows.append([{"text": FAQ[k]["label"], "callback_data": f"faq:{k}"}
                     for k in ks[i:i + 2]])
    return rows


def send_faq(chat_id, key, remember=True):
    """Instant canned answer (no LLM) + next suggestions + optional notify btn."""
    item = FAQ.get(key)
    if not item:
        return
    rows = sug_rows(item.get("sugs", DEFAULT_SUGS))
    if item.get("notify"):
        rows.append([{"text": "🔔 خبرم کن", "callback_data": "notify:catalog"}])
    send_text(chat_id, md_to_html(item["answer"]), inline={"inline_keyboard": rows})
    if remember:
        hist = MEMORY.setdefault(chat_id, [])
        hist.append({"role": "user", "content": item["label"]})
        hist.append({"role": "assistant", "content": item["answer"].replace("**", "")})
        del hist[:-10]


# ---------------------------------------------------------------- card texts
MENU_TXT = "خب، از کجا شروع کنیم؟ 😊"
ASK_TXT = "آماده‌ام! 🎤 هر چی توی ذهنته بپرس — مشاوره، معرفی، هرچی. فقط بنویس و بفرست 🚀"


def catalog_text():
    lines = [
        "🛒 کاتالوگ Hermes Shop",
        "",
        "کاتالوگ دارد ساخته می‌شود 🚧🔨",
        "",
        "اولین موج محصولات:",
    ]
    for c in PRODUCTS["categories"]:
        lines.append(f"{c['icon']} **{c['name']}** — {c['desc']}")
    lines += ["", "قیمت‌ها با افتتاح اعلام می‌شوند 💰",
              "تا افتتاح من همه‌جا هستم؛ بپرس و قدم بزن 😄"]
    return "\n".join(lines)


ABOUT_TXT = (
    "✨ درباره Hermes Shop\n\n"
    "Hermes Shop فروشگاه محصولات دیجیتال است؛ ساخته‌شده با عشق و کمی جادوی هوش مصنوعی 🪄\n"
    "من هرمس هستم؛ دستیار فروش اینجا، تا راهنماییت کنم 🤝\n\n"
    f"نسخه {VERSION} 🚀 · روی Railway 🚂"
)
HELP_TXT = (
    "🆘 راهنما\n\n"
    "💬 هر سوال داری همان تایپ کن — جواب می‌دهم\n"
    "⚡ دکمه‌های پیشنهادی، جواب فوری می‌دهند\n"
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
        f"{CAT_NAMES}\n\n"
        "تا باز شدن کاتالوگ من همین‌جام؛ بپرس یا از دکمه‌ها استفاده کن 👇"
    )


def welcome_back(name):
    return f"خوش برگشتی {_html.escape(name)}! 👋😄\nاز همان‌جا ادامه بدهیم؟"


# ---------------------------------------------------------------- handlers
MEMORY = {}          # chat_id -> [{role, content}]
BRAIN = None


def show_menu(chat_id):
    send_text(chat_id, MENU_TXT, inline=INLINE_MENU)


def answer_user(chat_id, text, kb=False):
    """User's typed text -> AI answer + fixed contextual suggestion buttons."""
    stop = threading.Event()
    threading.Thread(target=typing_until, args=(chat_id, stop), daemon=True).start()
    hist = MEMORY.setdefault(chat_id, [])
    hist.append({"role": "user", "content": text})
    try:
        messages = [{"role": "system", "content": SYSTEM}] + hist[-8:]
        answer, meta = BRAIN.ask_messages(messages)
    except Exception as e:
        stop.set()
        print(f"[err] chat={chat_id}: {e}", flush=True)
        send_text(chat_id, "اوپس، الان همه‌ی مغزهایم خسته‌اند 😅 یه بار دیگر امتحان کن.", kb=kb)
        return
    stop.set()
    plain = answer.strip()
    hist.append({"role": "assistant", "content": plain})
    del hist[:-10]
    keys = pick_sugs(text)
    print(f"[ans] chat={chat_id} prov={meta['provider']} model={meta['model']} "
          f"{meta['latency_s']}s sugs={','.join(keys)}", flush=True)
    rows = sug_rows(keys)
    if kb:   # first contact: install the persistent keyboard this once
        send_text(chat_id, md_to_html(plain), kb=True)
        if rows:
            send_text(chat_id, "یکی از این‌ها به کارت میاد؟ 👇", inline={"inline_keyboard": rows})
    else:
        send_text(chat_id, md_to_html(plain),
                  inline={"inline_keyboard": rows} if rows else INLINE_HOME)


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
        send_text(chat_id, "حافظه‌ی گفتگوی این چت پاک شد 🧹 از نو شروع می‌کنیم!", inline=INLINE_HOME)
        return

    answer_user(chat_id, text, kb=first)


def handle_cb(cb):
    chat_id = (cb.get("message") or {}).get("chat", {}).get("id")
    data = cb.get("data", "")
    if chat_id is None:
        answer_cb(cb["id"])
        return
    if data == "notify:catalog":
        answer_cb(cb["id"], "🔔 در لیست افتتاح!")
    else:
        answer_cb(cb["id"])
    mark_seen(chat_id)
    print(f"[cb] chat={chat_id} data={data}", flush=True)

    if data == "menu:main":
        show_menu(chat_id)
    elif data == "menu:ask":
        send_text(chat_id, ASK_TXT, inline=INLINE_HOME)
    elif data == "menu:catalog":
        send_text(chat_id, md_to_html(catalog_text()), inline=INLINE_CATALOG)
    elif data == "menu:about":
        send_text(chat_id, ABOUT_TXT, inline=INLINE_HOME)
    elif data == "menu:help":
        send_text(chat_id, HELP_TXT, inline=INLINE_HOME)
    elif data == "wb:continue":
        send_text(chat_id, "باشه! پس بگو چی توی ذهنته 😊", inline=INLINE_HOME)
    elif data == "notify:catalog":
        res = add_waitlist(chat_id)
        if res is False:
            send_text(chat_id, "ثبت شد! 🎉 اولین نفری هستم که افتتاح کاتالوگ رو بهت خبر می‌دم.")
        elif res is True:
            send_text(chat_id, "تو قبلاً توی لیست افتتاح هستی! 🙌 خیالت راحت.")
        else:
            send_text(chat_id, "اوپس، ثبت ناقص شد 😅 یه بار دیگه دکمه رو بزن.")
    elif data.startswith("faq:"):
        send_faq(chat_id, data.split(":", 1)[1])


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
