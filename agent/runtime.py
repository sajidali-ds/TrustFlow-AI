from __future__ import annotations

from typing import Any, Callable, Dict, List

END = "__end__"


class _Runner:
    def __init__(self, flow: "Flow", max_steps: int = 80):
        self.f, self.max_steps = flow, max_steps

    def invoke(self, state: Dict[str, Any]) -> Dict[str, Any]:
        node = self.f.entry
        for _ in range(self.max_steps):
            state.update(self.f.nodes[node](state) or {})
            if node in self.f.cond:
                node = self.f.cond[node][0](state)
            else:
                node = self.f.edges.get(node, END)
            if node == END:
                return state
        raise RuntimeError("flow exceeded max_steps (possible infinite loop)")


class Flow:
    def __init__(self, state_type: Any = dict):
        self.state_type = state_type
        self.nodes: Dict[str, Callable] = {}
        self.edges: Dict[str, str] = {}
        self.cond: Dict[str, Any] = {}
        self.entry = ""

    def node(self, name: str, fn: Callable) -> "Flow":
        self.nodes[name] = fn
        self.entry = self.entry or name
        return self

    def edge(self, src: str, dst: str) -> "Flow":
        self.edges[src] = dst
        return self

    def branch(self, src: str, router: Callable, targets: List[str]) -> "Flow":
        self.cond[src] = (router, targets)
        return self

    def compile(self, backend: str = "auto"):
        if backend in ("auto", "langgraph"):
            try:
                from langgraph.graph import END as LG_END, START, StateGraph
                g = StateGraph(self.state_type)
                for n, fn in self.nodes.items():
                    g.add_node(n, fn)
                g.add_edge(START, self.entry)
                for s, d in self.edges.items():
                    g.add_edge(s, LG_END if d == END else d)
                for s, (router, targets) in self.cond.items():
                    g.add_conditional_edges(s, router, {t: (LG_END if t == END else t) for t in targets})
                return g.compile()
            except ImportError:
                if backend == "langgraph":
                    raise
        return _Runner(self)
