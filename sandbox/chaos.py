from __future__ import annotations

from typing import Iterable

from sandbox.store import load, save

MODES = ("stock_race", "timeout_after_commit", "silent_failure")


def arm(modes: Iterable[str]) -> None:
    state = {}
    for m in modes:
        if m not in MODES:
            raise ValueError(f"unknown chaos mode {m!r}; choose from {MODES}")
        state[m] = True
    save("chaos", state)


def consume(mode: str) -> bool:
    state = load("chaos")
    if state.pop(mode, False):
        save("chaos", state)
        return True
    return False
