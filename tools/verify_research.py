"""Independently reconcile exported fills and check research chronology."""

import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def rows(path):
    with path.open(encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


def verify(output, data):
    summary = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    initial = Decimal(str(summary["initial_cash_usd"]))
    tolerance = initial * Decimal("0.00000001")
    if hashlib.sha256(data.read_bytes()).hexdigest() != summary["data_sha256"]:
        raise ValueError("Input checksum differs from the research snapshot")
    cutoff = datetime.fromisoformat(summary["holdout_start_utc"])
    episodes = rows(output / "training_episodes.csv")
    if any(datetime.fromisoformat(row["end_utc"]) > cutoff for row in episodes):
        raise ValueError("Training outcomes entered the sealed holdout")
    audited, target_exits, target_losses = [], 0, 0
    for period in ("development", "holdout"):
        for metric in summary[f"{period}_metrics"]:
            identifier = metric["strategy"].lower().replace(" ", "_")
            fills = rows(output / f"{period}_{identifier}_fills.csv")
            curves = rows(output / f"{period}_{identifier}_curve.csv")
            balance = initial
            inventory = {"dca": Decimal(0), "swing": Decimal(0)}
            swing_entry_cost = None
            for fill in fills:
                quantity = Decimal(fill["btc_units"])
                notional = quantity * Decimal(fill["fill_price"])
                fee = Decimal(fill["fee_usd"])
                sleeve = fill["sleeve"]
                if fill["side"] == "buy":
                    balance -= notional + fee
                    inventory[sleeve] += quantity
                    if period == "holdout" and metric["strategy"] == "Swing only":
                        swing_entry_cost = notional + fee
                else:
                    balance += notional - fee
                    inventory[sleeve] -= quantity
                    if period == "holdout" and metric["strategy"] == "Swing only" and fill["reason"] == "profit_target":
                        target_exits += 1
                        target_losses += int(notional - fee <= swing_entry_cost)
                if balance < -tolerance or any(value < Decimal("-0.000000001") for value in inventory.values()):
                    raise ValueError(f"Negative cash/inventory in {period} {metric['strategy']}")
            difference = abs(balance - Decimal(str(metric["final_equity_usd"])))
            if difference > tolerance or any(abs(value) > Decimal("0.000000001") for value in inventory.values()):
                raise ValueError(f"Exported fills do not reconcile for {period} {metric['strategy']}")
            if any(Decimal(row["cash_usd"]) < -tolerance for row in curves):
                raise ValueError("Negative reported cash")
            if any(abs(sum(Decimal(row[field]) for field in ("target_dca", "target_swing", "target_reserve")) - 1) > Decimal("0.000000001") for row in curves):
                raise ValueError("Allocation targets do not conserve capital")
            decision_path = output / f"{period}_{identifier}_decisions.csv"
            if decision_path.is_file():
                for decision in rows(decision_path):
                    fitted = decision.get("model_fitted_before_utc")
                    if fitted:
                        if datetime.fromisoformat(fitted) > datetime.fromisoformat(decision["time_utc"]):
                            raise ValueError("A decision used a future fitted model")
                        if period == "holdout" and datetime.fromisoformat(fitted) != cutoff:
                            raise ValueError("Holdout model was refitted")
            audited.append({"period": period, "strategy": metric["strategy"], "fills": len(fills),
                            "cash_reconciliation_error_usd": str(difference)})
    result = {"passed": True, "verified_at_utc": datetime.now(timezone.utc).isoformat(),
              "summary_sha256": hashlib.sha256(json.dumps(summary, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
              "checks": ["input_checksum", "training_outcome_cutoff", "frozen_holdout_models",
                         "fill_cash_reconciliation", "inventory_reconciliation", "nonnegative_cash", "allocation_totals"],
              "strategies_audited": len(audited), "details": audited,
              "swing_holdout_profit_target_exits": target_exits,
              "swing_holdout_profit_target_exits_with_net_loss": target_losses}
    (output / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "reports/milestone-2")
    parser.add_argument("--data", type=Path, default=ROOT / "data/raw/btc_usd_1h.csv")
    args = parser.parse_args()
    print(json.dumps(verify(args.output, args.data), indent=2))
