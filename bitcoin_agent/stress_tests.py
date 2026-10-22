"""Constructed market paths exercising the exact persistent paper engine."""

from __future__ import annotations

import copy
import csv
import json
import math
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import AgentConfig, validate_config
from .paper_engine import PaperEngine
from .paper_store import audit_paper, connection, paper_status
from .paper_types import Features, Quote, decimal as D


def _paths():
    warm = [100.0] * 60
    return {
        "year_decline_80pct": warm + [100 * 0.2 ** (day / 364) for day in range(365)],
        "flash_drop_30pct": warm + [70.0] * 35,
        "false_rebound": warm + [70.0] * 10 + [110.0] * 9 + [70.0] * 15 + [50.0] * 20,
        "outage_during_50pct_drop": warm + [50.0] * 35,
    }


def _daily_context(prior, now, streak):
    closing = prior[-1]
    trend = sum(prior[-200:]) / 200
    momentum = closing / prior[-31] - 1
    bearish = closing < trend and momentum < 0
    streak = streak + 1 if bearish else 0
    regime = "bear" if bearish else "bull" if closing > trend and momentum > 0 else "mixed"
    snapshot = Features.from_dict({"asof": now.isoformat(), "daily_asof": now.isoformat(), "close": str(closing), "atr": "2",
                                  "ready": True, "swing_signal": False, "regime": regime,
                                  "confirmed_bear": streak >= 7, "return_24h": str(closing / prior[-2] - 1)})
    return snapshot, streak


