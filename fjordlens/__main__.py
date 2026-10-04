import argparse
import json
import os
import sys
import threading
from pathlib import Path
from .runner import run_batch

def main():
    parser = argparse.ArgumentParser(prog="fjordlens", description="Exact-entity Norwegian company research")
    subs = parser.add_subparsers(dest="command", required=True)
    run = subs.add_parser("run", help="Research a batch; no package installation or API key required")
    run.add_argument("--input", "--organisations", dest="input", required=True,
                     help="JSONL/JSON/CSV/text list of organisation numbers supplied by the evaluator")
    run.add_argument("--output", default="out/run")
    run.add_argument("--previous")
    run.add_argument("--replay", help="Folder of immutable snapshots; disables live network access")
    run.add_argument("--registry-snapshot", help="Frozen official identity source-bundle folder; other connectors remain live")
    run.add_argument("--cutoff", help="Latest allowed evidence retrieval time, UTC ISO-8601")
    run.add_argument("--workers", type=int, default=None, help="Concurrent website workers (default 32)")
    run.add_argument("--max-requests", type=int, default=None, help="Total HTTP attempts (default: SIGNALPOST_MAX_REQUESTS or 30 per company)")
    run.add_argument("--seconds", type=float, default=None, help="Wall-clock budget (default: SIGNALPOST_TIME_BUDGET_SECONDS or 2600)")
    run.add_argument("--per-company", type=int, default=None, help="HTTP attempts per company (default 40)")
    run.add_argument("--fresh", action="store_true", help="Do not treat an earlier run in --output as the previous snapshot")
    run.add_argument("--max-pages", type=int, default=7)
    run.add_argument("--run-id")
    serve = subs.add_parser("serve", help="Open the local evidence workspace")
    serve.add_argument("--data", default="submission/envelopes.jsonl.gz")
    serve.add_argument("--port", type=int, default=8787)
    ask = subs.add_parser("ask", help="Answer a question about one company with cited facts from saved evidence")
    ask.add_argument("--profile", help="A single profile JSON file")
    ask.add_argument("--data", help="An envelopes.jsonl(.gz) file (use with --org)")
    ask.add_argument("--org", help="Organisation number inside --data")
    ask.add_argument("question")
    screen = subs.add_parser("screen", help="Screen companies with an inspectable filter plan, e.g. 'companies in Oslo with more than 5 employees top 10 by revenue'")
    screen.add_argument("--data", required=True, help="An envelopes.jsonl(.gz) file")
    screen.add_argument("query")
    args = parser.parse_args()
    if args.command == "run":
        for name in ("workers", "max_requests", "seconds", "per_company"):
            if getattr(args, name) is not None and getattr(args, name) <= 0:
                parser.error(f"--{name.replace('_', '-')} must be positive")
        report = run_batch(args.input, args.output, run_id=args.run_id, previous=args.previous, replay=args.replay,
                           cutoff=args.cutoff, registry_snapshot=args.registry_snapshot, workers=args.workers, max_requests=args.max_requests, seconds=args.seconds,
                           per_company=args.per_company, max_pages=args.max_pages, fresh=args.fresh)
        print(json.dumps({k: v for k, v in report.items() if k != "family_company_coverage"} | {"family_company_coverage": report["family_company_coverage"]}, ensure_ascii=True, indent=2))
        sys.stdout.flush()
        # Every envelope is written; a source failure is data, not a process failure. Exit without
        # waiting for any straggling network thread so the evaluator's wall clock is respected.
        code = 0 if report["terminal_envelopes"] == report["input_count"] else 1
        if threading.active_count() > 1:
            os._exit(code)
        raise SystemExit(code)
    elif args.command == "serve":
        from .server import serve as start_server
        start_server(Path(args.data), args.port)
    else:
        from .research import answer_profile, screen_profiles
        def load(path):
            import gzip
            raw = gzip.decompress(Path(path).read_bytes()).decode("utf-8") if str(path).endswith(".gz") else Path(path).read_text(encoding="utf-8")
            return [json.loads(line) for line in raw.splitlines() if line.strip()]
        if args.command == "screen":
            print(json.dumps(screen_profiles(load(args.data), args.query), ensure_ascii=False, indent=2))
        else:
            if args.profile:
                envelope = json.loads(Path(args.profile).read_text(encoding="utf-8"))
            elif args.data and args.org:
                envelope = next((row for row in load(args.data) if row.get("organisation_number") == args.org), None)
                if envelope is None:
                    parser.error(f"organisation number {args.org} is not in {args.data}")
            else:
                parser.error("ask needs --profile, or --data with --org")
            print(json.dumps(answer_profile(envelope, args.question), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
