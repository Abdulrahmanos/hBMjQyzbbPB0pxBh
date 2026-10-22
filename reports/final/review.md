# Bitcoin paper evaluation

Status: **incomplete; interim observations only**

Generated: 2026-10-20T06:24:18.820452+00:00

## Research question

Assuming Bitcoin remains operational and tradable, does the declared defensive accumulation policy improve returns and drawdown relative to simpler policies? Historical research and this revised forward policy are separate experiments.

## Evaluation protocol

Fresh $10,000 simulated accounts, identical prospective observations, idle cash included, 0.6% assumed fees and 0.1% adverse slippage per fill. Public bid/ask and product increments enter execution. The artificial $1,000-per-quote fill cap is not measured liquidity. Open holdings are marked at the midpoint without a hypothetical final sale. Cash earns no interest.

Only actually received quotes count. Each of seven anchored 24-hour periods must reach 99% quote-age coverage. Historical indicator warm-up and constructed tests do not fill gaps. The first failed October 16 window and October 19 recovery rehearsal remain archived separately.

Window: 2026-10-19T09:12:29.536612+00:00 to 2026-10-26T09:12:29.536612+00:00.

Completed days: 0/7; qualifying days: 0/7; observations: 6586.

## Observed comparisons

| Strategy | Equity USD | Return | Observed max drawdown | BTC |
| --- | ---: | ---: | ---: | ---: |
| scheduled_dca | 9,998.01 | -0.020% | 0.020% | 0.00120047 |
| buy_and_hold | 9,803.56 | -1.964% | 1.995% | 0.12008706 |
| cash | 10,000.00 | 0.000% | 0.000% | 0 |
| candidate | 9,998.01 | -0.020% | 0.020% | 0.00120023 |

The candidate starts gradually and retains defensive exit rules; buy-and-hold commits available cash, and scheduled DCA excludes dip/defensive rules. Exposure differences are part of the policy comparison and must be considered alongside returns. A short observed advantage does not establish durable profitability.

## Daily coverage

| Day | Full 24 hours | Quote coverage | Qualifies |
| --- | --- | ---: | --- |
| 1 | False | 43.909% | False |

## Independent checks

- PENDING / FAIL: prospective week complete.
- PENDING / FAIL: seven individually qualifying days.
- PASS: candidate and three baselines reconcile.
- PASS: all accepted quotes paired.
- PASS: same application source.
- PASS: same configuration.
- PASS: same calculation environment.
- PASS: public stream provenance.
- PASS: no observer or config incident.
- PENDING / FAIL: window closed and worker stopped.

The point-in-time SQLite backup passed integrity and independent balance reconstruction. Its hash is recorded in verification.json; the database stays local. evaluation.json records the configuration, environment, observations and limits. The application source is frozen for this experiment.

## Interpretation and limits

Coverage measures time under the configured quote-age limit. Prices are never imputed; observed drawdown can miss movements between quotes. Marked equity excludes hypothetical final exit costs. Seven days do not establish durable investment performance.

A passing week demonstrates operation under that week's conditions. It does not validate personal allocations, the long-term value of Bitcoin or protection through every market outage. Optional real messaging, OAuth, LLM advice and Docker deployment remain unverified; they do not influence this experiment.

The historical feature selector's 14.37% holdout return came with a 72.30% development drawdown; it was not promoted into this operational policy. The original swing rule lost money after costs, and swing entries remain disabled here. These are limitations that informed the operational controls, not hidden tuning successes.
