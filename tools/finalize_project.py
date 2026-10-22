"""Create a dated review; a final report requires the actual frozen forward week."""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitcoin_agent.config import load_config
from bitcoin_agent.forward import environment_versions, forward_summary, source_checksum
from bitcoin_agent.paper_store import connection
from bitcoin_agent.runtime import backup_database, read_json


def completion_checks(summary, audit, *, paired, code, config, environment, stopped):
    manifest = summary.get("manifest", {})
    days = summary.get("days", [])
    return {
        "prospective_week_complete": summary.get("seven_day_evidence_complete") is True,
        "seven_individually_qualifying_days": len(days) == 7 and all(
            day.get("complete_24_hours") and day.get("qualifies")
            and day.get("quote_coverage_fraction", 0) >= manifest.get("minimum_daily_quote_coverage", .99)
            for day in days),
        "candidate_and_three_baselines_reconcile": audit.get("passed") is True
            and audit.get("baseline_books_reconciled") == 3 and audit.get("candidate", {}).get("passed") is True,
        "all_accepted_quotes_paired": paired,
        "same_application_source": manifest.get("code_checksum") == code,
        "same_configuration": manifest.get("config_checksum") == config,
        "same_calculation_environment": manifest.get("environment") == environment,
        "public_stream_provenance": manifest.get("source") == "coinbase_public_stream"
            and manifest.get("historical_candles_count_as_forward") is False,
        "no_observer_or_config_incident": summary.get("observer_failures") == 0
            and summary.get("changed_config_checksums") == [],
        "window_closed_and_worker_stopped": summary.get("status") == "seven_day_window_closed" and stopped,
    }


def create_review(runtime, output, require_complete=False):
    runtime, output = Path(runtime).resolve(), Path(output).resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    snapshot = ROOT / "var/completion/snapshots" / (stamp + ".sqlite3")
    backup = backup_database(runtime / "ledger.sqlite3", snapshot)
    summary = forward_summary(snapshot)
    with connection(snapshot) as database:
        missing = database.execute("SELECT COUNT(*) FROM paper_quotes q LEFT JOIN forward_observations o ON o.quote_id=q.id WHERE q.accepted=1 AND o.quote_id IS NULL").fetchone()[0]
        extra = database.execute("SELECT COUNT(*) FROM forward_observations o LEFT JOIN paper_quotes q ON q.id=o.quote_id WHERE q.id IS NULL OR q.accepted!=1").fetchone()[0]
    supervisor, worker = read_json(runtime / "supervisor.json") or {}, read_json(runtime / "worker.json") or {}
    config = load_config(ROOT / "config/forward.json")
    checks = completion_checks(summary, backup["audit"], paired=missing == extra == 0,
                               code=source_checksum(), config=config.checksum, environment=environment_versions(),
                               stopped=(runtime / "COMPLETE").exists() and supervisor.get("state") == worker.get("state") == "stopped")
    complete = all(checks.values())
    if require_complete and not complete:
        raise ValueError("Finalization withheld: " + ", ".join(key for key, passed in checks.items() if not passed))
    output.mkdir(parents=True, exist_ok=True)
    verification = {"generated_at": summary["generated_at"], "final_evaluation_complete": complete,
                    "checks": checks, "snapshot_sha256": backup["sha256"], "source_checksum": source_checksum(),
                    "config_checksum": config.checksum, "unpaired_accepted_quotes": missing,
                    "uncommitted_forward_quotes": extra, "ledger_audit": backup["audit"]}
    (output / "evaluation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output / "verification.json").write_text(json.dumps(verification, indent=2) + "\n", encoding="utf-8")
    lines = ["# Bitcoin paper evaluation", "", "Status: **" + ("completed, independently checked forward week" if complete else "incomplete; interim observations only") + "**", "",
             "Generated: " + summary["generated_at"], "", "## Research question", "",
             "Assuming Bitcoin remains operational and tradable, does the declared defensive accumulation policy improve returns and drawdown relative to simpler policies? Historical research and this revised forward policy are separate experiments.", "",
             "## Evaluation protocol", "",
             "Fresh $10,000 simulated accounts, identical prospective observations, idle cash included, 0.6% assumed fees and 0.1% adverse slippage per fill. Public bid/ask and product increments enter execution. The artificial $1,000-per-quote fill cap is not measured liquidity. Open holdings are marked at the midpoint without a hypothetical final sale. Cash earns no interest.", "",
             "Only actually received quotes count. Each of seven anchored 24-hour periods must reach 99% quote-age coverage. Historical indicator warm-up and constructed tests do not fill gaps. The first failed October 16 window and October 19 recovery rehearsal remain archived separately.", "",
             f"Window: {summary.get('started_at', 'not started')} to {summary.get('window_ends_at', 'not started')}.", "",
             f"Completed days: {summary['completed_days']}/7; qualifying days: {summary['qualifying_days']}/7; observations: {summary['observations']}.", "",
             "## Observed comparisons", "", "| Strategy | Equity USD | Return | Observed max drawdown | BTC |", "| --- | ---: | ---: | ---: | ---: |"]
    for name, row in summary.get("strategies", {}).items():
        lines.append(f"| {name} | {float(row['equity_usd']):,.2f} | {float(row['net_return_fraction'])*100:.3f}% | {float(row['observed_max_drawdown_fraction'])*100:.3f}% | {row['btc_units']} |")
    lines += ["", "The candidate starts gradually and retains defensive exit rules; buy-and-hold commits available cash, and scheduled DCA excludes dip/defensive rules. Exposure differences are part of the policy comparison and must be considered alongside returns. A short observed advantage does not establish durable profitability.", "",
              "## Daily coverage", "", "| Day | Full 24 hours | Quote coverage | Qualifies |", "| --- | --- | ---: | --- |"]
    for day in summary.get("days", []):
        lines.append(f"| {day['day']} | {day['complete_24_hours']} | {day['quote_coverage_fraction']*100:.3f}% | {day['qualifies']} |")
    lines += ["", "## Independent checks", ""] + [f"- {'PASS' if passed else 'PENDING / FAIL'}: {key.replace('_', ' ')}." for key, passed in checks.items()]
    lines += ["", "The point-in-time SQLite backup passed integrity and independent balance reconstruction. Its hash is recorded in verification.json; the database stays local. evaluation.json records the configuration, environment, observations and limits. The application source is frozen for this experiment.", "",
              "## Interpretation and limits", "", summary.get("limits", "No prospective observations yet."), "",
              "A passing week demonstrates operation under that week's conditions. It does not validate personal allocations, the long-term value of Bitcoin or protection through every market outage. Optional real messaging, OAuth, LLM advice and Docker deployment remain unverified; they do not influence this experiment.", "",
              "The historical feature selector's 14.37% holdout return came with a 72.30% development drawdown; it was not promoted into this operational policy. The original swing rule lost money after costs, and swing entries remain disabled here. These are limitations that informed the operational controls, not hidden tuning successes.", ""]
    (output / "review.md").write_text("\n".join(lines), encoding="utf-8")
    with (output / "daily-coverage.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=["day", "start", "end", "complete_24_hours", "quote_coverage_fraction", "qualifies"])
        writer.writeheader()
        writer.writerows(summary.get("days", []))
    return verification


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default="var/forward")
    parser.add_argument("--output", default="reports/final")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(create_review(args.runtime, args.output, args.require_complete), indent=2))
    except (OSError, ValueError, ArithmeticError) as exc:
        print(str(exc), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
