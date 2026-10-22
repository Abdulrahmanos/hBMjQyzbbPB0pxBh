# Milestones and review gates

**22 October owner update:** continue through the historical LSTM comparison, requirements audit and GitHub publication without another milestone stop. The original review gates below are historical. Actual external notification verification remains dependent on the owner's bot and Gmail authorization; do not mark it complete from mocks.

The owner requested visible progress and an explicit stop after each milestone. Complete the current milestone, show its files and verification results, then wait for **continue**. Do not work through subsequent milestones during that stop.

| Milestone | Deliverable | Status |
| --- | --- | --- |
| 1. Foundation | Trading specification, configuration validator, CLI, simulated cash persistence | Complete; reviewed and continued |
| 2. Data and allocation research | Public candles, causal features, offline baselines, parameter/feature selection and charts | Complete; reviewed and continued |
| 3. Operational paper engine | Decimal execution/accounting, inventory-aware risk policy, stress paths, recurring real-data operation | Complete; reviewed and continued |
| 4. Adaptation and integrations | Restricted Google Sheet/cache, bounded shadow adviser, persistent trade alerts and weekly reports | Complete; reviewed and continued |
| 5. Deployment and evaluation | Docker packaging, local supervision, monitoring/backups and seven actual days of public-data paper operation | 19 October window interrupted overnight; same-account collection recovered; qualifying week still pending |
| 6. Final presentation | Local evaluation report, optional walkthrough, completion checklist | Interim report and local preview prepared; final observed week pending; uploads prohibited |

**19 October checkpoint:** the original forward window has zero qualifying days and a roughly 69-hour observation gap. The latest health check requires attention. Milestone 5 remains incomplete; this window cannot pass the declared seven-day coverage requirement. See the [dated publication checkpoint](publication-status.md). Preparing source publication does not complete the evaluation or start a replacement run.

**Later 19 October recovery and scope update:** the old window and a successful supervisor-recovery rehearsal are archived separately. A fresh evaluation began on 19 October at 12:12:29 Asia/Riyadh and ends on 26 October at that time if its criteria pass. The collector is healthy under the corrected hidden task. The owner's latest instruction prohibits all uploads and removes YouTube as a requirement. Continue locally only; see [completion-plan.md](completion-plan.md) and the [local checklist](completion-checklist.md).

**20 October interruption:** lid-triggered standby and later hibernation caused an approximately 11-hour-53-minute quote gap. Day 1 cannot pass its coverage threshold. The existing supervisor recovered on the same account; no reset or new start has occurred. Evidence and the audited snapshot are documented in the [interruption review](../reports/milestone-5/interruption-review-2026-10-20.md). A separate valid test requires continuous computer availability.

## Milestone 1 acceptance

- [x] Trading, accounting, and evaluation assumptions documented.
- [x] Default configuration accepted; invalid and unsupported configurations rejected.
- [x] CLI runs locally without third-party packages.
- [x] Initialization conserves the budget and starts with zero BTC.
- [x] Repeated initialization preserves persisted balances.
- [x] Verification results recorded below.
- [x] Ready for the owner review stop; milestone 2 remains pending.

## Planned milestone 2 review

Show the downloaded data coverage and quality findings, feature plots, and benchmark equity/drawdown curves. Describe funding and execution assumptions before interpreting returns. Historical date coverage depends on the API data actually available; report any gaps instead of filling them silently.

The owner expanded this milestone to investigate DCA/swing/reserve allocations selected from current features. This authorizes an offline research simulator and chronological parameter search here; the operational service remains milestone 3.

## Milestone 2 acceptance

- [x] Public hourly data downloaded, provenance/checksum recorded, and missing hours reported after recovery attempts.
- [x] Causal technical features and verified daily context implemented.
- [x] Baselines and continuous-inventory offline hybrid experiments include fees, slippage, and gap-aware stops.
- [x] Predeclared candidate grid, risk/return objective, training-only objective sensitivity, and context fallback implemented.
- [x] Walk-forward development and a frozen final 180-day holdout completed.
- [x] Research tests passed; saved portfolio status inspected.
- [x] All four figures visually reviewed and final review notes recorded.
- [x] Ready for the owner review stop; milestone 3 remains pending.

