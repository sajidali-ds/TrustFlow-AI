from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict


class CaseState(TypedDict, total=False):
    # inputs
    case_id: str
    customer_id: str
    order_id: str
    customer_message: str
    as_of: str                       # ISO date the claim is evaluated on (deterministic policy windows)
    # config
    proposer: str                    # rules | naive | llm | auto
    guardrail: bool                  # False only for the ablation experiment in eval/
    max_attempts: int
    # evidence
    intent: Dict[str, Any]
    customer_info: Optional[Dict[str, Any]]
    order_info: Optional[Dict[str, Any]]
    inventory_info: Optional[Dict[str, Any]]
    policy_docs: List[Dict[str, Any]]
    verdict: Optional[Dict[str, Any]]
    _verdict_obj: Any                # in-memory Verdict (not serialised)
    # reasoning
    plan: Optional[Dict[str, Any]]
    constraints: List[Dict[str, str]]    # {"action","kind": blocked|unverified,"reason"}
    rejections: List[Dict[str, Any]]     # proposals the guardrail refused
    attempts: int
    guard_ok: bool
    # execution
    idempotency_key: Optional[str]
    on_hand_before: Optional[int]
    action_result: Optional[Dict[str, Any]]
    verification: Optional[Dict[str, Any]]
    executed: List[Dict[str, Any]]       # every action API call that reached the system
    # outcome
    outcome: str                         # resolved | already_resolved | needs_clarification | escalated
    question: Optional[str]
    elapsed_ms: float
    final_action: Optional[str]
    escalation: Optional[Dict[str, Any]]
    customer_reply: Optional[str]
    # audit
    log: List[str]
    events: List[Dict[str, Any]]


def new_case(case_id: str, customer_id: str, order_id: str, customer_message: str, *,
             as_of: str, proposer: str = "auto", guardrail: bool = True, max_attempts: int = 3) -> CaseState:
    return CaseState(
        case_id=case_id, customer_id=customer_id, order_id=order_id, customer_message=customer_message,
        as_of=as_of, proposer=proposer, guardrail=guardrail, max_attempts=max_attempts,
        intent={}, customer_info=None, order_info=None, inventory_info=None, policy_docs=[],
        verdict=None, plan=None, constraints=[], rejections=[], attempts=0, guard_ok=False,
        idempotency_key=None, on_hand_before=None, action_result=None, verification=None, executed=[],
        outcome="", final_action=None, escalation=None, customer_reply=None, log=[], events=[],
    )


def emit(state: CaseState, node: str, msg: str, **data: Any) -> None:
    state["log"].append(f"[{node}] {msg}")
    state["events"].append({"node": node, "msg": msg, **data})
