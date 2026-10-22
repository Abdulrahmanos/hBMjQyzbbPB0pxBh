"""Prospective observation log and simultaneous, costed baseline books."""

from __future__ import annotations

import hashlib
import json
import platform
from importlib.metadata import PackageNotFoundError, version
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .paper_store import audit_paper, connection
from .paper_types import CENT, ONE, ZERO, ceil_step, decimal as D, floor_step, utc
from .remote_config import atomic_json

WINDOW_SECONDS = 7 * 86400
MINIMUM_DAILY_COVERAGE = .99


def source_checksum():
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).resolve().parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def environment_versions():
    result = {"python": platform.python_version(), "platform": platform.system()}
    for package in ("numpy", "pandas"):
        try:
            result[package] = version(package)
        except PackageNotFoundError:
            result[package] = "unavailable"
    return result


class ForwardEvaluation:
    """Only called by the public-stream worker after a committed accepted tick."""
    def __init__(self, path, config, *, code_checksum=None):
        self.path, self.config = path, config
        self.code_checksum = code_checksum or source_checksum()
        with connection(path, write=True) as database:
            tables = {row[0] for row in database.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "forward_run" in tables:
                manifest = json.loads(database.execute("SELECT manifest_json FROM forward_run WHERE id=1").fetchone()[0])
                if manifest["config_checksum"] != config.checksum or manifest["code_checksum"] != self.code_checksum or manifest["environment"] != environment_versions():
                    raise ValueError("Forward settings or source changed; preserve this run and create a separate experiment")
                return
            if database.execute("SELECT COUNT(*) FROM paper_quotes").fetchone()[0]:
                raise ValueError("Forward evaluation requires a fresh paper database; historical demos cannot count")
            database.execute("CREATE TABLE forward_run (id INTEGER PRIMARY KEY CHECK(id=1), manifest_json TEXT NOT NULL)")
            database.execute("CREATE TABLE forward_books (name TEXT PRIMARY KEY,state_json TEXT NOT NULL)")
            database.execute("CREATE TABLE forward_observations (quote_id TEXT PRIMARY KEY REFERENCES paper_quotes(id),observed_at TEXT NOT NULL,fresh_until TEXT NOT NULL,features_fresh INTEGER NOT NULL,entries_enabled INTEGER NOT NULL,config_checksum TEXT NOT NULL,candidate_equity TEXT NOT NULL,candidate_cash TEXT NOT NULL,candidate_btc TEXT NOT NULL,candidate_phase TEXT NOT NULL,books_json TEXT NOT NULL)")
            database.execute("CREATE INDEX forward_observations_time ON forward_observations(observed_at)")
            database.execute("CREATE TABLE forward_fills (id INTEGER PRIMARY KEY,quote_id TEXT NOT NULL REFERENCES forward_observations(quote_id) DEFERRABLE INITIALLY DEFERRED,book TEXT NOT NULL,observed_at TEXT NOT NULL,quantity TEXT NOT NULL,price TEXT NOT NULL,notional TEXT NOT NULL,fee TEXT NOT NULL,UNIQUE(quote_id,book))")
            manifest = {"schema_version": 1, "source": "coinbase_public_stream", "created_at": datetime.now(timezone.utc).isoformat(),
                        "config_checksum": config.checksum, "config": config.data, "code_checksum": self.code_checksum,
                        "initial_cash_usd": str(config.initial_cash), "window_seconds": WINDOW_SECONDS, "environment": environment_versions(),
                        "minimum_daily_quote_coverage": MINIMUM_DAILY_COVERAGE, "historical_candles_count_as_forward": False,
                        "llm_influences_orders": False, "baseline_rule": "DCA uses the initial DCA reference/tranche and 7-day cap; scheduled buys only. Buy-and-hold fills available cash up to the same per-quote cap. All books include idle cash and modeled fees/slippage; no final liquidation assumed."}
            database.execute("INSERT INTO forward_run VALUES (1,?)", (json.dumps(manifest, sort_keys=True),))
            for name in ("scheduled_dca", "buy_and_hold", "cash"):
                state = {"cash": str(config.initial_cash), "btc": "0", "fees": "0", "last_buy": None, "spending": []}
                database.execute("INSERT INTO forward_books VALUES (?,?)", (name, json.dumps(state)))

    def observe(self, quote, features, result, now, config, rules):
        if not result["accepted"] or result["duplicate"]:
            return
        now = utc(now)
        quote.check_fresh(now, config.data["market"]["stale_after_seconds"])
        with connection(self.path, write=True) as database:
            if database.execute("SELECT 1 FROM forward_observations WHERE quote_id=?", (quote.quote_id,)).fetchone():
                return
            stored = database.execute("SELECT quote_json,accepted FROM paper_quotes WHERE id=?", (quote.quote_id,)).fetchone()
            mark = database.execute("SELECT * FROM paper_marks WHERE quote_id=?", (quote.quote_id,)).fetchone()
            if stored is None or not stored["accepted"] or mark is None or json.loads(stored["quote_json"]) != quote.to_dict():
                raise ValueError("Forward observation must match a committed accepted public quote")
            last = database.execute("SELECT observed_at FROM forward_observations ORDER BY observed_at DESC LIMIT 1").fetchone()
            if last and now <= utc(last[0]):
                raise ValueError("Forward observation clock must increase")
            book_marks = {}
            for row in database.execute("SELECT * FROM forward_books").fetchall():
                name, state = row["name"], json.loads(row["state_json"])
                self._buy(database, quote, now, name, state, rules)
                state["spending"] = [item for item in state["spending"] if utc(item[0]) > now - timedelta(days=7)]
                database.execute("UPDATE forward_books SET state_json=? WHERE name=?", (json.dumps(state), name))
                equity = D(state["cash"]) + D(state["btc"]) * (quote.bid + quote.ask) / 2
                book_marks[name] = {"equity_usd": str(equity), "cash_usd": state["cash"], "btc_units": state["btc"], "fees_usd": state["fees"]}
            fresh_until = min(quote.market_time, quote.received_at) + timedelta(seconds=config.data["market"]["stale_after_seconds"])
            feature_fresh = features is not None and features.is_fresh(now, config.data["operational"]["feature_stale_after_seconds"])
            database.execute("INSERT INTO forward_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                             (quote.quote_id, now.isoformat(), fresh_until.isoformat(), int(feature_fresh), int(result["entries_enabled"]),
                              config.checksum, mark["equity_usd"], mark["cash_usd"], mark["btc_units"], mark["phase"], json.dumps(book_marks)))

    def _buy(self, database, quote, now, name, state, rules):
        if name == "cash" or not rules.market_enabled:
            return
        data = self.config.data
        cash, btc = D(state["cash"]), D(state["btc"])
        budget = min(cash, D(data["operational"]["paper_fill_cap_usd"]))
        if name == "scheduled_dca":
            dca = data["dca"]
            if state["last_buy"] and (now - utc(state["last_buy"])).total_seconds() < dca["schedule_hours"] * 3600:
                return
            reference = self.config.initial_cash * D(data["portfolio"]["dca_fraction"])
            spent = sum((D(item[1]) for item in state["spending"] if utc(item[0]) > now - timedelta(days=7)), ZERO)
            budget = min(budget, reference * D(dca["tranche_fraction_of_dca_budget"]), reference * D(dca["max_spend_fraction_per_7_days"]) - spent)
        fee = D(data["risk"]["fee_fraction_per_fill"])
        entry = ceil_step(quote.ask * (ONE + D(data["risk"]["slippage_fraction_per_fill"])), rules.quote_increment)
        quantity = floor_step(max(ZERO, budget - 2 * CENT) / (entry * (ONE + fee)), rules.base_increment)
        notional = ceil_step(quantity * entry, CENT)
        costs = ceil_step(notional * fee, CENT)
        debit = notional + costs
        if quantity <= 0 or notional < rules.minimum_notional or debit > budget:
            return
        state.update(cash=str(cash - debit), btc=str(btc + quantity), fees=str(D(state["fees"]) + costs), last_buy=now.isoformat())
        state["spending"].append([now.isoformat(), str(debit)])
        database.execute("INSERT INTO forward_fills (quote_id,book,observed_at,quantity,price,notional,fee) VALUES (?,?,?,?,?,?,?)",
                         (quote.quote_id, name, now.isoformat(), str(quantity), str(entry), str(notional), str(costs)))


