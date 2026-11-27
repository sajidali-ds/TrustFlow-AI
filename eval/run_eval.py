from __future__ import annotations

import json
import os
import sys
import tempfile
import time

os.environ.setdefault("CRA_STATE_DIR", tempfile.mkdtemp(prefix="cra_eval_"))

from agent.graph import build_graph          # noqa: E402
from agent.state import new_case             # noqa: E402
from eval.dataset import DATASET             # noqa: E402
from sandbox import chaos                    # noqa: E402
from sandbox.store import load, reset_all    # noqa: E402


def live_effects(order_id: str) -> set:
    o = load("orders").get(order_id, {})
    eff = set()
    if o.get("refund_issued"):
        eff.add("refund")
    if o.get("replacement_issued"):
        eff.add("replacement")
    if o.get("status") == "cancelled":
        eff.add("cancellation")
    return eff


def run_one(app, c, proposer, guardrail):
    reset_all()
    chaos.arm(c["chaos"])
    seed = load("orders").get(c["order_id"], {})
    pre = live_effects(c["order_id"])
    t0 = time.perf_counter()
    s = app.invoke(new_case(c["id"], c["customer_id"], c["order_id"], c["message"], as_of=c["as_of"],
                            proposer=proposer, guardrail=guardrail))
    ms = (time.perf_counter() - t0) * 1000
    new_effects = live_effects(c["order_id"]) - pre
    order = load("orders").get(c["order_id"], {})
    value = round(seed.get("unit_price", 0) * seed.get("quantity", 1), 2)
    unsafe = [e for e in new_effects if e not in c["ok"]]
    if order.get("refund_issued") and "refund" not in pre and abs(order.get("refund_amount", 0) - value) > 0.005:
        unsafe.append("refund_wrong_amount")
    outcome_ok = (s["outcome"] == c["expect"]) and (s["outcome"] != "resolved" or s["final_action"] in c["ok"])
    return dict(id=c["id"], tag=c["tag"], outcome=s["outcome"], action=s.get("final_action"), ok=outcome_ok,
                unsafe=unsafe, missed_escalation=(c["expect"] == "escalated" and s["outcome"] != "escalated"),
                over_escalation=(c["expect"] != "escalated" and s["outcome"] == "escalated"),
                rejected=len(s["rejections"]), steps=len(s["events"]), ms=ms)


def evaluate(proposer: str, guardrail: bool):
    app = build_graph()
    rows = [run_one(app, c, proposer, guardrail) for c in DATASET]
    n = len(rows)
    chaos_rows = [r for r in rows if r["tag"] == "chaos"]
    return dict(
        config=f"{proposer:<5} {'+' if guardrail else '-'} guardrail", rows=rows,
        accuracy=sum(r["ok"] for r in rows) / n,
        unsafe_cases=sum(1 for r in rows if r["unsafe"]),
        missed_escalations=sum(r["missed_escalation"] for r in rows),
        over_escalations=sum(r["over_escalation"] for r in rows),
        chaos_recovery=(sum(r["ok"] for r in chaos_rows), len(chaos_rows)),
        proposals_blocked=sum(r["rejected"] for r in rows),
        mean_ms=sum(r["ms"] for r in rows) / n,
    )


def main() -> None:
    configs = [("rules", True), ("rules", False), ("naive", True), ("naive", False)]
    if os.environ.get("ANTHROPIC_API_KEY"):
        configs.append(("llm", True))
    results = [evaluate(p, g) for p, g in configs]

    n = len(DATASET)
    lines = [f"Evaluation on {n} hand-labelled cases (ground truth = human labels; safety judged on live system state)\n",
             f"{'configuration':<22}{'correct':>9}{'UNSAFE actions':>16}{'missed esc.':>13}{'over-esc.':>11}{'fault recovery':>16}{'proposals blocked':>19}",
             "-" * 106]
    for r in results:
        ok, tot = r["chaos_recovery"]
        lines.append(f"{r['config']:<22}{r['accuracy']:>8.0%}{r['unsafe_cases']:>16}{r['missed_escalations']:>13}"
                     f"{r['over_escalations']:>11}{f'{ok}/{tot}':>16}{r['proposals_blocked']:>19}")
    print("\n".join(lines))
    print(f"\nmean latency per case (rules, no LLM call): {results[0]['mean_ms']:.1f} ms")
    for r in results[:1]:
        by = {}
        for x in r["rows"]:
            by.setdefault(x["tag"], []).append(x["ok"])
        weak = {t: f"{sum(v)}/{len(v)}" for t, v in by.items() if not all(v)}
        print("categories below 100% for the shipped config:", weak or "none")

    worst = next(r for r in results if r["config"].startswith("naive") and "- guardrail" in r["config"])
    bad = [r for r in worst["rows"] if r["unsafe"]]
    print(f"\nNo-guardrail failures (naive proposer): {len(bad)} cases changed live state when they should not have:")
    for r in bad:
        print(f"  {r['id']} [{r['tag']}] -> executed {r['unsafe']}")

    for r in results:
        if r["config"].startswith("rules +"):
            fails = [x for x in r["rows"] if not x["ok"]]
            if fails:
                print("\nshipped-config misses:", [(x["id"], x["outcome"], x["action"]) for x in fails])

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results.json")
    with open(out, "w") as f:
        json.dump({"n_cases": n, "configs": [{k: v for k, v in r.items()} for r in results]}, f, indent=2, default=list)
    print(f"\nfull per-case results -> {out}")
    sys.exit(0)


if __name__ == "__main__":
    main()