## Milestone 2 verification and findings

Completed on 2026-10-11. All 29 automated tests passed. An independent export audit reconciled cash and BTC inventory for 18 strategy-period comparisons and verified input checksums, model dates, allocation totals, and the holdout cutoff. Report regeneration from saved artifacts succeeded without refitting. The original persisted simulated portfolio remained at its initial balances.

Downloaded 59,384 hourly candles covering 2020-01-01 through 2026-10-10. Six focused gap-recovery attempts recovered no additional data; 16 missing hours remain explicitly recorded. The simulator handles them without imputing prices. Research evaluated 47 candidate profiles over 23 nonoverlapping development episodes, with 20 walk-forward model fits and a frozen final 180-day holdout.

The final holdout feature selector returned 14.37% with 14.66% maximum drawdown; buy-and-hold returned 9.82% with 29.48% drawdown. The longer development selector suffered 72.30% drawdown, demonstrating that target changes and paused buys do not sufficiently protect existing DCA holdings. The swing-only holdout lost 20.64% under the assumed costs; four of its 18 profit-target exits lost money after fees/slippage.

These findings do not establish a deployable optimum. The recently observed holdout cannot become an untouched test for future revisions. Milestone 3 should address existing-inventory exits, portfolio-state constraints, pause/resume rules, and net profitability of swing targets before creating a recurring paper service.

See the [full report and charts](../reports/milestone-2/review.md) and [independent verification record](../reports/milestone-2/verification.json). Research dependency versions are pinned in `requirements-research.lock.txt`.

## Milestone 3 acceptance

- [x] Decimal ledger, reservations, partial fills, minimum sizes, and persistent inventory implemented.
- [x] Quote replay/restart protection, transaction rollback, stale-data controls, and observed market rules tested.
- [x] Existing-inventory exits, exposure/cash controls, recovery epochs, lifetime capital floor, and owner controls implemented.
- [x] Swing targets account for costs; narrow opportunities filtered and entries default to disabled.
- [x] Public-data demo created one paper fill; restart preserved balances and made no extra purchase.
- [x] Four constructed paths compared across two policies; all eight ledgers reconciled.
- [x] Modeling guide with saved examples created.
- [x] All 53 automated tests passed.
- [x] Final chart/document review completed; ready for the owner review stop.

## Milestone 3 findings

The constructed year-long 80% decline ended at -61.72% for the pause-only control and -2.95% for the combined defensive controls. The comparison uses different pre-shock exposure (about 70% versus 60%), includes cash reallocations, and is not an isolated estimate of an exit rule or a forecast of returns. A 50% drop during a quote outage still produced -30.52% under the defensive controls, passing the 30% loss trigger before liquidation could complete. Recovery retains lifetime loss and the original funding floor.

The real-data demo and restart use only simulated capital. The operational database holds one DCA fill, and its cash/inventory audit reconciles exactly. No background service remains running. The original research input checksum remains unchanged, and the previously observed holdout will not be reused as independent validation of new policies.

Review the [milestone 3 report](../reports/milestone-3/review.md), [stress chart](../reports/milestone-3/stress_tests.png), and [modeling guide](modeling-guide.md).

## Milestone 4 acceptance and evidence

