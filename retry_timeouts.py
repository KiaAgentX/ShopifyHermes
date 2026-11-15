#!/usr/bin/env python3
"""Retry the models that timed out during the bulk scan — sequential, 90s timeout."""
import csv, json, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent


def load_env():
    env = {}
    for line in HERE.joinpath(".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def probe(env, model_id, timeout=90):
    url = env["OPENAI_BASE_URL"].rstrip("/") + "/chat/completions"
    headers = {"Authorization": f"Bearer {env['OPENAI_API_KEY']}", "Content-Type": "application/json"}
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": "ping. reply only: pong"}],
        "max_tokens": 8,
    }).encode()
    req = urllib.request.Request(url, data=payload, headers=headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = json.loads(r.read())
        return "ok", round(time.time() - t0, 1), body["choices"][0]["message"]["content"][:40]
    except urllib.error.HTTPError as e:
        try:
            code = json.loads(e.read().decode()).get("error", {}).get("code", f"http_{e.code}")
        except Exception:
            code = f"http_{e.code}"
        return code, round(time.time() - t0, 1), ""
    except Exception as e:
        return type(e).__name__, round(time.time() - t0, 1), ""


def main():
    env = load_env()
    with HERE.joinpath("models_report.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    timed_out = [r["model"] for r in rows if "timeout" in r["status"].lower() or r["status"] == "TimeoutError"]
    print(f"retrying {len(timed_out)} timed-out models (sequential, 90s):\n")
    for mid in timed_out:
        status, lat, snippet = probe(env, mid)
        print(f"  {mid:<48} {status:<12} {lat:>5}s  {snippet}")
        if status == "ok":
            for r in rows:
                if r["model"] == mid:
                    r["status"] = "ok"
                    r["latency_s"] = lat
                    r["error"] = ""
    with HERE.joinpath("models_report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)
    ok = [r for r in rows if r["status"] == "ok"]
    print(f"\nfinal ok models ({len(ok)}):")
    for r in ok:
        print(f"  {r['model']:<48} {r['latency_s']}s  price={r['prompt_price']}")


if __name__ == "__main__":
    main()
