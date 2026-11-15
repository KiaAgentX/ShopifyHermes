#!/usr/bin/env python3
"""Test the LLM API ecosystem: models list + one real chat completion.
Uses only stdlib (no pip installs). Reads config from .env.

Usage:
  python3 llm_test.py list          -> GET /models (what can this key use?)
  python3 llm_test.py chat <prompt> -> POST /chat/completions with MODEL
"""
import json, sys, urllib.request, urllib.error
from pathlib import Path


def load_env():
    env = {}
    for line in Path(__file__).parent.joinpath(".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def request(env, path, payload=None):
    url = env["OPENAI_BASE_URL"].rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {env['OPENAI_API_KEY']}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"http_error": e.code, "body": e.read().decode()[:500]}


def main():
    env = load_env()
    if not env.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY in .env khali-e — aval key ro bezar.")

    mode = sys.argv[1] if len(sys.argv) > 1 else "list"

    if mode == "list":
        result = request(env, "/models")
        if "http_error" in result:
            print(f"ERROR {result['http_error']}:", result["body"])
            return
        data = result.get("data", result)
        print(f"{len(data)} models available for this key:\n")
        for m in data[:25]:
            mid = m.get("id") or m.get("slug") or "?"
            price = m.get("pricing", {})
            prompt_p = price.get("prompt", "?")
            print(f"  {mid:<45} prompt: {prompt_p}")
        if len(data) > 25:
            print(f"  ... and {len(data) - 25} more")

    elif mode == "chat":
        prompt = sys.argv[2] if len(sys.argv) > 2 else "Salam! Ye jomle khodaro moarefi kon."
        model = env.get("MODEL")
        if not model:
            raise SystemExit("MODEL in .env khali-e — avval 'list' bezan va yek model entekhab kon.")
        result = request(env, "/chat/completions", {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are Hermes Shop, a helpful sales assistant for a digital products store. Reply in the same language the user writes in."},
                {"role": "user", "content": prompt},
            ],
        })
        if "http_error" in result:
            print(f"ERROR {result['http_error']}:", result["body"])
            return
        msg = result["choices"][0]["message"]["content"]
        usage = result.get("usage", {})
        print(f"MODEL : {model}")
        print(f"TOKENS: prompt={usage.get('prompt_tokens', '?')} completion={usage.get('completion_tokens', '?')}")
        print(f"\n--- JAVAB ---\n{msg}")

    else:
        print("usage: python3 llm_test.py [list|chat] [prompt]")


if __name__ == "__main__":
    main()