- [x] Owner-requested private Google configuration Sheet created through native import, read back and visually checked.
- [x] Strict complete-candidate validation, hourly refresh/backoff and atomic cache implemented.
- [x] Funding/core risk remain local; pending buys cancel on edits without resetting inventory or lifetime history.
- [x] Shadow adviser with explicit provider, schema, quotas, timeout, persisted inputs and deterministic fallback implemented.
- [x] Fill outbox, local inbox, Telegram/Gmail adapters and ambiguous-delivery handling implemented.
- [x] Monday 09:00 Riyadh period accounting, catch-up/latest-period scheduling, partial/stale labels and duplicate prevention verified.
- [x] Final 78-test suite passed, including quote ticks during a blocked integration and native Sheet numeric conversion.
- [x] Eight public-data quote cycles completed, one new paper fill, local alert delivered; restart created no duplicates.
- [x] Final portfolio audit reconciled two total fills, $9,800.06 cash and 0.00240330 BTC exactly.
- [x] Research data checksum and original foundation portfolio preserved.
- [x] Owner review stop reached; deployment and seven-day evaluation remain pending.

The owner chose a local fake inbox because this is a project without email/Telegram destinations. No messages were sent externally and no real LLM API call occurred. The demo's shadow response is a deterministic fixture. The created Sheet is private, but the Python runtime currently reads its initial verified local copy. Runtime Google OAuth, live provider credentials and real API delivery remain unverified optional setup. This is not a claim of live integration certification or LLM investment benefit.

Review the [milestone 4 report](../reports/milestone-4/review.md), [local inbox](../reports/milestone-4/inbox/index.html), and [setup/methodology guide](integrations.md). Actual continuous deployment starts only after the next **continue**.

## Milestone 5 deployment checkpoint

- [x] Owner chose their Windows computer; no cloud account needed.
- [x] Fresh $10,000 account, source/environment/config manifest and simultaneous cash/DCA/buy-and-hold books implemented.
- [x] Single-writer locks, supervision, graceful stop, stale health checks, rotating logs and audited backups implemented.
- [x] Actual worker ownership checked; Windows venv launcher indirection corrected and tested.
- [x] Per-user limited Task Scheduler registration and startup verified.
- [x] Controlled worker interruption recovered without resetting cash/BTC or adding a duplicate fill.
- [x] All 98 automated tests passed, including a clearly constructed coverage fixture that does not count as forward data.
- [x] Docker/Compose source prepared; Docker is absent, so container build/runtime remains unverified.
- [x] Implementation review stop reached while the authorized collector keeps running.
- [ ] Seven actual days collected with the declared daily quote-age coverage and integrity criteria.
- [ ] Final forward evaluation, incident/coverage review and milestone 5 completion.

The live window started **2026-10-16 14:02:48 Asia/Riyadh** and ends **2026-10-23 14:02:48 Asia/Riyadh**. A short prior startup check is archived separately. None of the October 11–12 demos, historical candles, overnight elapsed time before launch or synthetic unit/stress data counts toward the week. Source/strategy changes would require a separate experiment. Google edits and LLM advice are disabled for this frozen-policy run; local fake notifications remain enabled.

The machine must stay awake, signed in and online for consistent collection. The logon task can resume after an interruption, but cannot create missing observations. See the [deployment guide](deployment.md) and [updating report](../var/forward/reports/evaluation.md). Do not call this milestone complete while the week or its review remains outstanding.

## Credential timing

Milestones 1–3 should use public data and local simulation wherever available. Ask for integration-specific setup only when milestone 4 needs connected services. Never ask the owner to paste API keys or passwords into chat. Cloud-account choices are needed at milestone 5. Public publishing is a concrete final review item at milestone 6.

## Milestone 1 verification

Verified on 2026-10-11 with Python 3.14.6:

- `python -m unittest discover -s tests -v`: 12 tests passed, including invalid-configuration subcases and state-preservation checks.
- `python -m bitcoin_agent validate-config`: accepted the default configuration and conserved $10,000 across the three buckets.
- First `init-db`: created the simulated portfolio with zero BTC and one initialization event.
- Second `init-db`: preserved balances and retained one event.
- `status`: successfully read saved state.

Windows sandbox restrictions blocked Python's private temporary directories. Virtual-environment setup and the test suite were completed using the requested execution approvals. The local application commands ran successfully in the normal sandbox. See [milestone 1 review](milestone-1-review.md).
