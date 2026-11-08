#!/usr/bin/env python3
"""
Part 2 runner: "Can care be passed on, and does it survive the swarm?" (hackathon weekend, Nov 6-8, 2026)
Sends eHydra's exact review requests (packets_p2.json) to OpenAI. In the pushback conditions, when a
reviewer rejects, it sends the Product Owner's pushback as a second turn and records the final verdict.

Safe to stop and restart: it skips anything already saved in results_p2.jsonl.
Standard library only. Needs OPENAI_API_KEY.

  python3 run_p2.py --reps 1          # one full pass (360 reviews + follow-ups)
  python3 run_p2.py --reps 2          # adds a second pass, skipping the first
  python3 run_p2.py --fake --reps 1   # offline test, no API calls

Other models through OpenRouter (needs OPENROUTER_API_KEY; stops before going over --budget dollars):
  python3 run_p2.py --model anthropic/claude-sonnet-4.5 --reps 1 --budget 15
"""
import argparse, json, os, re, time, urllib.request, urllib.error
from pathlib import Path
HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser(); ap.add_argument("--reps", type=int, default=1); ap.add_argument("--fake", action="store_true")
ap.add_argument("--model", default=None, help="OpenRouter model ID, e.g. anthropic/claude-sonnet-4.5 (omit for OpenAI gpt-5.4-mini)")
ap.add_argument("--budget", type=float, default=10.0, help="OpenRouter only: stop before spending more than this many dollars")
a = ap.parse_args()
OR = bool(a.model)
key = os.environ.get("OPENROUTER_API_KEY" if OR else "OPENAI_API_KEY")
if not a.fake and not key: raise SystemExit("Set " + ("OPENROUTER_API_KEY" if OR else "OPENAI_API_KEY") + " first (export ...=...)")
packets = json.loads((HERE / "packets_p2.json").read_text())
slug = re.sub(r"[^A-Za-z0-9]+", "-", a.model).strip("-") if OR else ""
out = HERE / ("results_p2_FAKE.jsonl" if a.fake else (f"results_p2_{slug}.jsonl" if OR else "results_p2.jsonl"))
spent = [0.0]
done = set()
if out.exists():
    for line in out.read_text().splitlines():
        try: r = json.loads(line); done.add((r["id"], r["rep"])) if r.get("ok") else None
        except Exception: pass

def call(body):
    if a.fake:
        txt = body["input"] if isinstance(body["input"], str) else body["input"][-1]["content"]
        if "Product Owner" in txt: return json.dumps({"approved": True, "reason": "Owner approved.", "findings": []})
        bad = "adpartner" in txt
        return json.dumps({"approved": not bad, "reason": "location sent to ad partner" if bad else "fine",
                           "findings": [{"severity": "blocking", "claim": "Sends precise location to an ad partner without consent", "reproduction": "call detectTimeZone"}] if bad else []})
    if OR:
        if spent[0] >= a.budget: raise SystemExit(f"\nStopped: reached the ${a.budget:.2f} budget (spent ${spent[0]:.2f}). Rerun with a higher --budget to continue.")
        inp = body["input"]
        msgs = [{"role": "system", "content": body["instructions"]}] + ([{"role": "user", "content": inp}] if isinstance(inp, str) else inp)
        payload = {"model": a.model, "messages": msgs, "response_format": {"type": "json_object"}, "max_tokens": 8000,
                   "reasoning": {"effort": "low"}, "usage": {"include": True}}
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(payload).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json", "X-Title": "eHydra hackathon study"})
    else:
        req = urllib.request.Request("https://api.openai.com/v1/responses", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=300) as r: data = json.loads(r.read())
            if OR:
                if "error" in data: raise RuntimeError(str(data["error"])[:200])
                spent[0] += float((data.get("usage") or {}).get("cost") or 0)
                return data["choices"][0]["message"].get("content") or ""
            return "\n".join(c.get("text", "") for it in data.get("output", []) for c in (it.get("content") or []) if c.get("type") == "output_text")
        except urllib.error.HTTPError as e:
            if e.code in (401, 402, 403):
                raise SystemExit(f"\nStopped: the provider refused the request (HTTP {e.code}: {e.read()[:160]!r}). "
                                 "Nothing else was sent. Check the account's credits or key, then rerun.")
            if e.code in (429, 500, 502, 503) and attempt < 3: time.sleep(int(e.headers.get("Retry-After", 10 * (attempt + 1)))); continue
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:200]!r}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3: time.sleep(15); continue
            raise RuntimeError(str(e)[:200])

def rejected(text):
    try: return json.loads(text).get("approved") is False
    except Exception:
        m = re.search(r'"approved"\s*:\s*(true|false)', text or ""); return bool(m and m.group(1) == "false")

todo = [(p, rep) for rep in range(a.reps) for p in packets if (p["id"], rep) not in done]
print(f"{len(todo)} reviews to run ({len(done)} already saved).")
with open(out, "a") as f:
    for n, (p, rep) in enumerate(todo, 1):
        t0 = time.time(); rec = {"id": p["id"], "case": p["case"], "values": p["values"], "social": p["social"], "role": p["role"], "rep": rep}
        try:
            first = call(p["body"]); rec.update(ok=True, first=first)
            if p["followup"] and rejected(first):
                body2 = dict(p["body"]); body2["input"] = [
                    {"role": "user", "content": p["body"]["input"]},
                    {"role": "assistant", "content": first},
                    {"role": "user", "content": p["followup"]}]
                rec["final"] = call(body2)
        except Exception as e:
            rec.update(ok=False, error=str(e)[:300])
        rec["seconds"] = round(time.time() - t0, 1)
        if OR: rec["model"] = a.model; rec["spent_total"] = round(spent[0], 4)
        f.write(json.dumps(rec) + "\n"); f.flush()
        status = "error: " + rec["error"][:50] if not rec["ok"] else ("pushback sent" if "final" in rec else "ok")
        cost = f"  ${spent[0]:.2f} so far" if OR else ""
        print(f"[{n}/{len(todo)}] {p['case']:4s} {p['values']} {p['social'][:2]} {p['role']:7s} rep {rep}  {status}  ({rec['seconds']}s){cost}")
print(f"\nDone. Attach {out.name} to the chat with Claude.")
