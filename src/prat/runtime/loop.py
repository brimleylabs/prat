"""Loop detection for agent tool calls.

An agent that keeps calling the same tool on the same target (reading one
file over and over, re-running one failing command unchanged) is stuck. The
detector sits in front of the policy: when the same call appears
``threshold`` times within the last ``window`` calls, the call is denied with
a message telling the agent to stop and reconsider. Denied calls count too,
so an agent that ignores the denial keeps being denied.
"""

from __future__ import annotations

import hashlib
import json
from collections import deque
from collections.abc import Callable

from prat.policy import Decision, ToolCall

LOOP_MESSAGE = (
    "Loop detected: you have made this same call {count} times in your last {window} "
    "tool calls. Stop and reconsider. Repeating it will not give a different result. "
    "Read the error or output you already have, change your approach, or, if you "
    "cannot proceed, finish with a final line 'BLOCKED: <reason>'."
)


def call_key(call: ToolCall) -> str:
    """What makes two calls 'the same': the tool kind, its target, and what it does there.
    Five edits to one file are work, and so are five reads of different parts of it; the same
    edit or the same read five times is a loop."""
    if call.command is not None:
        target = " ".join(call.command.split())
    elif call.path is not None:
        doing = f"{call.what}\x1f{call.content or ''}"
        digest = hashlib.sha256(doing.encode("utf-8", "replace")).hexdigest()[:16]
        target = f"{call.path.replace(chr(92), '/')}#{digest}"
    else:
        target = f"{call.tool_name}:{json.dumps(dict(call.arguments), sort_keys=True, default=str)}"
    return f"{call.kind}|{target}"


class LoopDetector:
    def __init__(self, threshold: int = 5, window: int = 8) -> None:
        if threshold < 2 or window < threshold:
            raise ValueError("need 2 <= threshold <= window")
        self.threshold = threshold
        self.window = window
        self._recent: deque[str] = deque(maxlen=window)
        self.tripped = 0

    def check(self, call: ToolCall) -> Decision | None:
        """Record a call. Returns a denial when it completes a loop, else ``None``."""
        key = call_key(call)
        self._recent.append(key)
        count = sum(1 for seen in self._recent if seen == key)
        if count >= self.threshold:
            self.tripped += 1
            return Decision.deny(LOOP_MESSAGE.format(count=count, window=self.window))
        return None

    def guard(self, policy: Callable[[ToolCall], Decision]) -> Callable[[ToolCall], Decision]:
        """Wrap a policy callback so loops are denied before the policy is asked."""

        def guarded(call: ToolCall) -> Decision:
            denial = self.check(call)
            return denial if denial is not None else policy(call)

        return guarded
