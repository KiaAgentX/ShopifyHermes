# Hermes Shop 🤖🛍️

بات فروشنده فروشگاه محصولات دیجیتال — نسخه 1.1

بات تلگرامی که با مغز LLM (سازگار با OpenAI) حرف می‌زنه. نقشه‌ی راه: اتصال به دیتابیس فروشگاه، مشاور فروش (نقش A) و تولیدکننده محصول (نقش B).

## چه چیزهایی در v1.1 اضافه شد

- **روند شروع (Onboarding UI):** خوش‌آمد گرم، منوی Inline چهاردکمه، خوش‌برگشتی برای کاربران قدیمی، دکمه ثابت «🏠 منو» پایین صفحه — طرح کامل در `docs/UI-FLOW.md`
- **دکمه‌های پیشنهادی:** زیر هر جواب AI دو سوال مرتبط (ساخته‌شده توسط خود LLM با مارکر `SUGGEST:`)
- **فیکس جواب دوبار:** هنگام استارت، پیام‌های عقب‌مانده (backlog) تایید و رد می‌شوند + offset در `data/state.json` ذخیره می‌شود
- **فیکس ستاره‌ها:** خروجی markdown به HTML ایمن تبدیل می‌شود + fallback متنی
- **مغز چندتامین‌کننده:** numes.ai + OpenRouter (مدل‌های free) با failover خودکار بین همه‌ی (تامین‌کننده، کلید، مدل)ها

## فایل‌ها

| فایل | نقش |
|---|---|
| `bot.py` | حلقه تلگرام + روند شروع + منوها + فیکس‌های پایداری |
| `brain.py` | مغز LLM چندتامین‌کننده با failover خودکار |
| `docs/UI-FLOW.md` | سند طراحی UI — مرجع همه پیام‌ها و دکمه‌ها |
| `send_message.py` | ابزار کوچک ارسال پیام |
| `llm_test.py` | تست سریع API (`list` / `chat`) |
| `model_scan.py` | اسکن مدل‌های numes |
| `models_report.csv` | گزارش کامل اسکن numes |

## راه‌اندازی

```bash
cp .env.example .env   # مقادیر واقعی را پر کنید
python3 bot.py
```

متغیرهای محیطی روی Railway (Raw Editor): همان کلیدهای `.env` — شامل
`OPENROUTER_API_KEY` + `OPENROUTER_MODELS`، `COARENA_API_KEY` + `COARENA_MODELS`
و `ROUTER9_API_KEY` + `ROUTER9_MODELS` برای تامین‌کننده‌های دوم به بعد.

## معماری ضدخطا (brain)

استخر (pool) از همه ترکیب‌های (تامین‌کننده، کلید، مدل) ساخته می‌شود؛ به همین ترتیب:

1. numes: همه مدل‌های `MODELS` × همه کلیدهای `NUMES_API_KEYS`
2. OpenRouter: مدل‌های `OPENROUTER_MODELS` (فقط مدل‌های `:free`)
3. Coarena: مدل‌های `COARENA_MODELS` (اعتبار رایگان $5 در روز)
4. 9Router: مدل‌های `ROUTER9_MODELS` (پروکسی Railway، مدل‌های `oc/*-free`)

- خطای مدل (502/timeout/جواب خالی) → ورودی بعدی
- خطای کلید (401/402/403/429) → ورودی بعدی (چرخش کلید طبیعی اتفاق می‌افتد)
- ترکیب موفق آخر، دفعه بعد اول امتحان می‌شود

> مدل‌های free روی OpenRouter چرخشی هستند؛ با اسکریپت‌های زیر لیست تازه بگیرید:
> `python3 /home/z/my-project/scripts/openrouter_probe.py` (OpenRouter)
> `python3 /home/z/my-project/scripts/coarena_probe.py` (Coarena + تست feedback)
> `python3 /home/z/my-project/scripts/router9_probe.py` (9Router)

## امنیت

- `.env` در `.gitignore` است و هرگز کامیت نمی‌شود
- توکن‌ها (تلگرام، numes، OpenRouter، گیت‌هاب) را قبل از انتشار عمومی rotate کنید
