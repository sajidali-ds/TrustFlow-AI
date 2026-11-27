from __future__ import annotations

import copy
import json
import os
import shutil
import threading
from typing import Any, Dict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEED_DIR = os.path.join(ROOT, "data", "seed")
_lock = threading.RLock()


def state_dir() -> str:
    d = os.environ.get("CRA_STATE_DIR") or os.path.join(ROOT, ".state")
    os.makedirs(d, exist_ok=True)
    return d


def _path(name: str) -> str:
    return os.path.join(state_dir(), f"{name}.json")


def load(name: str) -> Dict[str, Any]:
    with _lock:
        if not os.path.exists(_path(name)):
            reset_all()
        with open(_path(name)) as f:
            return json.load(f)


def save(name: str, data: Dict[str, Any]) -> None:
    with _lock:
        tmp = _path(name) + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _path(name))  # atomic: a crash never leaves a half-written file


def reset_all() -> None:
    """Restore customers/orders/inventory from seeds; clear ledger, chaos, audit."""
    with _lock:
        for name in ("customers", "orders", "inventory"):
            with open(os.path.join(SEED_DIR, f"{name}.json")) as f:
                seed = json.load(f)
            with open(_path(name), "w") as f:
                json.dump(copy.deepcopy(seed), f, indent=2)
        for name in ("ledger", "chaos"):
            with open(_path(name), "w") as f:
                json.dump({}, f)
        audit = os.path.join(state_dir(), "audit.jsonl")
        if os.path.exists(audit):
            os.remove(audit)


def wipe_state() -> None:
    shutil.rmtree(state_dir(), ignore_errors=True)
