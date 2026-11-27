from __future__ import annotations

import json
import sys
import time

from agent.graph import build_graph
from agent.state import new_case
from sandbox import chaos
from sandbox.store import reset_all
from scenarios.cases import AS_OF, SCENARIOS


def run_case(app, c: dict) -> dict:
    reset_all()                                  # every case starts from the clean seed
    chaos.arm(c.get("chaos", []))
    state = new_case(c["case_id"], c["customer_id"], c["order_id"], c["customer_message"],
                     as_of=c.get("as_of", AS_OF), proposer=c.get("proposer", "auto"),
                     guardrail=c.get("guardrail", True))
    t0 = time.perf_counter()
    out = app.invoke(state)
    out['elapsed_ms'] = round((time.perf_counter() - t0) * 1000, 1)
    return out


def show(c: dict, s: dict) -> None:
    print("=" * 100)
    print(f"{c['case_id']} | {s['order_id']} | {s['customer_id']} | proposer={s['proposer']}")
    print(f"  customer: \"{s['customer_message']}\"")
    print(f"  scenario: {c['note']}")
    print("-" * 100)
    for line in s["log"]:
        print("  " + line)
    print("-" * 100)
    extra = f" via {s['final_action']}" if s.get("final_action") else ""
    print(f"  OUTCOME: {s['outcome'].upper()}{extra} | actions that reached systems: "
          f"{[e['action'] + ':' + e['status'] for e in s['executed']] or 'none'}")
    if s.get("escalation"):
        b = s["escalation"]
        print(f"  HANDOFF BRIEF: priority={b['priority']} | next step: {b['suggested_next_step']}")
    print()


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    as_json = "--json" in sys.argv
    cases = [c for c in SCENARIOS if not args or c["case_id"] in args]
    if not cases:
        sys.exit(f"unknown scenario; available: {[c['case_id'] for c in SCENARIOS]}")
    app = build_graph()
    for c in cases:
        s = run_case(app, c)
        if as_json:
            print(json.dumps({k: v for k, v in s.items() if not k.startswith("_")}, indent=2, default=str))
        else:
            show(c, s)


if __name__ == "__main__":
    main()
