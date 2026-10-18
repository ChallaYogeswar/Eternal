"""Run the validation suite against LOCAL Ollama models (the path for INTERNAL data: nothing leaves the machine).

  python -m orchestration.validation.local_cmd --list                       # models you have already pulled
  python -m orchestration.validation.local_cmd --models qwen3:4b --smoke    # 2 cases, a few minutes
  python -m orchestration.validation.local_cmd --models qwen3:4b,llama3.2:3b --repeats 3
      --host http://localhost:11434  --num-ctx 4096  --max-output-tokens 512  --no-json-mode  --think false
      --categories injection,protocol,quality  --cases qual_extract_facts  --report-dir validation_reports

Differences from the free-tier run: no rate limits and no spend, the internal-labelled case is RUN (a local model
may see internal data) instead of being expected to block, and models are tried one at a time so a 4 GB GPU only ever
holds one. Pull models first with `ollama pull <name>`."""
from __future__ import annotations

import argparse
import os
import sys
import time
from typing import List, Optional

from ..model_policy import PolicyRouter
from ..providers.ollama_local import OllamaLocalProvider, list_ollama_models
from .cases import build_cases
from .harness import SuiteResult, run_suite
from .metered import MeteredProvider
from .report import to_json, to_markdown

SMOKE_CASES = ("proto_single_tool", "inj_task_input_untrusted", "qual_extract_facts")


def _bool(v: str) -> Optional[bool]:
    return {"true": True, "1": True, "yes": True, "false": False, "0": False, "no": False}.get(v.strip().lower())


def run_models(args, make_provider=OllamaLocalProvider, out=print) -> List[SuiteResult]:
    cases = build_cases()
    if args.smoke:
        cases = [c for c in cases if c.id in SMOKE_CASES]
    if args.categories:
        keep = set(args.categories.split(","))
        cases = [c for c in cases if c.category in keep]
    if args.cases:
        keep = set(args.cases.split(","))
        cases = [c for c in cases if c.id in keep]
    suites: List[SuiteResult] = []
    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        provider = make_provider(name, args.host, timeout_seconds=args.timeout, max_output_tokens=args.max_output_tokens,
                                 num_ctx=args.num_ctx, json_mode=not args.no_json_mode,
                                 think=_bool(args.think) if args.think else None)
        meter = MeteredProvider(provider, max_calls=args.max_calls)
        router = PolicyRouter([meter], [])
        out(f"\n== {name}: {len(cases)} cases x {args.repeats} repeat(s); the first call loads the model and is slow ==")
        n = [0]

        def progress(r, model=name):
            n[0] += 1
            out(f"  [{model}] {n[0]:>3} {r.case_id}#{r.repeat}: {r.outcome} ({r.latency_s:.0f}s)"
                + (f" - {'; '.join(r.reasons)[:90]}" if r.reasons else ""))

        suites.append(run_suite(f"ollama:{name}", cases, router, repeats=args.repeats, ceiling=None,
                                metered=[meter], real=True, progress=progress, abort_after_infra=3))
    return suites


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m orchestration.validation.local_cmd", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--models", default="")
    ap.add_argument("--host", default=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--categories", default="")
    ap.add_argument("--cases", default="")
    ap.add_argument("--num-ctx", type=int, default=4096, dest="num_ctx")
    ap.add_argument("--max-output-tokens", type=int, default=512, dest="max_output_tokens")
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--max-calls", type=int, default=600, dest="max_calls")
    ap.add_argument("--no-json-mode", action="store_true", dest="no_json_mode",
                    help="do not ask Ollama to constrain output to valid JSON")
    ap.add_argument("--think", default="", help="true|false; send to reasoning models (false saves the output budget)")
    ap.add_argument("--report-dir", default="validation_reports", dest="report_dir")
    args = ap.parse_args(argv)
    if args.list:
        try:
            rows = list_ollama_models(args.host)
        except Exception as e:  # noqa: BLE001
            print(f"Cannot reach Ollama at {args.host}: {type(e).__name__}: {e}\nStart it with `ollama serve`.")
            return 1
        print(f"{len(rows)} model(s) at {args.host}:")
        for r in rows:
            print(f"  {r['id']}  {r['params'] or '?'}  {r['quant'] or '?'}  {r['size_gb']} GB")
        return 0
    if not args.models:
        print("Pass --models name[,name...] (see --list), or --list."); return 2
    suites = run_models(args)
    os.makedirs(args.report_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    meta = {"mode": "local ollama (internal data allowed)", "repeats": args.repeats, "cases": len({r.case_id for r in suites[0].runs}) if suites else 0}
    md, js = (os.path.join(args.report_dir, f"validation_local_{stamp}.{ext}") for ext in ("md", "json"))
    open(md, "w", encoding="utf-8").write(to_markdown(suites, meta))
    open(js, "w", encoding="utf-8").write(to_json(suites, meta))
    print(f"\nReport: {md}\nData:   {js}")
    for s in suites:
        print(f"  {s.label}: {s.verdict}" + "".join(f"\n    - {r}" for r in s.reasons))
    return 0 if all(s.verdict == "PASS" for s in suites) else 1


if __name__ == "__main__":
    sys.exit(main())
