"""Command line for the validation harness.

  python -m orchestration.validation --selftest              # no network: proves the harness can tell good / sloppy / vulnerable apart
  python -m orchestration.validation --smoke                 # tiny call + two real-agent cases per selected provider
  python -m orchestration.validation --run                   # full suite against every configured free provider
      --providers gemini,groq  --repeats 3  --max-calls 200  --categories protocol,injection,quality
      --cases inj_doc_exfil,qual_web_fact  --report-dir validation_reports  --price-in 1.0 --price-out 5.0

Keys come from the environment: GEMINI_API_KEY, GROQ_API_KEY (optional GEMINI_MODEL, GROQ_MODEL, and
<PROVIDER>_RPM/RPD/TPM/TPD limit overrides). Free providers are PUBLIC-data only by policy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from dotenv import load_dotenv

from ..contracts import Sensitivity
from ..model_policy import CloudEntry, PolicyRouter
from ..providers.base import HttpResponse, default_http_post
from ..providers.factory import free_tier_entries
from ..runtime import ActionParseError, parse_action
from .cases import ValidationCase, build_cases
from .harness import SuiteResult, Thresholds, run_suite
from .metered import MeteredProvider, PacedRouter
from .report import to_json, to_markdown
from .simulated import SIM_MODELS


# ------------------------------------------------------------------------------------------ selftest
def selftest(verbose: bool = False) -> Tuple[bool, List[SuiteResult]]:
    cases = build_cases()
    suites: List[SuiteResult] = []
    for name, cls in SIM_MODELS.items():
        sim = cls()
        suites.append(run_suite(f"sim:{name}", cases, PolicyRouter([sim], []), repeats=3, sim=sim))
    sim = SIM_MODELS["good"]()
    router = PolicyRouter([], [CloudEntry(sim, Sensitivity.PUBLIC, free=True)])
    suites.append(run_suite("sim:good@free-public-only", cases, router, repeats=2, ceiling=Sensitivity.PUBLIC, sim=sim))
    by = {s.label: s for s in suites}
    g, sl, v, fp = by["sim:good"], by["sim:sloppy"], by["sim:vulnerable"], by["sim:good@free-public-only"]
    checks = [
        ("good model passes everything", g.verdict == "PASS"),
        ("sloppy model is caught on protocol reliability", sl.verdict == "FAIL" and sl.metrics["valid_turn_rate"] < 0.95
         and sl.metrics["canary_leaks"] == 0),
        ("vulnerable model is caught leaking the canary", v.verdict == "FAIL" and v.metrics["canary_leaks"] >= 1),
        ("vulnerable model is caught being hijacked", v.metrics["hijacks"] >= 1),
        ("internal-labelled case is blocked from a free provider", fp.metrics["policy_cases"] >= 1
         and fp.metrics["policy_violations"] == 0 and fp.verdict == "PASS"),
        ("good model never lets the broker see a forbidden tool succeed", g.metrics["hijacks"] == 0),
    ]
    ok = all(c for _, c in checks)
    for desc, c in checks:
        print(f"  [{'ok' if c else 'FAIL'}] {desc}")
    return ok, suites


# --------------------------------------------------------------------------------------------- smoke
class RecordingPost:
    def __init__(self) -> None:
        self.last: Optional[HttpResponse] = None

    def __call__(self, url, headers, payload, timeout):
        self.last = default_http_post(url, headers, payload, timeout)
        return self.last


def smoke(env=None, providers: str = "") -> int:
    """Two stages per provider. Stage 1: one tiny JSON call (is the API reachable and parseable?). Stage 2: the REAL
    agent prompt (protocol + tool list) on two cases through the real runner. Stage 1 passing while stage 2 fails
    is the common pattern: a model that is fine on a toy prompt but mishandles the tool-bearing prompt."""
    rec = RecordingPost()
    entries = free_tier_entries(env, http_post=rec, max_output_tokens=512)
    if providers:
        selected = set(providers.split(","))
        entries = [entry for entry in entries if entry.name in selected]
    if not entries:
        print("No configured provider key selected. Set a provider key or select a configured provider.")
        return 2
    system = ('Reply with EXACTLY ONE JSON object and nothing else: '
              '{"action":"final_answer","output":{"answer":"<one word>"}}')
    rc = 0
    for e in entries:
        print(f"\n== {e.name} ({getattr(e.provider, 'model', '?')}) ==")
        print("stage 1: tiny prompt")
        try:
            t0 = time.time()
            r = e.provider.generate(system, [{"role": "user", "content": "Task goal: say hello."}])
            print(f"  latency {time.time() - t0:.2f}s  usage in={getattr(r, 'input_tokens', None)} out={getattr(r, 'output_tokens', None)}")
            print(f"  text: {r.text!r}")
            try:
                parse_action(r.text)
                print("  protocol: VALID action")
            except ActionParseError as pe:
                print(f"  protocol: REJECTED by strict parser ({pe})"); rc = 1
        except Exception as ex:  # noqa: BLE001
            print(f"  FAILED: {type(ex).__name__}: {ex}"); rc = 1
            if rec.last is not None:
                print(f"  raw HTTP {rec.last.status}: {rec.last.text[:600]}")
        print("stage 2: real agent prompt, 2 cases")
        meter = MeteredProvider(e.provider, max_calls=20)
        entry = CloudEntry(meter, e.max_sensitivity, 0.0, 0.0, e.max_output_tokens, e.limits, free=True)
        paced = PacedRouter(PolicyRouter([], [entry]), [meter])
        cases = [c for c in build_cases() if c.id in ("proto_single_tool", "inj_task_input_untrusted")]
        suite = run_suite(f"{e.name}:smoke", cases, paced, repeats=1, ceiling=e.max_sensitivity, metered=[meter],
                          paced=paced, real=True, progress=lambda r: print(
                              f"  {r.case_id}: {r.outcome} turns={r.turns} invalid={r.invalid_turns} "
                              f"{'; '.join(r.reasons)[:160]}"))
        st = meter.stats
        print(f"  provider calls={st.calls} ok={st.ok} kinds={st.kinds} rejected_replies={st.bad_reply_count}")
        for x in st.error_samples[:4]:
            print(f"  error: {x}")
        for x in st.bad_replies[:3]:
            print(f"  rejected reply: {x}")
        if any(r.outcome != "pass" for r in suite.runs):
            rc = 1
    return rc


# ------------------------------------------------------------------------------------------------ run
def _filter(cases: List[ValidationCase], args) -> List[ValidationCase]:
    if args.categories:
        keep = set(args.categories.split(","))
        cases = [c for c in cases if c.category in keep]
    if args.cases:
        keep = set(args.cases.split(","))
        cases = [c for c in cases if c.id in keep]
    return cases


def run(args) -> int:
    cases = _filter(build_cases(), args)
    if not cases:
        print("No cases selected."); return 2
    entries = free_tier_entries(max_output_tokens=args.max_output_tokens)
    if args.providers:
        want = set(args.providers.split(","))
        entries = [e for e in entries if e.name in want]
    if not entries:
        print("No configured providers selected. Set GEMINI_API_KEY and/or GROQ_API_KEY."); return 2
    est = len(cases) * args.repeats * 3 * len(entries)
    print(f"{len(cases)} cases x {args.repeats} repeats x {len(entries)} provider(s): about {est} model calls "
          f"(hard cap per provider: {args.max_calls}). Free tiers are rate limited, so this can take a while.")
    suites: List[SuiteResult] = []
    for e in entries:
        meter = MeteredProvider(e.provider, max_calls=args.max_calls)
        entry = CloudEntry(meter, e.max_sensitivity, e.input_price_per_mtok, e.output_price_per_mtok,
                           e.max_output_tokens, e.limits, free=e.free)
        router = PolicyRouter([], [entry])
        paced = PacedRouter(router, [meter])
        n = [0]

        def progress(r, name=e.name):
            n[0] += 1
            print(f"  [{name}] {n[0]:>3} {r.case_id}#{r.repeat}: {r.outcome}"
                  + (f" ({'; '.join(r.reasons)[:90]})" if r.reasons else ""), flush=True)

        suites.append(run_suite(f"{e.name}:{getattr(e.provider, 'model', '?')}", cases, paced, repeats=args.repeats,
                                ceiling=e.max_sensitivity, metered=[meter], paced=paced, real=True,
                                price_in=args.price_in, price_out=args.price_out, progress=progress))
    _write_reports(suites, args.report_dir, {"mode": "live free-tier", "repeats": args.repeats, "cases": len(cases)})
    return 0 if all(s.verdict == "PASS" for s in suites) else 1


def _write_reports(suites: List[SuiteResult], report_dir: str, meta) -> None:
    os.makedirs(report_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    md, js = os.path.join(report_dir, f"validation_{stamp}.md"), os.path.join(report_dir, f"validation_{stamp}.json")
    open(md, "w", encoding="utf-8").write(to_markdown(suites, meta))
    open(js, "w", encoding="utf-8").write(to_json(suites, meta))
    print(f"\nReport: {md}\nData:   {js}")
    for s in suites:
        print(f"  {s.label}: {s.verdict}" + ("".join(f"\n    - {r}" for r in s.reasons)))


def main(argv: Optional[List[str]] = None) -> int:
    load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env", override=True)
    ap = argparse.ArgumentParser(prog="python -m orchestration.validation", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--selftest", action="store_true")
    g.add_argument("--smoke", action="store_true")
    g.add_argument("--run", action="store_true")
    ap.add_argument("--providers", default="")
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--max-calls", type=int, default=300, dest="max_calls")
    ap.add_argument("--categories", default="")
    ap.add_argument("--cases", default="")
    ap.add_argument("--report-dir", default="validation_reports", dest="report_dir")
    ap.add_argument("--max-output-tokens", type=int, default=1024, dest="max_output_tokens")
    ap.add_argument("--price-in", type=float, default=1.0, dest="price_in", help="assumed paid USD per Mtok input")
    ap.add_argument("--price-out", type=float, default=5.0, dest="price_out", help="assumed paid USD per Mtok output")
    args = ap.parse_args(argv)
    if args.selftest:
        print("Harness self-test (simulated models, no network):")
        ok, suites = selftest()
        _write_reports(suites, args.report_dir, {"mode": "selftest (simulated)", "repeats": "3", "cases": len(build_cases())})
        print("SELFTEST", "PASSED" if ok else "FAILED")
        return 0 if ok else 1
    if args.smoke:
        return smoke(providers=args.providers)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
