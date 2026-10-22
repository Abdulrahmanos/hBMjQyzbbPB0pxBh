# Historical LSTM comparison protocol

Declared 22 October 2026 before fitting this experiment. The question is whether an LSTM helps choose **DCA, swing or reserve**, compared with a deterministic rule choosing from exactly the same three modes. This extension is requested by the owner; the supplied Apziva brief does not mandate LSTM.

## Target and decisions

At each Monday 00:00 UTC, use the preceding 30 completed daily observations. Predict the next seven-day utility of each mode and the Bitcoin log return. Utility is `log(final equity / initial cash) - 0.75 * maximum drawdown`, with modeled costs included. The fourth output makes price-movement prediction separately inspectable; it does not directly determine allocation. Choose the largest predicted utility, preferring reserve on exact ties.

The three fixed profiles allocate 90% DCA / 10% reserve, 90% swing / 10% reserve, or 100% reserve. Entries, ATR exits, sizing and costs come from the existing historical simulator and identical application settings. Swing mode enables the existing entry rule; it does not buy immediately just because it was selected. DCA remains gradual. Mode selection therefore differs from selecting immediate BTC exposure.

The rules baseline selects reserve in bear, high-volatility or unknown regimes; otherwise it selects swing when the prior completed hourly bar has the configured breakout/volume signal and DCA otherwise. Additional controls always choose one mode, choose the best training-average mode, or use a ridge regression on the latest daily features. None sees test outcomes when making decisions.

Each episode starts from cash and liquidates at its end. Sequential episode returns compound, with hourly marked equity used for drawdown. Weekly liquidation costs apply equally to every policy. This is a deliberately bounded **flat-to-flat allocation experiment**, not a validation of continuous DCA inventory management or the live paper policy. It may penalize gradual accumulation and frequent switching. Spending caps/cooldowns reset at each episode and that limitation must remain visible.

The simulator also retains the legacy historical swing profit-target convention, rather than the later operational net-cost 2R calculation. At the configured high transaction costs, some legacy target exits can lose money after fees. This limitation applies identically to rules and LSTM but makes reserve more attractive; it prevents interpreting the result as a general rejection of improved swing execution.

## Data and time boundaries

Use the existing, checksummed Coinbase hourly archive. Do not download replacement prices or interpolate gaps. Discard episodes missing any of their 168 hours, and sequences missing any complete daily input. Daily features become usable only after the day ends. The simulator may use only signals from a completed prior bar for new entries. Episode labels become available at episode end.

Training outcomes must finish by 1 January 2024; validation episodes start after that boundary and finish by 1 January 2025; test episodes start after that boundary. Boundary-straddling episodes are excluded. Overlapping input history is allowed; overlapping future labels are not. Means/scales come only from training inputs and labels. Validation loss chooses the stopping epoch; test outcomes never choose thresholds, epochs or seeds.

All of these historical prices have already been inspected in earlier project research. The test is held out from this new model's fitting, **not an untouched project-level holdout**. Findings remain retrospective and exploratory.

## Model and assessment

Use a single-layer eight-unit PyTorch LSTM with four linear outputs, AdamW and three fixed seeds. The primary result averages all three predictions; do not publish only the best seed. Compare the same test weeks, identical cash, execution costs and action set. Report net return, hourly drawdown, fees, exposure, selected modes, prediction errors and per-seed results. Use a four-week moving-block bootstrap for the paired weekly log-return difference against rules. A confidence interval crossing zero is insufficient evidence of an improvement. This bootstrap does not account for every researcher's design choice or eliminate historical selection bias.

Artifacts include dated predictions versus realized outcomes, per-week choices, per-mode realized returns, train/validation loss, weights, input/config/code checksums, figures and a plain-language conclusion. Do not deploy or activate the LSTM automatically. The old paper experiment and its frozen source remain separate.
