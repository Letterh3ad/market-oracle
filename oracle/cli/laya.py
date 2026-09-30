from __future__ import annotations

import argparse

from oracle.services.context import AppContext
from oracle.services.laya_history_service import plan_history, score_history
from oracle.services.laya_service import laya_scores, score_company
from oracle.services.laya_test_service import LayaTest, keep_laya_test, laya_tests, run_laya_test
from oracle.services.watch_service import DEFAULT_LIST

UNCALIBRATED = "probabilities are uncalibrated"


def register(parser: argparse.ArgumentParser) -> None:
    parser.description = "Laya, a small local model that scores each stored headline for a company"
    sub = parser.add_subparsers(dest="laya_command", required=True)
    score = sub.add_parser("score", help="score the company's stored articles not yet scored by this model")
    score.add_argument("symbol")
    score.set_defaults(handler=score_cmd)
    show = sub.add_parser("show", help="the company's scored articles, newest first")
    show.add_argument("symbol")
    show.set_defaults(handler=show_cmd)
    history = sub.add_parser("history", help="score the backfilled history per (article, target) pair (hours; resumes)")
    history.add_argument("--list", default=DEFAULT_LIST)
    history.add_argument("--plan", action="store_true", help="count the pairs and estimate the time, scoring nothing")
    history.add_argument("--own-only", action="store_true", help="only each company's own news (a measuring run)")
    history.add_argument("--only", nargs="+", metavar="SYMBOL", help="only these main-list companies (a measuring run)")
    history.add_argument("--allow-cpu", action="store_true",
                         help="score on the CPU when the GPU has no room (about 40 times slower); else it waits")
    history.set_defaults(handler=history_cmd)
    test = sub.add_parser("test", help="the pre-registered predictive test over the scored history")
    test.add_argument("--list", default=DEFAULT_LIST)
    test.add_argument("--sensitivity", action="store_true", help="links as known today, 03's supplier and customer links in")
    test.add_argument("--show", action="store_true", help="print the kept results, running nothing")
    test.set_defaults(handler=test_cmd)


def score_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    run = score_company(ctx, args.symbol, on_progress=lambda line: print(line, flush=True))
    where = f" on {run.device}" if run.device else ""
    print(f"{run.symbol}: {run.scored} scored{where}, {run.already} already scored, of {run.articles} articles "
          f"({run.seconds:.1f} s, {run.model})")
    return 0


def show_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    scores = laya_scores(ctx, args.symbol)
    if not scores:
        print(f"no Laya scores for {args.symbol}; run `oracle laya score {args.symbol}`")
        return 0
    print(f"{len(scores)} articles; {UNCALIBRATED}")
    for s in scores:
        day = s.known_at[:10] + ("" if s.dated else " (fetched)")
        print(f"{day}  relevant {s.relevant:.2f}  {s.direction:<4} {s.direction_probs[s.direction]:.2f}  "
              f"{s.event} {s.event_prob:.2f}  {s.title}")
    return 0


def history_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    kw = dict(only=args.only, own_only=args.own_only)
    plan = plan_history(ctx, args.list, **kw)
    rel = ", ".join(f"{r} {n}" for r, n in plan.by_relation.items())
    print(f"{plan.pairs} pairs for {plan.targets} companies in {plan.groups} groups ({rel}); {plan.unscored} not scored; "
          f"about {plan.estimate_s / 3600:.1f} h at {plan.ms_per_pair} ms a pair ({plan.basis})")
    if args.plan:
        return 0
    run = score_history(ctx, args.list, allow_cpu=args.allow_cpu, on_progress=lambda line: print(line, flush=True),
                        **kw)
    print(run.summary)
    return 1 if run.stopped else 0


def test_cmd(ctx: AppContext, args: argparse.Namespace) -> int:
    if args.show:
        kept = laya_tests(ctx)
        if not kept:
            print("no Laya test kept yet; run `oracle laya test`")
        for t in kept:
            print(format_test(t))
        return 0
    t = keep_laya_test(ctx, run_laya_test(ctx, args.list, sensitivity=args.sensitivity,
                                          on_progress=lambda line: print(line, flush=True)))
    print(format_test(t))
    return 0


def _num(x: float | None, fmt: str = "+.4f") -> str:
    return "n/a" if x is None else format(x, fmt)


def format_test(t: LayaTest) -> str:
    lines = [f"Laya predictive test, {t.label}: {t.verdict}",
             f"model {t.model}; {t.start.isoformat()} to {t.end.isoformat()}; {t.companies} companies; pairs counted "
             + ", ".join(f"{r} {n}" for r, n in t.articles.items()),
             f"t-stat: {t.t_method}"]
    for h in t.horizons:
        beats = {True: "beats", False: "does not beat", None: "cannot be set against"}
        lines += ["", f"{h.horizon}-day excess return (features over {h.lookback} days): {h.verdict}: IC {_num(h.ic)}, "
                      f"t {_num(h.t, '.2f')}, {h.days} IC days, positive in {h.positive} of {h.counted} counted segments",
                  f"  news-attention baseline (own_attention, sign fitted): IC {_num(h.baseline_ic)}, "
                  f"t {_num(h.baseline_t, '.2f')}; Laya {beats[h.beats_baseline]} it",
                  f"  momentum baseline (own_momentum, sign fitted): IC {_num(h.momentum_ic)}, "
                  f"t {_num(h.momentum_t, '.2f')}; Laya {beats[h.beats_momentum]} it",
                  "  Laya counts as useful only if it beats both baselines; the pass bar above is separate.",
                  f"  {'segment':<24} {'IC':>8} {'t':>6} {'days':>5}  counted  weights (fitted on trailing data)"]
        for s in h.segments:
            w = " ".join(f"{k.removesuffix('_mean')}:{v:+.2f}" for k, v in s.weights.items() if v)
            lines.append(f"  {s.start.isoformat()} to {s.end.isoformat()} {_num(s.ic):>8} {_num(s.t, '.2f'):>6} "
                         f"{s.days:>5}  {'yes' if s.counted else 'no: ' + (s.why or '')}  {w}")
        lines.append(f"  {'feature (as is, counted days)':<32} {'IC':>8} {'t':>6} {'days':>5}  segments positive")
        for f in h.features:
            tag = "" if f.in_score else "  (secondary)"
            lines.append(f"  {f.name:<32} {_num(f.ic):>8} {_num(f.t, '.2f'):>6} {f.days:>5}  {f.positive}/{h.counted}{tag}")
    lines += ["", t.caveat]
    return "\n".join(lines)
