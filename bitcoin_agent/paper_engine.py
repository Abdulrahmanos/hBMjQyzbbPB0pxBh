"""A persistent decimal paper engine; every quote is one atomic transaction."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

from .config import AgentConfig
from .paper_store import (active_orders, audit_paper, balances_from, connection, event, initialize_paper,
                          save_balances, save_state, state_from, status_from)
from .paper_types import (CENT, ONE, ZERO, Features, ProductRules, Quote, ceil_step, decimal as D,
                          floor_step, swing_plan, utc)


class PaperEngine:
    def __init__(self, path, config: AgentConfig, rules: ProductRules | None = None):
        self.path, self.config = path, config
        self.app = config.data
        self.op = self.app.get("operational")
        if self.op is None:
            raise ValueError("Use the operational paper configuration")
        self.rules = rules or ProductRules()
        self.fee = D(self.app["risk"]["fee_fraction_per_fill"])
        self.slip = D(self.app["risk"]["slippage_fraction_per_fill"])
        initialize_paper(path, config)
        audit_paper(path)
        if rules is None:
            with connection(path) as database:
                cached = state_from(database).get("product_rules")
            if cached:
                self.rules = ProductRules(D(cached["base_increment"]), D(cached["quote_increment"]),
                                          D(cached["minimum_notional"]), cached["market_enabled"])

    def _cancel_buys(self, database, now, reason, sleeve=None):
        for order in active_orders(database, "buy", sleeve):
            database.execute("UPDATE paper_orders SET status='canceled',reserved_usd='0',last_error=? WHERE id=?", (reason, order["id"]))
            event(database, now, "paper_order_canceled", {"order_id": order["id"], "reason": reason})

    def apply_config(self, config, now):
        """Apply an already validated configuration between ticks, never reset capital."""
        if config.funding_checksum != self.config.funding_checksum:
            raise ValueError("Configuration cannot change initial funding")
        if config.checksum == self.config.checksum:
            return False
        with connection(self.path, write=True) as database:
            self._cancel_buys(database, now, "config_changed")
            state = state_from(database)
            state.update(config_checksum=config.checksum, allocation_updated_at=None, last_signal_asof=None)
            save_state(database, state)
            database.execute("INSERT OR IGNORE INTO config_snapshots VALUES (?,?,?)", (config.checksum, now.isoformat(), config.canonical_json))
            event(database, now, "paper_config_applied", {"checksum": config.checksum})
        self.config, self.app = config, config.data
        self.op = self.app["operational"]
        self.fee, self.slip = D(self.app["risk"]["fee_fraction_per_fill"]), D(self.app["risk"]["slippage_fraction_per_fill"])
        return True

    def _available_cash(self, database, balances, sleeve):
        reserved = sum((D(order["reserved_usd"]) for order in active_orders(database, "buy", sleeve)), ZERO)
        return max(ZERO, balances[sleeve]["cash"] - reserved)

    def _submit(self, database, balances, quote, now, side, sleeve, quantity, reason, *, budget=ZERO, atr=None, risk_budget=None):
        quantity = floor_step(quantity, self.rules.base_increment)
        reference = quote.ask if side == "buy" else quote.bid
        if quantity <= 0 or quantity * reference < self.rules.minimum_notional:
            event(database, now, "paper_order_below_minimum", {"side": side, "sleeve": sleeve, "quantity": str(quantity), "reason": reason})
            return None
        if side == "buy" and budget > self._available_cash(database, balances, sleeve):
            raise ArithmeticError("Order reservation exceeds available cash")
        if side == "sell":
            reserved = sum((D(order["quantity"]) - D(order["filled_quantity"]) for order in active_orders(database, "sell", sleeve)), ZERO)
            if quantity > balances[sleeve]["btc"] - reserved:
                raise ArithmeticError("Order reservation exceeds inventory")
        order_id = hashlib.sha256(f"{quote.quote_id}:{side}:{sleeve}:{reason}".encode()).hexdigest()
        database.execute("INSERT OR IGNORE INTO paper_orders VALUES (?,?,?,?,?,'0','pending',?,?,?,?,NULL)",
                         (order_id, now.isoformat(), side, sleeve, str(quantity), reason, str(budget),
                          str(atr) if atr is not None else None, str(risk_budget) if risk_budget is not None else None))
        event(database, now, "paper_order_intent", {"order_id": order_id, "side": side, "sleeve": sleeve,
                                                   "quantity": str(quantity), "reason": reason})
        return order_id

    def _exit(self, database, balances, quote, now, sleeve, reason, quantity=None):
        self._cancel_buys(database, now, reason, sleeve)
        if active_orders(database, "sell", sleeve):
            return
        available = balances[sleeve]["btc"]
        desired = available if quantity is None else min(available, quantity)
        if desired > 0:
            placed = self._submit(database, balances, quote, now, "sell", sleeve, desired, reason)
            if placed is None:
                event(database, now, "paper_exit_below_minimum", {"sleeve": sleeve, "btc": str(desired), "reason": reason})

    def _pause(self, database, state, now, reason, liquidate=True, halted=False):
        if state["phase"] == "active" or halted:
            state.update(phase="halted" if halted else "liquidating" if liquidate else "paused",
                         pause_reason=reason, pause_started_at=now.isoformat(), recovery_days=0,
                         recovery_last_day=None, liquidation_required=liquidate)
            event(database, now, "paper_risk_pause", {"reason": reason, "liquidate": liquidate, "halted": halted})
        self._cancel_buys(database, now, reason)

    def _risk_actions(self, database, balances, state, quote, features, now, mark):
        cash = sum((row["cash"] for row in balances.values()), ZERO)
        btc = sum((row["btc"] for row in balances.values()), ZERO)
        equity = cash + btc * mark
        state["lifetime_peak"] = str(max(D(state["lifetime_peak"]), equity))
        state["epoch_peak"] = str(max(D(state["epoch_peak"]), equity))
        lifetime_dd = ONE - equity / D(state["lifetime_peak"])
        state["max_lifetime_drawdown"] = str(max(D(state["max_lifetime_drawdown"]), lifetime_dd))
        if equity <= self.config.initial_cash * (ONE - D(self.op["max_lifetime_loss_fraction"])):
            if state["phase"] != "halted":
                self._pause(database, state, now, "lifetime_capital_floor", halted=True)
        elif state["phase"] == "active":
            cutoff = (now - timedelta(hours=self.op["crash_window_hours"])).isoformat()
            old_prices = [D(row[0]) for row in database.execute("SELECT price FROM paper_marks WHERE observed_at>=?", (cutoff,))]
            reference = max(old_prices, default=mark)
            crash = mark <= reference * (ONE - D(self.op["crash_drop_fraction"]))
            fresh = features is not None and features.is_fresh(now, self.op["feature_stale_after_seconds"])
            crash = crash or (fresh and (features.return_24h <= -D(self.op["crash_drop_fraction"])
                                         or mark <= features.close * (ONE - D(self.op["crash_drop_fraction"]))))
            if crash:
                self._pause(database, state, now, "rapid_price_decline")
            elif fresh and features.confirmed_bear and self.op["bear_exit_enabled"]:
                self._pause(database, state, now, "confirmed_bear")
            elif ONE - equity / D(state["epoch_peak"]) >= D(self.app["risk"]["max_portfolio_drawdown_fraction"]):
                self._pause(database, state, now, "portfolio_drawdown", liquidate=self.op["drawdown_action"] == "liquidate")
        if state["liquidation_required"]:
            for sleeve in ("swing", "dca"):
                self._exit(database, balances, quote, now, sleeve, state["pause_reason"])
        elif state["phase"] == "active" and btc > 0:
            cap = D(self.op["max_btc_exposure_fraction"])
            actual = btc * mark / equity
            if actual > cap + D(self.op["exposure_buffer_fraction"]) or cash < D(self.op["minimum_cash_fraction"]) * equity:
                self._cancel_buys(database, now, "exposure_trim")
                sell_net = floor_step(quote.bid * (ONE - self.slip), self.rules.quote_increment) * (ONE - self.fee)
                needed = ceil_step(max(ZERO, btc * mark - cap * equity + 2 * CENT)
                                   / (mark - cap * (mark - sell_net)), self.rules.base_increment)
                for sleeve in ("dca", "swing"):
                    quantity = min(needed, balances[sleeve]["btc"])
                    if quantity > 0:
                        self._exit(database, balances, quote, now, sleeve, "exposure_trim", quantity)
                        needed -= quantity

    def _protect_swing(self, database, balances, quote, now):
        position = database.execute("SELECT * FROM paper_positions WHERE sleeve='swing'").fetchone()
        if D(position["quantity"]) <= 0:
            return
        reason = None
        if position["stop_price"] and quote.bid <= D(position["stop_price"]):
            reason = "swing_stop"
        elif position["target_price"] and quote.bid >= D(position["target_price"]):
            reason = "net_profit_target"
        elif (now - utc(position["opened_at"])).total_seconds() >= self.app["swing"]["max_holding_hours"] * 3600:
            reason = "swing_time_exit"
        if reason:
            self._exit(database, balances, quote, now, "swing", reason)

    def _rebalance_cash(self, database, balances, state, features, now, mark):
        old = state["allocation_updated_at"]
        if old and (now - utc(old)).total_seconds() < self.op["allocation_update_hours"] * 3600:
            return
        if self.op["allocation_mode"] == "fixed":
            weights = [self.app["portfolio"][key] for key in ("dca_fraction", "swing_fraction", "reserve_fraction")]
        else:
            regime = features.regime if features is not None else "unknown"
            key = "bull_targets" if regime == "bull" else "bear_targets" if regime in {"bear", "high_volatility"} else "mixed_targets" if regime == "mixed" else "unknown_targets"
            weights = self.op[key]
        targets = [D(value) for value in weights]
        pool = sum((row["cash"] for row in balances.values()), ZERO)
        equity = pool + sum((row["btc"] for row in balances.values()), ZERO) * mark
        reservations = {sleeve: sum((D(order["reserved_usd"]) for order in active_orders(database, "buy", sleeve)), ZERO)
                        for sleeve in ("dca", "swing")}
        free_pool = pool - sum(reservations.values(), ZERO)
        reserved_cash = min(free_pool, max(targets[2], D(self.op["minimum_cash_fraction"])) * equity)
        available = free_pool - reserved_cash
        needs = {sleeve: max(ZERO, targets[index] * equity - balances[sleeve]["btc"] * mark - reservations[sleeve])
                 for index, sleeve in enumerate(("dca", "swing"))}
        total_need = sum(needs.values(), ZERO)
        allocated = min(available, total_need)
        for sleeve in ("dca", "swing"):
            amount = floor_step(allocated * needs[sleeve] / total_need, CENT) if total_need > 0 else ZERO
            balances[sleeve]["cash"] = reservations[sleeve] + amount
        balances["reserve"]["cash"] = pool - balances["dca"]["cash"] - balances["swing"]["cash"]
        new_targets = [str(value) for value in targets]
        if new_targets != state["targets"]:
            state["dca_budget_reference"] = str(self.config.initial_cash * targets[0])
        state["targets"], state["allocation_updated_at"] = new_targets, now.isoformat()
        event(database, now, "paper_cash_targets", {"targets": new_targets, "regime": features.regime if features else "unknown"})

    def _entry_room(self, balances, mark, entry):
        cash = sum((value["cash"] for value in balances.values()), ZERO)
        btc_value = sum((value["btc"] for value in balances.values()), ZERO) * mark
        equity = cash + btc_value
        cost = entry * (ONE + self.fee)
        delta = cost - mark
        cap = D(self.op["max_btc_exposure_fraction"])
        minimum_cash = D(self.op["minimum_cash_fraction"])
        exposure_room = (cap * equity - btc_value - 2 * CENT) / (mark + cap * delta)
        cash_room = (cash - minimum_cash * equity - 2 * CENT) / (cost - minimum_cash * delta)
        return max(ZERO, min(exposure_room, cash_room))

    def _entries(self, database, balances, state, quote, features, now, mark):
        if state["last_signal_asof"] == features.asof.isoformat():
            return
        state["last_signal_asof"] = features.asof.isoformat()
        entry = ceil_step(quote.ask * (ONE + self.slip), self.rules.quote_increment)
        room = self._entry_room(balances, mark, entry)
        dca = self.app["dca"]
        last_time = utc(state["last_dca_buy_at"]) if state["last_dca_buy_at"] else None
        cooldown_ok = last_time is None or (now - last_time).total_seconds() >= dca["cooldown_hours"] * 3600
        scheduled = state["next_dca_due_at"] is None or now >= utc(state["next_dca_due_at"])
        dip = state["last_dca_price"] is not None and features.close <= D(state["last_dca_price"]) * (ONE - D(dca["dip_trigger_fraction"]))
        if dca["enabled"] and room > 0 and cooldown_ok and (scheduled or dip) and not active_orders(database, None, "dca"):
            since = (now - timedelta(days=7)).isoformat()
            spent = sum((D(row[0]) + D(row[1]) for row in database.execute("SELECT notional_usd,fee_usd FROM paper_fills WHERE side='buy' AND sleeve='dca' AND occurred_at>?", (since,))), ZERO)
            reference = D(state["dca_budget_reference"])
            budget = min(self._available_cash(database, balances, "dca"), reference * D(dca["tranche_fraction_of_dca_budget"]),
                         max(ZERO, reference * D(dca["max_spend_fraction_per_7_days"]) - spent))
            buffer = 2 * CENT * (ceil_step(budget / D(self.op["paper_fill_cap_usd"]), ONE) + ONE)
            quantity = min(room, max(ZERO, budget - buffer) / (entry * (ONE + self.fee)))
            self._submit(database, balances, quote, now, "buy", "dca", quantity,
                         "scheduled_dca" if scheduled else "dip_dca", budget=budget)
        swing = self.app["swing"]
        if swing["enabled"] and features.swing_signal and balances["swing"]["btc"] == 0 and not active_orders(database, None, "swing"):
            plan = swing_plan(quote, features.atr, self.app, self.rules)
            if plan["target_atr_multiple"] > D(self.op["max_target_atr_multiple"]) or quote.bid <= plan["stop"]:
                event(database, now, "paper_swing_filtered", {"reason": "cost_adjusted_target_too_far_or_stop_crossed", "target_atr_multiple": str(plan["target_atr_multiple"])})
                return
            equity = sum((row["cash"] + row["btc"] * mark for row in balances.values()), ZERO)
            risk_budget = equity * D(swing["risk_fraction_of_equity"])
            budget = min(self._available_cash(database, balances, "swing"), equity * D(state["targets"][1]) * D(swing["max_notional_fraction_of_swing_budget"]))
            buffer = 2 * CENT * (ceil_step(budget / D(self.op["paper_fill_cap_usd"]), ONE) + ONE)
            quantity = min(room, max(ZERO, risk_budget - 4 * CENT) / plan["loss_per_btc"],
                           max(ZERO, budget - buffer) / (entry * (ONE + self.fee)))
            self._submit(database, balances, quote, now, "buy", "swing", quantity, "trend_volume_breakout",
                         budget=budget, atr=features.atr, risk_budget=risk_budget)

    def _fill_orders(self, database, balances, state, quote, now, mark, can_buy):
        if not self.rules.market_enabled:
            return 0
        liquidity = D(self.op["paper_fill_cap_usd"])
        orders = active_orders(database)
        orders.sort(key=lambda order: (order["side"] != "sell", order["sleeve"] != "swing", order["created_at"], order["id"]))
        count = 0
        for order in orders:
            if order["side"] == "buy" and not can_buy:
                continue
            remaining = D(order["quantity"]) - D(order["filled_quantity"])
            reference = quote.ask if order["side"] == "buy" else quote.bid
            price = ceil_step(reference * (ONE + self.slip), self.rules.quote_increment) if order["side"] == "buy" else floor_step(reference * (ONE - self.slip), self.rules.quote_increment)
            if price <= 0 or liquidity <= 0:
                continue
            quantity = min(remaining, liquidity / price)
            sleeve = order["sleeve"]
            position = database.execute("SELECT * FROM paper_positions WHERE sleeve=?", (sleeve,)).fetchone()
            if order["side"] == "buy":
                funds = min(D(order["reserved_usd"]), balances[sleeve]["cash"])
                quantity = min(quantity, max(ZERO, funds - 2 * CENT) / (price * (ONE + self.fee)), self._entry_room(balances, mark, price))
                if sleeve == "swing":
                    plan = swing_plan(quote, D(order["atr"]), self.app, self.rules)
                    stop = max(D(position["stop_price"] or "0"), plan["stop"])
                    expected_proceeds = floor_step(stop * (ONE - self.slip), self.rules.quote_increment) * (ONE - self.fee)
                    existing_risk = max(ZERO, D(position["cost_basis_usd"]) - D(position["quantity"]) * expected_proceeds)
                    quantity = min(quantity, max(ZERO, D(order["risk_budget_usd"]) - existing_risk - 4 * CENT) / (price * (ONE + self.fee) - expected_proceeds))
            else:
                quantity = min(quantity, balances[sleeve]["btc"])
            quantity = floor_step(quantity, self.rules.base_increment)
            if quantity <= 0:
                if order["side"] == "buy":
                    self._cancel_buys(database, now, "remaining_budget_or_exposure_exhausted", sleeve)
                continue
            exact_notional = quantity * price
            notional = ceil_step(exact_notional, CENT) if order["side"] == "buy" else floor_step(exact_notional, CENT)
            fee = ceil_step(notional * self.fee, CENT)
            old_quantity, old_basis = D(position["quantity"]), D(position["cost_basis_usd"])
            realized = ZERO
            if order["side"] == "buy":
                cost = notional + fee
                balances[sleeve]["cash"] -= cost
                balances[sleeve]["btc"] += quantity
                new_quantity, new_basis = old_quantity + quantity, old_basis + cost
                average = (D(position["average_entry"]) * old_quantity + price * quantity) / new_quantity
                opened_at = position["opened_at"] or now.isoformat()
                stop_price, target_price = position["stop_price"], position["target_price"]
                if sleeve == "swing":
                    stop = max(D(stop_price or "0"), floor_step(price - D(self.app["swing"]["atr_multiplier"]) * D(order["atr"]), self.rules.quote_increment))
                    unit_basis = new_basis / new_quantity
                    unit_loss = max(ZERO, unit_basis - floor_step(stop * (ONE - self.slip), self.rules.quote_increment) * (ONE - self.fee))
                    rounding_buffer = 2 * CENT * (ceil_step(new_quantity * price / D(self.op["paper_fill_cap_usd"]), ONE) + ONE) / new_quantity
                    target = ceil_step((unit_basis + D(self.app["swing"]["take_profit_r_multiple"]) * unit_loss + rounding_buffer)
                                       / ((ONE - self.slip) * (ONE - self.fee)), self.rules.quote_increment)
                    stop_price, target_price = str(stop), str(target)
                reserve = max(ZERO, D(order["reserved_usd"]) - cost)
                if sleeve == "dca":
                    state["last_dca_buy_at"], state["last_dca_price"] = now.isoformat(), str(price)
                    if order["reason"] == "scheduled_dca":
                        state["next_dca_due_at"] = (now + timedelta(hours=self.app["dca"]["schedule_hours"])).isoformat()
            else:
                allocated_basis = old_basis if quantity == old_quantity else floor_step(old_basis * quantity / old_quantity, CENT)
                realized = notional - fee - allocated_basis
                balances[sleeve]["cash"] += notional - fee
                balances[sleeve]["btc"] -= quantity
                new_quantity, new_basis = old_quantity - quantity, old_basis - allocated_basis
                average, opened_at = D(position["average_entry"]), position["opened_at"]
                stop_price, target_price, reserve = position["stop_price"], position["target_price"], ZERO
                if new_quantity == 0:
                    new_basis, average, opened_at, stop_price, target_price = ZERO, ZERO, None, None, None
            database.execute("UPDATE paper_positions SET quantity=?,cost_basis_usd=?,average_entry=?,opened_at=?,stop_price=?,target_price=? WHERE sleeve=?",
                             (str(new_quantity), str(new_basis), str(average), opened_at, stop_price, target_price, sleeve))
            filled = D(order["filled_quantity"]) + quantity
            status = "filled" if filled == D(order["quantity"]) else "partial"
            database.execute("UPDATE paper_orders SET filled_quantity=?,status=?,reserved_usd=? WHERE id=?", (str(filled), status, "0" if status == "filled" else str(reserve), order["id"]))
            fill_id = hashlib.sha256(f"{order['id']}:{quote.quote_id}".encode()).hexdigest()
            rounding = abs(notional - exact_notional)
            database.execute("INSERT INTO paper_fills VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (fill_id, order["id"], quote.quote_id, now.isoformat(), order["side"], sleeve, str(quantity),
                              str(price), str(notional), str(fee), str(quantity * abs(price - reference)), str(rounding), str(realized)))
            event(database, now, "paper_fill", {"fill_id": fill_id, "order_id": order["id"], "side": order["side"],
                                                "sleeve": sleeve, "quantity": str(quantity), "price": str(price), "fee_usd": str(fee), "reason": order["reason"]})
            liquidity -= notional
            count += 1
        return count

    def _recover(self, database, balances, state, features, now, mark):
        if state["phase"] in {"active", "halted"}:
            return
        btc_value = sum((value["btc"] for value in balances.values()), ZERO) * mark
        if active_orders(database, "sell") or btc_value >= self.rules.minimum_notional:
            return
        state["phase"] = "cooldown"
        if self.op["resume_mode"] == "manual" or state.get("owner_pause", False):
            return
        valid = features is not None and features.is_fresh(now, self.op["feature_stale_after_seconds"])
        if not valid or features.regime != "bull" or features.confirmed_bear:
            state["recovery_days"], state["recovery_last_day"] = 0, None
            return
        day = features.daily_asof.date()
        last = datetime.fromisoformat(state["recovery_last_day"]).date() if state["recovery_last_day"] else None
        if last != day:
            state["recovery_days"] = state["recovery_days"] + 1 if last and day - last == timedelta(days=1) else 1
            state["recovery_last_day"] = features.daily_asof.isoformat()
        elapsed = (now - utc(state["pause_started_at"])).total_seconds()
        if elapsed >= self.op["resume_cooldown_hours"] * 3600 and state["recovery_days"] >= self.op["recovery_days"]:
            equity = sum((value["cash"] + value["btc"] * mark for value in balances.values()), ZERO)
            state.update(phase="active", pause_reason=None, liquidation_required=False, risk_epoch=state["risk_epoch"] + 1,
                         epoch_peak=str(equity), allocation_updated_at=None, last_signal_asof=None)
            event(database, now, "paper_recovery_resume", {"risk_epoch": state["risk_epoch"], "equity_usd": str(equity)})

    def request_liquidation(self, reason: str, now: datetime | None = None):
        """Persist an owner exit request; execution still needs a fresh quote."""
        if not reason.strip():
            raise ValueError("An operator reason is required")
        now = utc(now or datetime.now(timezone.utc))
        with connection(self.path, write=True) as database:
            state = state_from(database)
            if state["phase"] != "halted":
                self._pause(database, state, now, "owner_requested", liquidate=True)
            state["liquidation_required"], state["owner_pause"] = True, True
            if state["pause_started_at"] is None:
                state["pause_started_at"] = now.isoformat()
            self._cancel_buys(database, now, "owner_requested")
            save_state(database, state)
            event(database, now, "paper_owner_liquidation", {"reason": reason})
            return status_from(database)

    def manual_resume(self, reason: str, now: datetime | None = None):
        if not reason.strip():
            raise ValueError("An operator reason is required")
        now = utc(now or datetime.now(timezone.utc))
        with connection(self.path, write=True) as database:
            state, balances = state_from(database), balances_from(database)
            if state["phase"] in {"active", "halted"}:
                raise ValueError("Manual resume requires a paused portfolio; a capital-floor halt cannot be overridden")
            if not state["last_tick_at"] or not 0 <= (now - utc(state["last_tick_at"])).total_seconds() <= self.app["market"]["stale_after_seconds"]:
                raise ValueError("Resume requires a recent accepted quote")
            mark = D(state["last_price"])
            btc = sum((value["btc"] for value in balances.values()), ZERO)
            if active_orders(database) or btc * mark >= self.rules.minimum_notional:
                raise ValueError("Resume requires completed exits and no active orders")
            if (now - utc(state["pause_started_at"])).total_seconds() < self.op["resume_cooldown_hours"] * 3600:
                raise ValueError("Resume cooldown has not elapsed")
            equity = sum((value["cash"] + value["btc"] * mark for value in balances.values()), ZERO)
            if equity <= self.config.initial_cash * (ONE - D(self.op["max_lifetime_loss_fraction"])):
                raise ValueError("Original-capital floor prevents resume")
            state.update(phase="active", pause_reason=None, liquidation_required=False, owner_pause=False,
                         risk_epoch=state["risk_epoch"] + 1, epoch_peak=str(equity), allocation_updated_at=None, last_signal_asof=None)
            save_state(database, state)
            event(database, now, "paper_owner_resume", {"reason": reason, "risk_epoch": state["risk_epoch"]})
            return status_from(database)

    def tick(self, quote: Quote, features: Features | None, now: datetime | None = None):
        now = utc(now or datetime.now(timezone.utc))
        with connection(self.path, write=True) as database:
            state, balances = state_from(database), balances_from(database)
            if database.execute("SELECT 1 FROM paper_quotes WHERE id=?", (quote.quote_id,)).fetchone():
                return {"accepted": False, "duplicate": True, "new_fills": 0, "portfolio": status_from(database)}
            try:
                quote.check_fresh(now, self.app["market"]["stale_after_seconds"])
                if state["last_tick_at"] and now < utc(state["last_tick_at"]):
                    raise ValueError("Observation clock moved backwards")
            except ValueError as exc:
                self._cancel_buys(database, now, "invalid_quote")
                database.execute("INSERT INTO paper_quotes VALUES (?,?,0,?)", (quote.quote_id, now.isoformat(), json.dumps(quote.to_dict())))
                event(database, now, "paper_quote_rejected", {"reason": str(exc), "quote_id": quote.quote_id})
                return {"accepted": False, "duplicate": False, "reason": str(exc), "new_fills": 0, "portfolio": status_from(database)}
            database.execute("INSERT INTO paper_quotes VALUES (?,?,1,?)", (quote.quote_id, now.isoformat(), json.dumps(quote.to_dict())))
            database.execute("INSERT OR IGNORE INTO config_snapshots VALUES (?,?,?)", (self.config.checksum, now.isoformat(), self.config.canonical_json))
            state["config_checksum"] = self.config.checksum
            state["product_rules"] = {"base_increment": str(self.rules.base_increment), "quote_increment": str(self.rules.quote_increment),
                                      "minimum_notional": str(self.rules.minimum_notional), "market_enabled": self.rules.market_enabled}
            if features is not None:
                payload = {"config_checksum": self.config.checksum, "features": features.to_dict(),
                           "fresh": features.is_fresh(now, self.op["feature_stale_after_seconds"])}
                identity = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
                if state.get("last_feature_context") != identity:
                    event(database, now, "paper_feature_context", payload)
                    state["last_feature_context"] = identity
            mark = (quote.bid + quote.ask) / 2
            self._risk_actions(database, balances, state, quote, features, now, mark)
            self._protect_swing(database, balances, quote, now)
            fresh_features = features is not None and features.is_fresh(now, self.op["feature_stale_after_seconds"])
            spread_ok = (quote.ask - quote.bid) / mark <= D(self.op["maximum_spread_fraction"])
            can_buy = state["phase"] == "active" and fresh_features and spread_ok and self.rules.market_enabled
            if not can_buy:
                self._cancel_buys(database, now, "entries_blocked")
            for order in active_orders(database, "buy"):
                if (now - utc(order["created_at"])).total_seconds() > self.op["order_timeout_seconds"]:
                    self._cancel_buys(database, now, "buy_timeout", order["sleeve"])
            if can_buy:
                self._rebalance_cash(database, balances, state, features, now, mark)
                self._entries(database, balances, state, quote, features, now, mark)
            count = self._fill_orders(database, balances, state, quote, now, mark, can_buy)
            self._risk_actions(database, balances, state, quote, features, now, mark)
            self._recover(database, balances, state, features, now, mark)
            state["last_tick_at"], state["last_price"] = now.isoformat(), str(mark)
            equity = sum((value["cash"] + value["btc"] * mark for value in balances.values()), ZERO)
            state["lifetime_peak"] = str(max(D(state["lifetime_peak"]), equity))
            state["epoch_peak"] = str(max(D(state["epoch_peak"]), equity))
            drawdown = ONE - equity / D(state["lifetime_peak"])
            state["max_lifetime_drawdown"] = str(max(D(state["max_lifetime_drawdown"]), drawdown))
            save_balances(database, balances)
            save_state(database, state)
            cash = sum((value["cash"] for value in balances.values()), ZERO)
            btc = sum((value["btc"] for value in balances.values()), ZERO)
            database.execute("INSERT INTO paper_marks VALUES (?,?,?,?,?,?,?,?,?)", (quote.quote_id, now.isoformat(), str(mark), str(equity),
                             str(cash), str(btc), str(btc * mark / equity), str(drawdown), state["phase"]))
            return {"accepted": True, "duplicate": False, "new_fills": count, "entries_enabled": can_buy, "portfolio": status_from(database)}
