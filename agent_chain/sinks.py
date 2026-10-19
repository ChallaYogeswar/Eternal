"""Off-box checkpoint copies through an operator-supplied command (scp over Tailscale, `git commit && git push`, ...).

The command is an argv LIST run without a shell. Placeholders `{file}` and `{name}` are substituted into single argv
items, so a checkpoint's contents can never become shell syntax. Fetching uses a second command that writes the
copies into a directory we then read. A failing command raises, and the CheckpointWriter turns that into a logged
`checkpoint_failed` entry; the run itself is never blocked by a sink being down (staleness is what the Auditor sees).
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from typing import List, Sequence

from orchestration.checkpoint import Checkpoint, CheckpointError


def _subst(argv: Sequence[str], **kw: str) -> List[str]:
    return [a.format(**kw) if "{" in a else a for a in argv]


class CommandSink:
    def __init__(self, publish_argv: Sequence[str], fetch_argv: Sequence[str], *, timeout_s: float = 30.0):
        if not publish_argv or not fetch_argv:
            raise ValueError("both publish_argv and fetch_argv are required")
        if isinstance(publish_argv, str) or isinstance(fetch_argv, str):
            raise ValueError("argv must be a list, not a string (no shell is used)")
        self.publish_argv, self.fetch_argv, self.timeout_s = list(publish_argv), list(fetch_argv), timeout_s

    def _run(self, argv: List[str]) -> None:
        try:
            r = subprocess.run(argv, shell=False, capture_output=True, timeout=self.timeout_s)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise CheckpointError(f"sink command failed: {type(e).__name__}") from e
        if r.returncode != 0:
            raise CheckpointError(f"sink command exited {r.returncode}")

    def publish(self, cp: Checkpoint) -> None:
        name = f"checkpoint-{cp.seq:08d}.json"
        with tempfile.TemporaryDirectory(prefix="ckpt_") as d:
            path = os.path.join(d, name)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(cp.to_dict(), f, sort_keys=True)
            self._run(_subst(self.publish_argv, file=path, name=name))

    def fetch_all(self) -> List[Checkpoint]:
        with tempfile.TemporaryDirectory(prefix="ckpt_") as d:
            self._run(_subst(self.fetch_argv, dir=d))
            out = []
            for fn in sorted(os.listdir(d)):
                if fn.startswith("checkpoint-") and fn.endswith(".json"):
                    with open(os.path.join(d, fn), encoding="utf-8") as f:
                        out.append(Checkpoint.from_dict(json.load(f)))
            return sorted(out, key=lambda c: c.seq)
