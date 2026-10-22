"""Export actual, separate paper accounts; retain missing days and interruptions."""
import csv
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bitcoin_agent.paper_store import audit_paper
from bitcoin_agent.forward import audit_forward

SOURCES = {"public_connectivity_demo": "var/operational.sqlite3",
           "oct16_interrupted": "var/archive/forward-20261016-interrupted/ledger.sqlite3",
           "oct19_recovery_rehearsal": "var/archive/forward-20261019-recovery-rehearsal/ledger.sqlite3",
           "oct19_interrupted": "var/forward/ledger.sqlite3"}


def coverage(times, start, end):
    covered, previous_end = 0., start
    for at in times:
        lo, hi = max(start, at), min(end, at + timedelta(seconds=120))
        if hi > max(lo, previous_end):
            covered += (hi - max(lo, previous_end)).total_seconds()
        previous_end = max(previous_end, hi)
    return max(0., covered)


def main():
    now = datetime.now(timezone.utc)
    stamp = now.strftime("%Y%m%dT%H%M%S%f")
    out = ROOT / "reports/real-data-evaluation"
    out.mkdir(parents=True, exist_ok=True)
    snapshots = ROOT / "var/completion/snapshots" / ("submission-" + stamp)
    snapshots.mkdir(parents=True, exist_ok=True)
    daily, hourly, summaries = [], [], []
    for name, relative in SOURCES.items():
        source = ROOT / relative
        target = snapshots / (name + ".sqlite3")
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as live, sqlite3.connect(target) as saved:
            live.backup(saved)
            if saved.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ArithmeticError("Snapshot integrity failed")
        with sqlite3.connect(target) as database:
            database.row_factory = sqlite3.Row
            tables = {r[0] for r in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            quotes = [dict(r) for r in database.execute("SELECT id,observed_at FROM paper_quotes WHERE accepted=1 ORDER BY observed_at,id")]
            marks = [dict(r) for r in database.execute("SELECT * FROM paper_marks ORDER BY observed_at,quote_id")]
            fills = [dict(r) for r in database.execute("SELECT occurred_at,side,sleeve,quantity,price,fee_usd FROM paper_fills ORDER BY occurred_at,id")]
            audit = audit_forward(target) if "forward_run" in tables else audit_paper(target)
            provenance = json.loads(database.execute("SELECT manifest_json FROM forward_run WHERE id=1").fetchone()[0]) if "forward_run" in tables else {"source": "coinbase_public_connectivity_demo", "independently_stream_manifested": False}
        if not audit["passed"]:
            raise ArithmeticError("An account failed reconciliation")
        times = [datetime.fromisoformat(q["observed_at"]) for q in quotes]
        if not times:
            continue
        start, last = times[0], times[-1]
        day = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while day <= last:
            end = min(day + timedelta(days=1), now)
            inside = [t for t in times if day <= t < end]
            observed = coverage(times, day, end)
            daily.append({"account": name, "day_utc": day.date().isoformat(), "accepted_quotes": len(inside),
                          "first_quote": inside[0].isoformat() if inside else "", "last_quote": inside[-1].isoformat() if inside else "",
                          "observed_quote_age_coverage_seconds": round(observed, 6),
                          "evaluated_seconds": (end - day).total_seconds(), "coverage_fraction": observed / (end - day).total_seconds(),
                          "complete_calendar_day": end - day == timedelta(days=1)})
            day += timedelta(days=1)
        # One real mark per observed UTC hour, without creating missing rows.
        by_hour = {}
        for mark in marks:
            hour = datetime.fromisoformat(mark["observed_at"]).replace(minute=0, second=0, microsecond=0)
            by_hour[hour] = mark
        for hour, mark in sorted(by_hour.items()):
            hourly.append({"account": name, "hour_utc": hour.isoformat(), "actual_mark_at": mark["observed_at"],
                           "quote_id": mark["quote_id"], "price_usd": mark["price"], "cash_usd": mark["cash_usd"],
                           "btc_units": mark["btc_units"], "equity_usd": mark["equity_usd"], "phase": mark["phase"]})
        summary = {"account": name, "source": relative, "first_actual_quote": start.isoformat(), "last_actual_quote": last.isoformat(),
                   "accepted_quotes": len(quotes), "observed_calendar_days": len({t.date() for t in times}),
                   "largest_gap_between_actual_quotes_seconds": max([(b - a).total_seconds() for a, b in zip(times, times[1:])] or [0]),
                   "snapshot_sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "audit": audit, "provenance": provenance,
                   "first_equity_usd": marks[0]["equity_usd"], "last_equity_usd": marks[-1]["equity_usd"],
                   "fills": fills, "role": "Connectivity demonstration" if name == "public_connectivity_demo" else "Recovery rehearsal, excluded from timed validation" if "rehearsal" in name else "Interrupted prospective account"}
        summaries.append(summary)
    for filename, records in (("daily-coverage.csv", daily), ("observed-hourly-marks.csv", hourly)):
        with (out / filename).open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]))
            writer.writeheader(); writer.writerows(records)
    result = {"generated_at": now.isoformat(), "accounts": summaries, "calendar_span_days":
              (max(datetime.fromisoformat(s["last_actual_quote"]) for s in summaries) - min(datetime.fromisoformat(s["first_actual_quote"]) for s in summaries)).total_seconds() / 86400,
              "continuous_week_passed": False, "accounts_combined_into_one_portfolio": False,
              "historical_or_synthetic_quotes_substituted": False,
              "scope": "Dated real-data logs spanning more than a calendar week, with gaps and separate account identities. This is evidence of interrupted operation, not seven uninterrupted days or successful 24/7 deployment."}
    (out / "evaluation.json").write_text(json.dumps(result, indent=2) + "\n")
    lines = ["# Real-data operation and interruption log", "", "**The system did not achieve continuous 24/7 operation.**", "",
             "This log exports actually received quotes and paper-account marks. It spans more than one calendar week, "
             "but contains substantial outages. A calendar span is not a continuous week of testing. Separate accounts and the recovery rehearsal remain separate; their returns are not joined into a fictional portfolio.", "",
             "| Account | First actual quote (UTC) | Last actual quote (UTC) | Accepted quotes | Largest quote gap (hours) | Audit |",
             "| --- | --- | --- | ---: | ---: | --- |"]
    for s in summaries:
        lines.append(f"| {s['account']} | {s['first_actual_quote']} | {s['last_actual_quote']} | {s['accepted_quotes']} | {s['largest_gap_between_actual_quotes_seconds']/3600:.2f} | PASS |")
    lines += ["", "## Evidence and interpretation", "",
              "- `daily-coverage.csv` includes empty days inside each account's observation span. Coverage counts only the 120-second validity interval of an actual received quote.",
              "- `observed-hourly-marks.csv` contains the last actual mark in each observed hour; missing hours are not manufactured.",
              "- `evaluation.json` records provenance, fills, independently reconstructed cash/BTC, and hashes of consistent private SQLite snapshots.",
              "- The original October 16 account, October 19 recovery rehearsal and replacement account retain distinct identities.",
              "- Windows evidence attributes repeated long gaps to lid-triggered standby and battery-budget hibernation; other unproven causes remain unassigned.",
              "- The owner's local-computer choice was used. Reliable always-on operation and on-time Monday email require an available process, network and credentials.",
              "- The earlier 99% daily threshold was an additional project protocol, not a numerical requirement in the supplied Apziva brief. Its failure is preserved rather than relabelled as success.",
              "", "The paper collector was stopped during final packaging, with its account preserved. No live-money orders were submitted. No historical warm-up or constructed stress path is counted as prospective evidence.", ""]
    (out / "review.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"accounts": len(summaries), "calendar_span_days": result["calendar_span_days"], "all_audits_passed": True,
                      "continuous_week_passed": False, "output": str(out)}, indent=2))


if __name__ == "__main__":
    main()