def run_stress(config: AgentConfig, output="reports/milestone-3", progress=print):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[1]
    run_id = uuid.uuid4().hex[:12]
    databases = root / "var/stress" / run_id
    databases.mkdir(parents=True)
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    rows, curves, audits = [], {}, {}
    for name, prices in _paths().items():
        for policy in ("pause_only", "defensive"):
            settings = copy.deepcopy(config.data)
            # Common starting sleeves for the controlled comparison. The protected
            # policy can constrain the resulting exposure before the shock.
            settings["portfolio"].update(dca_fraction=0.7, swing_fraction=0.2, reserve_fraction=0.1)
            settings["swing"]["enabled"] = False
            settings["operational"]["allocation_mode"] = "fixed"
            if policy == "pause_only":
                settings["operational"].update(bear_exit_enabled=False, drawdown_action="pause", max_btc_exposure_fraction=0.98,
                                                 exposure_buffer_fraction=0.01, minimum_cash_fraction=0.01,
                                                 max_lifetime_loss_fraction=0.99, crash_drop_fraction=0.99, resume_mode="manual")
            candidate = validate_config(settings)
            path = databases / f"{name}_{policy}.sqlite3"
            engine = PaperEngine(path, candidate)
            prior, streak, daily_rows = [100.0] * 200, 0, []
            pre_shock_exposure = None
            for day, price in enumerate(prices):
                now = start + timedelta(days=day)
                snapshot, streak = _daily_context(prior, now, streak)
                if name == "outage_during_50pct_drop" and day == 60:
                    # Invalid data cannot execute an exit. The new price becomes
                    # observable one hour later; the loss happens during the gap.
                    raw = {"quote_id": f"{name}:{day}:stale", "time": (now - timedelta(hours=1)).isoformat(),
                           "received_at": now.isoformat(), "bid": str(price - 0.01), "ask": str(price + 0.01), "price": str(price)}
                    engine.tick(Quote.from_dict(raw), snapshot, now)
                    now += timedelta(hours=1)
                for tick in range(12):
                    timestamp = now + timedelta(seconds=5 * tick)
                    raw = {"quote_id": f"{name}:{day}:{tick}", "time": timestamp.isoformat(), "received_at": timestamp.isoformat(),
                           "bid": str(price - 0.01), "ask": str(price + 0.01), "price": str(price)}
                    result = engine.tick(Quote.from_dict(raw), snapshot, timestamp)
                    if not result["portfolio"]["active_orders"]:
                        break
                status = result["portfolio"]
                equity = D(status["equity_usd"])
                exposure = D(status["btc_units"]) * D(status["last_price"]) / equity
                if day == 59:
                    pre_shock_exposure = float(exposure)
                daily_rows.append({"day": day, "price": price, "equity_usd": float(equity), "cash_usd": float(D(status["cash_usd"])),
                                   "btc_exposure": float(exposure), "lifetime_drawdown": float(D(status["max_lifetime_drawdown"])), "phase": status["phase"]})
                prior.append(price)
            audit = audit_paper(path)
            audits[f"{name}:{policy}"] = audit
            with connection(path) as database:
                resumes = database.execute("SELECT COUNT(*) FROM system_events WHERE event_type='paper_recovery_resume'").fetchone()[0]
                pauses = [json.loads(row[0]) for row in database.execute("SELECT payload_json FROM system_events WHERE event_type='paper_risk_pause'")]
            last = daily_rows[-1]
            rows.append({"scenario": name, "policy": policy, "net_return": last["equity_usd"] / float(config.initial_cash) - 1,
                         "max_drawdown": last["lifetime_drawdown"], "final_equity_usd": last["equity_usd"],
                         "pre_shock_btc_exposure": pre_shock_exposure, "ending_phase": last["phase"], "recovery_resumes": resumes,
                         "fees_usd": audit["fees_usd"], "pause_reasons": ";".join(pause["reason"] for pause in pauses),
                         "database": str(path), "ledger_passed": audit["passed"]})
            curves[(name, policy)] = daily_rows
            with (output / f"{name}_{policy}_curve.csv").open("w", newline="", encoding="utf-8") as destination:
                writer = csv.DictWriter(destination, fieldnames=daily_rows[0].keys())
                writer.writeheader()
                writer.writerows(daily_rows)
            (output / f"{name}_{policy}_config.json").write_text(json.dumps(settings, indent=2), encoding="utf-8")
            progress(json.dumps({"event": "stress_case_complete", "scenario": name, "policy": policy,
                                 "net_return": rows[-1]["net_return"], "max_drawdown": rows[-1]["max_drawdown"], "audit_passed": audit["passed"]}))
    with (output / "stress_summary.csv").open("w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    summary = {"kind": "synthetic_control_test", "run_id": run_id, "config_checksum": config.checksum,
               "assumptions": {"synthetic_start_price": 100, "warmup_accumulation_days": 60,
                               "prior_flat_daily_history": 200, "quote_observations": "Daily plus five-second partial-exit ticks at the same modeled price",
                               "swing_entries": False, "feature_timing": "Previous daily close only; 200-day mean, 30-day momentum, seven bearish days",
                               "comparison": "Combined controls; pre-shock exposure differs. This does not isolate any one control or estimate future returns."},
               "results": rows, "audits": audits}
    (output / "stress_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _plot(output, curves, float(config.initial_cash))
    return summary


def _plot(output, curves, initial):
    import os
    os.environ.setdefault("MPLCONFIGDIR", str(Path(__file__).resolve().parents[1] / "var/matplotlib"))
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import StrMethodFormatter
    titles = {"year_decline_80pct": "80% decline over one year", "flash_drop_30pct": "Sudden 30% price drop",
              "false_rebound": "Rebound followed by renewed selling", "outage_during_50pct_drop": "50% drop while quotes are unavailable"}
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))
    for ax, (name, title) in zip(axes.flat, titles.items()):
        for policy, label, color in (("pause_only", "Pause only", "#b45309"), ("defensive", "Defensive controls", "#2563eb")):
            values = curves[(name, policy)]
            ax.plot([row["day"] for row in values], [row["equity_usd"] for row in values], label=label, color=color, linewidth=2)
        ax.axvline(60, color="#94a3b8", linestyle="--", linewidth=1)
        ax.axhline(initial, color="#cbd5e1", linewidth=1)
        ax.set_title(title, fontsize=12, fontweight="bold")
        ax.set_ylabel("Marked portfolio value (USD)")
        ax.set_xlabel("Days from start; shock begins after day 59")
        ax.yaxis.set_major_formatter(StrMethodFormatter("${x:,.0f}"))
        ax.grid(alpha=0.15)
        ax.spines[["top", "right"]].set_visible(False)
        ax.legend(loc="lower left", fontsize=9)
    fig.suptitle("Paper engine stress tests: constructed price paths", x=0.07, ha="left", fontsize=18, fontweight="bold")
    fig.text(0.07, 0.925, "Behavior tests of combined controls. These are artificial scenarios and do not demonstrate future investment performance.", color="#475569")
    fig.subplots_adjust(top=0.86, left=0.08, right=0.97, bottom=0.08, hspace=0.35, wspace=0.25)
    fig.savefig(output / "stress_tests.png", dpi=150)
    plt.close(fig)
