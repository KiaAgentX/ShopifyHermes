#!/usr/bin/env python3
"""brain.py — the bot's LLM brain with automatic failover.

- Tries MODELS in order; if one is down (502/timeout), skips to the next.
- Supports MULTIPLE keys (NUMES_API_KEYS=k1,k2,...). On 402 (no credits)
  or 429 (rate limit) it rotates to the next key automatically.
- Remembers which (key, model) combo worked last time and tries it first.

Usage:
  python3 brain.py "soalat"
  python3 brain.py --selftest
"""
import json, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ENV = {}


def load_env():
    global ENV
    for line in HERE.joinpath(".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            ENV[k.strip()] = v.strip()
    return ENV


def _lists():
    keys = [k.strip() for k in ENV.get("NUMES_API_KEYS", ENV.get("OPENAI_API_KEY", "")).split(",") if k.strip()]
    models = [m.strip() for m in ENV.get("MODELS", "").split(",") if m.strip()]
    return keys, models


class Brain:
    def __init__(self):
        load_env()
        self.base = ENV["OPENAI_BASE_URL"].rstrip("/")
        self.keys, self.models = _lists()
        self.key_idx = 0          # preferred key (round-robin pointer)
        self.last_good = None     # (key_idx, model) that worked last
        if not self.keys:
            raise SystemExit("no API key in .env")
        if not self.models:
            raise SystemExit("no MODELS in .env")

    def _call(self, key, model, messages, max_tokens=700, timeout=60):
        url = self.base + "/chat/completions"
        payload = json.dumps({"model": model, "messages": messages, "max_tokens": max_tokens}).encode()
        req = urllib.request.Request(url, data=payload, headers={
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
        return body["choices"][0]["message"]["content"], round(time.time() - t0, 1), body.get("usage", {})

    def ask(self, user_text, system=None, max_tokens=700):
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_text})
        return self.ask_messages(messages, max_tokens)

    def ask_messages(self, messages, max_tokens=700):
        # try last-good combo first, then everything else in fallback order
        plan = []
        if self.last_good:
            plan.append(self.last_good)
        ki0 = self.key_idx
        for m in self.models:
            for i in range(len(self.keys)):
                ki = (ki0 + i) % len(self.keys)
                if (ki, m) not in plan:
                    plan.append((ki, m))

        errors = []
        for ki, model in plan:
            key_err = None
            try:
                answer, latency, usage = self._call(self.keys[ki], model, messages, max_tokens=max_tokens)
                self.last_good = (ki, model)
                self.key_idx = ki
                return answer, {"model": model, "key": f"#{ki + 1}", "latency_s": latency,
                                "tokens": f"{usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')}"}
            except urllib.error.HTTPError as e:
                code = e.code
                detail = ""
                try:
                    detail = json.loads(e.read().decode()).get("error", {}).get("code", "")
                except Exception:
                    pass
                if code in (401, 402, 403, 429):   # key problem -> rotate key, retry same model
                    self.key_idx = (ki + 1) % len(self.keys)
                    key_err = f"key#{ki + 1} {code}({detail})"
                    errors.append(f"{model} {key_err}")
                    continue
                errors.append(f"{model} http_{code}")  # provider problem -> next model
            except Exception as e:
                errors.append(f"{model} {type(e).__name__}")
        raise RuntimeError("hamash kharab shod:\n  " + "\n  ".join(errors))


if __name__ == "__main__":
    brain = Brain()
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        question = "Yek jomle kodomi be falsafi chi benevis? (Yek jomle, kutah)"
    else:
        question = " ".join(sys.argv[1:]) or "Salam!"
    answer, meta = brain.ask(question, system="You are Hermes Shop, the friendly sales assistant of a digital-products store. Answer in the user's language, short and warm.")
    print(f"model={meta['model']}  key={meta['key']}  {meta['latency_s']}s  tokens={meta['tokens']}")
    print(f"\n{answer}")
