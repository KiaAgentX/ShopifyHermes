#!/usr/bin/env python3
"""Scan ALL models on numes.ai: which work, which cost money, which error.
Tiny ping per model (max_tokens=8) to keep cost near zero. Threaded.
Writes models_report.csv and prints a summary.
"""
import concurrent.futures as cf
import csv, json, time, urllib.error, urllib.request
from pathlib import Path

HERE = Path(__file__).parent
TIMEOUT = 30
WORKERS = 20


def load_env():
    env = {}
    for line in HERE.joinpath(".env").read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def api(env, path, payload=None):
    url = env["OPENAI_BASE_URL"].rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {env['OPENAI_API_KEY']}",
        "Content-Type": "application/json",
    }
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return json.loads(r.read()), None
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode())
            code = body.get("error", {}).get("code", f"http_{e.code}")
        except Exception:
            code = f"http_{e.code}"
        return None, code
    except Exception as e:
        return None, type(e).__name__


def probe(env, model_id):
    t0 = time.time()
    _, err = api(env, "/chat/completions", {
        "model": model_id,
        "messages": [{"role": "user", "content": "ping. reply only: pong"}],
        "max_tokens": 8,
    })
    latency = round(time.time() - t0, 2)
    return model_id, err, latency


def main():
    env = load_env()
    result, err = api(env, "/models")
    if err:
        raise SystemExit(f"cannot list models: {err}")
    models = result.get("data", result)
    print(f"scanning {len(models)} models with {WORKERS} workers...\n", flush=True)

    rows = []
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(probe, env, m.get("id")): m for m in models}
        done = 0
        for fut in cf.as_completed(futs):
            mid, err, latency = fut.result()
            m = futs[fut]
            price = m.get("pricing", {}) or {}
            rows.append({
                "model": mid,
                "status": "ok" if err is None else str(err),
                "latency_s": latency if err is None else "",
                "prompt_price": price.get("prompt", ""),
                "completion_price": price.get("completion", ""),
                "error": err or "",
            })
            done += 1
            if done % 20 == 0:
                print(f"  {done}/{len(models)} done", flush=True)

    with HERE.joinpath("models_report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["status"] == "ok"]
    ok_free = [r for r in ok if float(r["prompt_price"] or 0) == 0]
    ok_paid = [r for r in ok if float(r["prompt_price"] or 0) > 0]
    broken = [r for r in rows if r["status"] != "ok"]

    ok_free.sort(key=lambda r: r["latency_s"])
    ok_paid.sort(key=lambda r: float(r["prompt_price"] or 0))

    print(f"\n=== SUMMARY ===")
    print(f"ok free : {len(ok_free)}")
    print(f"ok paid : {len(ok_paid)}")
    print(f"broken  : {len(broken)}")

    print(f"\n--- WORKING + FREE (fastest first) ---")
    for r in ok_free:
        print(f"  {r['model']:<50} {r['latency_s']}s")

    print(f"\n--- WORKING + PAID (cheapest first, price per 1M tokens) ---")
    for r in ok_paid[:15]:
        p1m = float(r["prompt_price"]) * 1e6
        print(f"  {r['model']:<50} ~${p1m:>8.2f}/M  {r['latency_s']}s")

    print(f"\n--- BROKEN (error type) ---")
    by_err = {}
    for r in broken:
        by_err.setdefault(r["status"], []).append(r["model"])
    for err_type, mids in sorted(by_err.items(), key=lambda x: -len(x[1])):
        print(f"  [{err_type}] x{len(mids)}: {', '.join(mids[:8])}{' ...' if len(mids) > 8 else ''}")


if __name__ == "__main__":
    main()
