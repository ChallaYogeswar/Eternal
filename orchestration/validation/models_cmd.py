"""List the models your keys can use, so GROQ_MODEL / GEMINI_MODEL are set from facts, not guesses.

  python -m orchestration.validation.models_cmd                 # both providers that have a key
  python -m orchestration.validation.models_cmd --provider groq --filter gpt-oss,llama,qwen
  python -m orchestration.validation.models_cmd --env-file .env

Keys come from the environment (or --env-file). They are sent only as request headers and never printed."""
from __future__ import annotations

import argparse
import os
import sys
from typing import Dict, List, Optional

from ..providers.models import list_gemini_models, list_groq_models


def load_env_file(path: str) -> Dict[str, str]:
    """Minimal KEY=VALUE parser (comments, blank lines, optional quotes). Returns a dict; never touches os.environ."""
    out: Dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip().removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def render(provider: str, rows: List[dict], needles: List[str]) -> str:
    if needles:
        rows = [r for r in rows if any(n in str(r["id"]).lower() for n in needles)]
    lines = [f"== {provider}: {len(rows)} model(s) =="]
    for r in rows:
        extra = []
        if r.get("context"):
            extra.append(f"ctx={r['context']}")
        if r.get("max_output"):
            extra.append(f"max_out={r['max_output']}")
        if r.get("active") is False:
            extra.append("INACTIVE")
        if r.get("methods"):
            extra.append("methods=" + ",".join(r["methods"]))
        lines.append(f"  {r['id']}  " + " ".join(extra))
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None, env: Optional[Dict[str, str]] = None, getters=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m orchestration.validation.models_cmd", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--provider", choices=["groq", "gemini"], default="")
    ap.add_argument("--filter", default="", help="comma-separated substrings; keep models whose id contains any")
    ap.add_argument("--env-file", default="")
    args = ap.parse_args(argv)
    e = dict(os.environ if env is None else env)
    if args.env_file:
        e.update(load_env_file(args.env_file))
    getters = getters or {"groq": list_groq_models, "gemini": list_gemini_models}
    keys = {"groq": e.get("GROQ_API_KEY", ""), "gemini": e.get("GEMINI_API_KEY", "")}
    needles = [n.strip().lower() for n in args.filter.split(",") if n.strip()]
    wanted = [args.provider] if args.provider else ["gemini", "groq"]
    rc, shown = 0, 0
    for p in wanted:
        if not keys[p]:
            print(f"{p}: no key set, skipped")
            continue
        try:
            print(render(p, getters[p](keys[p]), needles))
            shown += 1
        except Exception as ex:  # noqa: BLE001 - report and carry on to the next provider
            msg = str(ex)
            for secret in (v for v in keys.values() if v):
                msg = msg.replace(secret, "***")
            print(f"{p}: FAILED: {type(ex).__name__}: {msg[:300]}")
            rc = 1
    if not shown and rc == 0:
        print("No keys found. Set GEMINI_API_KEY and/or GROQ_API_KEY (or pass --env-file).")
        rc = 2
    return rc


if __name__ == "__main__":
    sys.exit(main())
