#!/usr/bin/env python3
"""brain.py — the bot's LLM brain with MULTI-PROVIDER automatic failover.

Provider pool (in order): numes.ai first, then OpenRouter (free models).
- Every entry is one (provider, key, model) combo.
- On 502/timeout/empty-content -> skip to the next entry (next model).
- On 401/402/403/429 (key problem) -> skip too; key rotation happens naturally
  because each (model, key) pair is its own entry.
- Remembers which entry worked last and tries it first.

Usage:
  python3 brain.py "soalat"
  python3 brain.py --selftest
"""
import json, os, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ENV = {}

ENV_KEYS = ["TELEGRAM_BOT_TOKEN", "OWNER_CHAT_ID",
            "OPENAI_BASE_URL", "OPENAI_API_KEY", "NUMES_API_KEYS", "MODELS",
            "OPENROUTER_API_KEY", "OPENROUTER_MODELS"]


def load_env():
    """Fill ENV: .env file first, then real environment variables override
    (so Railway/production env wins over the local .env file)."""
    global ENV
    if HERE.joinpath(".env").exists():
        for line in HERE.joinpath(".env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                ENV[k.strip()] = v.strip()
    for k in list(ENV.keys()) + ENV_KEYS:
        if os.environ.get(k):
            ENV[k] = os.environ[k]
    return ENV


OR_BASE = "https://openrouter.ai/api/v1"


def _pool():
    """Flat ordered list of all (base, key, model) fallback entries."""
    pool = []
    # --- provider 1: numes.ai ---
    base = ENV.get("OPENAI_BASE_URL", "").rstrip("/")
    keys = [k.strip() for k in ENV.get("NUMES_API_KEYS",
            ENV.get("OPENAI_API_KEY", "")).split(",") if k.strip()]
    models = [m.strip() for m in ENV.get("MODELS", "").split(",") if m.strip()]
    for m in models:
        for k in keys:
            pool.append({"base": base, "key": k, "model": m, "prov": "numes"})
    # --- provider 2: OpenRouter (free models) ---
    or_key = ENV.get("OPENROUTER_API_KEY", "").strip()
    or_models = [m.strip() for m in ENV.get("OPENROUTER_MODELS", "").split(",") if m.strip()]
    if or_key:
        for m in or_models:
            pool.append({"base": OR_BASE, "key": or_key, "model": m, "prov": "openrouter"})
    return pool


class Brain:
    def __init__(self):
        load_env()
        self.pool = _pool()
        self.last_good = None     # index of the pool entry that worked last
        if not self.pool:
            raise SystemExit("no API key/models in .env")

    def _call(self, base, key, model, messages, max_tokens=700, timeout=60):
        url = base + "/chat/completions"
        payload = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode()
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if "openrouter.ai" in base:
            headers["X-Title"] = "Hermes Shop"
        req = urllib.request.Request(url, data=payload, headers=headers)
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
        content = body["choices"][0]["message"].get("content")
        if not content:
            # some reasoning models burn the whole token budget -> empty content
            raise ValueError("empty content")
        return content, round(time.time() - t0, 1), body.get("usage", {})

    def ask(self, user_text, system=None, max_tokens=700):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_text})
        return self.ask_messages(messages, max_tokens)

    def ask_messages(self, messages, max_tokens=700):
        # try last-good combo first, then everything else in fallback order
        plan = list(range(len(self.pool)))
        if self.last_good is not None:
            plan.remove(self.last_good)
            plan.insert(0, self.last_good)

        errors = []
        for i in plan:
            e = self.pool[i]
            try:
                answer, latency, usage = self._call(e["base"], e["key"], e["model"],
                                                    messages, max_tokens=max_tokens)
                self.last_good = i
                return answer, {"model": e["model"], "provider": e["prov"],
                                "latency_s": latency,
                                "tokens": f"{usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')}"}
            except urllib.error.HTTPError as ex:
                code = ex.code
                detail = ""
                try:
                    err = json.loads(ex.read().decode()).get("error", {})
                    detail = str(err.get("code", err.get("message", "")))[:60]
                except Exception:
                    pass
                errors.append(f"{e['prov']}/{e['model']} http_{code} {detail}")
            except Exception as ex:
                errors.append(f"{e['prov']}/{e['model']} {type(ex).__name__}")
        raise RuntimeError("hamash kharab shod:\n  " + "\n  ".join(errors))


if __name__ == "__main__":
    brain = Brain()
    print(f"pool: {len(brain.pool)} entries -> "
          + ", ".join(f"{e['prov']}:{e['model']}" for e in brain.pool))
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        question = "Yek jomle kodomi be falsafi chi benevis? (Yek jomle, kutah)"
    else:
        question = " ".join(sys.argv[1:]) or "Salam!"
    answer, meta = brain.ask(question, system="You are Hermes Shop, the friendly sales assistant of a digital-products store. Answer in the user's language, short and warm.")
    print(f"model={meta['model']}  provider={meta['provider']}  {meta['latency_s']}s  tokens={meta['tokens']}")
    print(f"\n{answer}")
