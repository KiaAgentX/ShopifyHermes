#!/usr/bin/env python3
"""brain.py — the bot's LLM brain with MULTI-PROVIDER automatic failover.

Provider pool (in order): Coarena (fastest, free $5/day), numes.ai,
OpenRouter (free), 9Router (last).
- Every entry is one (provider, key, model) combo.
- On 502/timeout/empty-content -> skip to the next entry (next model).
- On 401/402/403/429 (key problem) -> skip too; key rotation happens naturally
  because each (model, key) pair is its own entry.
- Remembers which entry worked last and tries it first.

Usage:
  python3 brain.py "soalat"
  python3 brain.py --selftest
"""
import json, os, re, sys, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
ENV = {}

ENV_KEYS = ["TELEGRAM_BOT_TOKEN", "OWNER_CHAT_ID",
            "OPENAI_BASE_URL", "OPENAI_API_KEY", "NUMES_API_KEYS", "MODELS",
            "OPENROUTER_API_KEY", "OPENROUTER_MODELS",
            "COARENA_API_KEY", "COARENA_MODELS",
            "ROUTER9_API_KEY", "ROUTER9_MODELS"]


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
CA_BASE = "https://coarena.ai/api/v1"
R9_BASE = "https://9router-production-af2f3af2.up.railway.app/v1"


def _pool():
    """Flat ordered list of all (base, key, model) fallback entries.

    Order = real latency priority (from live probes):
    Coarena (1.5-7s) -> numes (fast when warm) -> OpenRouter free -> 9Router.
    """
    pool = []
    # --- provider 1: Coarena (fastest, free $5/day credit) ---
    ca_key = ENV.get("COARENA_API_KEY", "").strip()
    ca_models = [m.strip() for m in ENV.get("COARENA_MODELS", "").split(",") if m.strip()]
    if ca_key:
        for m in ca_models:
            pool.append({"base": CA_BASE, "key": ca_key, "model": m, "prov": "coarena"})
    # --- provider 2: numes.ai ---
    base = ENV.get("OPENAI_BASE_URL", "").rstrip("/")
    keys = [k.strip() for k in ENV.get("NUMES_API_KEYS",
            ENV.get("OPENAI_API_KEY", "")).split(",") if k.strip()]
    models = [m.strip() for m in ENV.get("MODELS", "").split(",") if m.strip()]
    for m in models:
        for k in keys:
            pool.append({"base": base, "key": k, "model": m, "prov": "numes"})
    # --- provider 3: OpenRouter (free models) ---
    or_key = ENV.get("OPENROUTER_API_KEY", "").strip()
    or_models = [m.strip() for m in ENV.get("OPENROUTER_MODELS", "").split(",") if m.strip()]
    if or_key:
        for m in or_models:
            pool.append({"base": OR_BASE, "key": or_key, "model": m, "prov": "openrouter"})
    # --- provider 4 (last): 9Router (railway proxy, oc/* free models) ---
    r9_key = ENV.get("ROUTER9_API_KEY", "").strip()
    r9_models = [m.strip() for m in ENV.get("ROUTER9_MODELS", "").split(",") if m.strip()]
    if r9_key:
        for m in r9_models:
            pool.append({"base": R9_BASE, "key": r9_key, "model": m, "prov": "9router"})
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
        payload = json.dumps({"model": model, "messages": messages,
                              "max_tokens": max_tokens, "temperature": 0.6}).encode()
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

    @staticmethod
    def _looks_broken(text, ref=""):
        """Heuristic quality gate: catches empty/garbled/wrong-language LLM
        output so failover moves to the next model instead of sending junk.
        ref = the user's last message (used to detect language mismatch)."""
        if not text or len(text.strip()) < 5:
            return True
        if "\ufffd" in text:
            return True
        if re.search(r"([A-Za-z\u0600-\u06FF])\1{4,}", text):   # e.g. هههههه/word runs
            return True
        # Persian question but a mostly-Latin answer -> wrong language, retry
        if len(re.findall(r"[\u0600-\u06FF]", ref)) >= 10:
            letters = re.findall(r"[A-Za-z\u0600-\u06FF]", text)
            if letters:
                latin = sum(1 for c in letters if c.isascii())
                if latin / len(letters) > 0.6:
                    return True
        return False

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
        fallback = None   # first (answer, meta) that answered but looked garbled
        ref = messages[-1].get("content", "") if messages else ""
        for i in plan:
            e = self.pool[i]
            meta = {"model": e["model"], "provider": e["prov"]}
            try:
                answer, latency, usage = self._call(e["base"], e["key"], e["model"],
                                                    messages, max_tokens=max_tokens)
            except urllib.error.HTTPError as ex:
                code = ex.code
                detail = ""
                try:
                    err = json.loads(ex.read().decode()).get("error", {})
                    detail = str(err.get("code", err.get("message", "")))[:60]
                except Exception:
                    pass
                errors.append(f"{e['prov']}/{e['model']} http_{code} {detail}")
                continue
            except Exception as ex:
                errors.append(f"{e['prov']}/{e['model']} {type(ex).__name__}")
                continue
            meta.update(latency_s=latency,
                        tokens=f"{usage.get('prompt_tokens', '?')}+{usage.get('completion_tokens', '?')}")
            if self._looks_broken(answer, ref):
                errors.append(f"{e['prov']}/{e['model']} garbled/low-quality (skipped)")
                if fallback is None:
                    fallback = (answer, meta)
                continue
            self.last_good = i
            return answer, meta
        if fallback is not None:      # something answered everywhere-garbled:
            return fallback           # send the least-bad one instead of erroring
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
