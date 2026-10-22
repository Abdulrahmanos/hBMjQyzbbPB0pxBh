"""Local commands for reviewing configuration and simulated state."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from decimal import InvalidOperation

from . import __version__
from .config import ConfigError, load_config
from .storage import StorageError, initialize_database, read_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bitcoin Trading Agent: local paper research")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", default=None, help="Non-secret JSON configuration")
    parser.add_argument("--db", default=None, help="Simulated portfolio database")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-config", help="Validate settings and display simulated allocations")
    commands.add_parser("init-db", help="Initialize simulated cash once; preserve existing balances")
    commands.add_parser("status", help="Read the current simulated portfolio")
    fetch = commands.add_parser("fetch-data", help="Download public, completed hourly BTC-USD candles")
    fetch.add_argument("--start", required=True, help="Inclusive UTC date or timestamp")
    fetch.add_argument("--end", required=True, help="Exclusive UTC date or timestamp")
    fetch.add_argument("--output", default="data/raw/btc_usd_1h.csv")
    research = commands.add_parser("research", help="Run offline baselines and chronological allocation experiments")
    research.add_argument("--data", default="data/raw/btc_usd_1h.csv")
    research.add_argument("--settings", default="config/research.json")
    research.add_argument("--output", default="reports/milestone-2")
    render = commands.add_parser("render-report", help="Render saved research results without refitting models")
    render.add_argument("--data", default="data/raw/btc_usd_1h.csv")
    render.add_argument("--output", default="reports/milestone-2")
    commands.add_parser("paper-init", help="Initialize persistent operational paper state")
    commands.add_parser("paper-status", help="Inspect paper positions, orders, and risk state")
    commands.add_parser("paper-audit", help="Reconcile decimal balances against fills")
    liquidate = commands.add_parser("paper-liquidate", help="Request simulated exits and an owner pause")
    liquidate.add_argument("--reason", required=True)
    resume = commands.add_parser("paper-resume", help="Resume a flat paper portfolio after cooldown and review")
    resume.add_argument("--reason", required=True)
    step = commands.add_parser("paper-step", help="Process a saved quote and optional feature snapshot")
    step.add_argument("--quote", required=True)
    step.add_argument("--features")
    step.add_argument("--at", help="Explicit UTC observation clock for offline replay")
    run = commands.add_parser("run-paper", help="Observe public quotes and run simulated trades")
    run.add_argument("--cycles", type=int, default=1, help="Quote cycles; 0 runs until Ctrl+C")
    run.add_argument("--history", default="data/raw/btc_usd_1h.csv")
    run.add_argument("--runtime", default="var/live")
    run.add_argument("--integrations", help="Optional nonsecret integration settings; demo mode is recommended")
    run.add_argument("--output", default="reports/milestone-3/live-demo.json", help="Save the completed run summary")
    stress = commands.add_parser("stress-test", help="Exercise paper risk controls on constructed price paths")
    stress.add_argument("--output", default="reports/milestone-3")
    demo = commands.add_parser("integration-demo", help="Create a local inbox from saved paper fills and weekly marks")
    demo.add_argument("--settings", default="config/integrations.demo.json")
    demo.add_argument("--output", default="reports/milestone-4/demo-summary.json")
    report = commands.add_parser("report-preview", help="Save a weekly report without external delivery")
    report.add_argument("--end", help="UTC period end; default latest scheduled weekly report")
    report.add_argument("--output", default="reports/milestone-4/report-preview.json")
    outbox = commands.add_parser("outbox-status", help="Read persistent message statuses")
    retry = commands.add_parser("outbox-retry", help="Explicitly retry a blocked or uncertain message; may duplicate an uncertain send")
    retry.add_argument("--id", required=True)
    supervisor = commands.add_parser("supervise-paper", help="Supervise the seven-day local public-data paper worker")
    worker = commands.add_parser("paper-worker", help="Internal single-writer forward worker; public data only")
    worker.add_argument("--cycles", type=int, default=0)
    worker.add_argument("--supervisor-token", help=argparse.SUPPRESS)
    for command in (supervisor, worker):
        command.add_argument("--runtime", default="var/forward")
        command.add_argument("--settings", default="config/integrations.forward.json")
    health = commands.add_parser("health", help="Read live local supervisor/worker freshness without network access")
    health.add_argument("--runtime", default="var/forward")
    evaluation = commands.add_parser("forward-report", help="Refresh the prospective evaluation report from actual observations")
    evaluation.add_argument("--output", default="var/forward/reports")
    commands.add_parser("forward-audit", help="Reconcile the candidate and concurrent baseline ledgers")
    backup = commands.add_parser("backup-paper", help="Create and verify a new consistent forward SQLite snapshot")
    backup.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    operational = args.command in {"paper-init", "paper-status", "paper-audit", "paper-liquidate", "paper-resume", "paper-step", "run-paper", "stress-test", "integration-demo", "report-preview", "outbox-status", "outbox-retry"}
    forward = args.command in {"supervise-paper", "paper-worker", "health", "forward-report", "forward-audit", "backup-paper"}
    args.config = args.config or ("config/forward.json" if forward else "config/demo.json" if args.command == "integration-demo" else "config/paper.json" if operational else "config/default.json")
    args.db = args.db or ("var/forward/ledger.sqlite3" if forward else "var/operational.sqlite3" if operational else "var/paper.sqlite3")
    try:
        if forward:
            from .runtime import backup_database, run_worker, runtime_health, supervise
            from .forward import audit_forward, write_forward_report
            if args.command == "supervise-paper":
                result = supervise(args.config, args.db, args.runtime, args.settings)
            elif args.command == "paper-worker":
                result = run_worker(args.config, args.db, args.runtime, args.settings, cycles=args.cycles, supervisor_token=args.supervisor_token)
            elif args.command == "health":
                result = runtime_health(args.runtime)
                print(json.dumps(result, indent=2))
                return 0 if result["healthy"] else 1
            elif args.command == "forward-report":
                result = write_forward_report(args.db, args.output)
            elif args.command == "forward-audit":
                result = audit_forward(args.db)
            else:
                result = backup_database(args.db, args.output)
        elif args.command in {"integration-demo", "report-preview", "outbox-status", "outbox-retry"}:
            from datetime import datetime, timezone
            from pathlib import Path
            from .notifications import outbox_status, report_boundary, retry_message, weekly_report
            from .paper_types import utc
            now = datetime.now(timezone.utc)
            config = load_config(args.config)
            if args.command == "integration-demo":
                from .integrations import IntegrationCoordinator, load_integration_settings
                from .paper_store import audit_paper, initialize_paper
                initialize_paper(args.db, config)
                coordinator = IntegrationCoordinator(args.db, config, load_integration_settings(args.settings))
                result = coordinator.poll(now=now)
                result = {"config_refresh": result["config_refresh"], "delivery": result["delivery"], **coordinator.preview(), "audit": audit_paper(args.db)}
            elif args.command == "report-preview":
                result = weekly_report(args.db, utc(args.end) if args.end else report_boundary(now, config.data["reporting"]), now)
            elif args.command == "outbox-status":
                result = outbox_status(args.db)
            else:
                retry_message(args.db, args.id, now)
                result = {"id": args.id, "status": "pending", "uncertain_send_may_duplicate": True}
            if hasattr(args, "output"):
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        elif args.command in {"paper-liquidate", "paper-resume"}:
            from .paper_engine import PaperEngine
            engine = PaperEngine(args.db, load_config(args.config))
            result = engine.request_liquidation(args.reason) if args.command == "paper-liquidate" else engine.manual_resume(args.reason)
        elif args.command == "stress-test":
            from .stress_tests import run_stress
            result = run_stress(load_config(args.config), args.output, progress=lambda value: print(value, flush=True))
        elif args.command == "paper-status":
            from .paper_store import paper_status
            result = paper_status(args.db)
        elif args.command == "paper-audit":
            from .paper_store import audit_paper
            result = audit_paper(args.db)
        elif args.command == "paper-init":
            from .paper_store import initialize_paper, paper_status
            initialize_paper(args.db, load_config(args.config))
            result = paper_status(args.db)
        elif args.command == "paper-step":
            from pathlib import Path
            from .paper_engine import PaperEngine
            from .paper_types import Features, Quote, utc
            quote = Quote.from_dict(json.loads(Path(args.quote).read_text(encoding="utf-8")))
            features = Features.from_dict(json.loads(Path(args.features).read_text(encoding="utf-8"))) if args.features else None
            result = PaperEngine(args.db, load_config(args.config)).tick(quote, features, utc(args.at) if args.at else None)
        elif args.command == "run-paper":
            from pathlib import Path
            from .paper_service import run_paper
            settings = None
            if args.integrations:
                from .integration_io import load_env
                from .integrations import load_integration_settings
                load_env()
                settings = load_integration_settings(args.integrations)
            result = run_paper(args.db, load_config(args.config), cycles=args.cycles, history=args.history, runtime=args.runtime,
                               progress=lambda value: print(value, flush=True), integration_settings=settings)
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        elif args.command == "fetch-data":
            from .market_data import fetch_candles

            result = fetch_candles(args.start, args.end, args.output, progress=lambda value: print(value, flush=True))
        elif args.command == "research":
            from .research import run_research

            result = run_research(args.data, args.output, load_config(args.config), args.settings,
                                  progress=lambda value: print(value, flush=True))
        elif args.command == "render-report":
            from .research_report import render_saved_report

            result = render_saved_report(args.output, args.data)
        elif args.command == "status":
            result = read_status(args.db)
        else:
            config = load_config(args.config)
            if args.command == "validate-config":
                result = {"valid": True, "execution_mode": "paper", "initial_cash_usd": format(config.initial_cash, ".2f"),
                          "allocations_usd": {key: format(value, ".2f") for key, value in config.initial_allocations().items()},
                          "config_checksum": config.checksum,
                          "capabilities": ["configuration_validation", "simulated_cash_initialization", "persistent_status"]}
            else:
                created = initialize_database(args.db, config)
                result = {"initialized": created, "existing_balances_preserved": not created, **read_status(args.db)}
        print(json.dumps(result, indent=2))
        return 0
    except (ConfigError, StorageError, OSError, sqlite3.Error, ArithmeticError, ValueError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