def audit_forward(path):
    candidate = audit_paper(path)
    with connection(path) as database:
        manifest = json.loads(database.execute("SELECT manifest_json FROM forward_run WHERE id=1").fetchone()[0])
        for row in database.execute("SELECT * FROM forward_books"):
            state = json.loads(row["state_json"])
            cash, btc, fees = D(manifest["initial_cash_usd"]), ZERO, ZERO
            for fill in database.execute("SELECT * FROM forward_fills WHERE book=? ORDER BY observed_at,id", (row["name"],)):
                cash -= D(fill["notional"]) + D(fill["fee"])
                btc += D(fill["quantity"])
                fees += D(fill["fee"])
            if cash != D(state["cash"]) or btc != D(state["btc"]) or fees != D(state["fees"]) or min(cash, btc, fees) < 0:
                raise ArithmeticError("Forward baseline ledger does not reconcile")
        unmatched = database.execute("SELECT COUNT(*) FROM forward_observations f LEFT JOIN paper_marks p ON f.quote_id=p.quote_id WHERE p.quote_id IS NULL OR f.candidate_equity!=p.equity_usd OR f.candidate_cash!=p.cash_usd OR f.candidate_btc!=p.btc_units").fetchone()[0]
        if unmatched:
            raise ArithmeticError("Forward observations differ from paper marks")
    return {"passed": True, "candidate": candidate, "baseline_books_reconciled": 3}


def forward_summary(path, now=None):
    now = utc(now or datetime.now(timezone.utc))
    with connection(path) as database:
        manifest = json.loads(database.execute("SELECT manifest_json FROM forward_run WHERE id=1").fetchone()[0])
        observations = [dict(row) for row in database.execute("SELECT * FROM forward_observations ORDER BY observed_at,quote_id")]
        observer_failures = database.execute("SELECT COUNT(*) FROM system_events WHERE event_type='forward_observer_failure'").fetchone()[0]
    if not observations:
        return {"status": "waiting_for_first_public_quote", "generated_at": now.isoformat(), "observations": 0,
                "completed_days": 0, "qualifying_days": 0, "seven_day_evidence_complete": False, "manifest": manifest}
    start = utc(observations[0]["observed_at"])
    end = min(max(now, start), start + timedelta(seconds=manifest["window_seconds"]))
    observations = [row for row in observations if utc(row["observed_at"]) <= end]
    day_rows, covered_total, max_gap = [], 0.0, 0.0
    for index in range(7):
        left, right = start + timedelta(days=index), min(start + timedelta(days=index + 1), end)
        if left >= end:
            break
        covered = 0.0
        for position, row in enumerate(observations):
            at = utc(row["observed_at"])
            next_at = utc(observations[position + 1]["observed_at"]) if position + 1 < len(observations) else end
            until = min(next_at, utc(row["fresh_until"]), end)
            covered += max(0.0, (min(right, until) - max(left, at)).total_seconds())
        span = (right - left).total_seconds()
        full = span >= 86400
        ratio = min(1.0, covered / span) if span else 0.0
        day_rows.append({"day": index + 1, "start": left.isoformat(), "end": right.isoformat(), "complete_24_hours": full,
                         "quote_coverage_fraction": ratio, "qualifies": full and ratio >= manifest["minimum_daily_quote_coverage"]})
        covered_total += covered
    for position, row in enumerate(observations):
        next_at = utc(observations[position + 1]["observed_at"]) if position + 1 < len(observations) else end
        max_gap = max(max_gap, (next_at - utc(row["observed_at"])).total_seconds())
    initial = D(manifest["initial_cash_usd"])
    peaks = {name: initial for name in ("candidate", "scheduled_dca", "buy_and_hold", "cash")}
    drawdowns = {name: ZERO for name in peaks}
    latest = {}
    for row in observations:
        values = json.loads(row["books_json"])
        values["candidate"] = {"equity_usd": row["candidate_equity"], "cash_usd": row["candidate_cash"], "btc_units": row["candidate_btc"]}
        for name, value in values.items():
            equity = D(value["equity_usd"])
            peaks[name] = max(peaks[name], equity)
            drawdowns[name] = max(drawdowns[name], ONE - equity / peaks[name])
            latest[name] = {**value, "net_return_fraction": str(equity / initial - ONE), "observed_max_drawdown_fraction": str(drawdowns[name])}
    changes = sorted({row["config_checksum"] for row in observations} - {manifest["config_checksum"]})
    elapsed = (end - start).total_seconds()
    complete = elapsed >= WINDOW_SECONDS and all(row["qualifies"] for row in day_rows) and len(day_rows) == 7 and not changes and observer_failures == 0
    return {"status": "seven_day_window_closed" if elapsed >= WINDOW_SECONDS else "collecting_forward_data", "generated_at": now.isoformat(),
            "started_at": start.isoformat(), "window_ends_at": (start + timedelta(seconds=WINDOW_SECONDS)).isoformat(),
            "elapsed_hours": elapsed / 3600, "completed_days": sum(row["complete_24_hours"] for row in day_rows),
            "qualifying_days": sum(row["qualifies"] for row in day_rows), "seven_day_evidence_complete": complete,
            "observations": len(observations), "last_observation_at": observations[-1]["observed_at"],
            "quote_coverage_fraction": covered_total / elapsed if elapsed else None, "max_observation_gap_seconds": max_gap,
            "fresh_feature_observation_fraction": sum(row["features_fresh"] for row in observations) / len(observations),
            "changed_config_checksums": changes, "observer_failures": observer_failures, "days": day_rows, "strategies": latest, "manifest": manifest,
            "limits": "Coverage measures time under the configured quote-age limit. Prices are never imputed; observed drawdown can miss movements between quotes. Marked equity excludes hypothetical final exit costs. Seven days do not establish durable investment performance."}


def write_forward_report(path, output, now=None):
    summary = forward_summary(path, now)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    atomic_json(output / "evaluation.json", summary)
    lines = ["# Forward paper evaluation", "", f"Status: **{summary['status']}**", "",
             f"Updated: {summary['generated_at']}", "",
             f"Completed 24-hour days: **{summary['completed_days']}/7**. Qualifying days: **{summary['qualifying_days']}/7**.", "",
             "Only prospective public observations count. Historical candles initialize indicators and do not fill evaluation gaps.", ""]
    if summary["observations"]:
        lines += [f"First quote: {summary['started_at']}. Seven-day boundary: {summary['window_ends_at']}.", "",
                  f"Accepted observations: {summary['observations']}. Maximum observed gap: {summary['max_observation_gap_seconds']:.1f} seconds.", "",
                  "| Strategy | Marked equity (USD) | Net return | Observed max drawdown | BTC |", "| --- | ---: | ---: | ---: | ---: |"]
        for name, row in summary["strategies"].items():
            lines.append(f"| {name} | {D(row['equity_usd']):.2f} | {D(row['net_return_fraction'])*100:.3f}% | {D(row['observed_max_drawdown_fraction'])*100:.3f}% | {row['btc_units']} |")
        lines += ["", summary["limits"], "", "Scheduled DCA uses the initial DCA sleeve reference with no dip rule or defensive exits. Buy-and-hold buys available cash under the same modeled per-quote fill cap. Cash earns no interest. The candidate keeps its risk controls; exposure differences are part of the comparison."]
    target = output / "evaluation.md"
    temporary = target.with_suffix(".md.tmp")
    temporary.write_text("\n".join(lines) + "\n", encoding="utf-8")
    temporary.replace(target)
    return summary
