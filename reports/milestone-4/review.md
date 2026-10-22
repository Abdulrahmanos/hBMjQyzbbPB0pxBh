# Milestone 4 review

Completed on 2026-10-12. The owner can now review a local demo inbox without email or Telegram accounts. A private Google Sheet holds the initial nonsecret strategy controls. Configuration validation/cache, live adapter code and a bounded shadow adviser are implemented. The live provider paths remain unconfigured and were tested with mocks.

## Open the deliverables

- [Your local inbox](inbox/index.html): two paper trade alerts and one Monday report.
- [Monday report text](weekly-report.md): period-filtered saved portfolio figures.
- Private configuration Sheet: 16 supported controls and the local risk limits. Its identifier remains local; the portable initial controls are in [sheet-rows.json](../../config/sheet-rows.json).
- [Integration setup and methodology](../../docs/integrations.md): how configuration, advice, recovery and reporting work.
- [Verification record](verification.json), [test record](test-results.json) and [shadow record](adviser-records.json). The private native Sheet readback is excluded from publication.

## What was verified

All 78 automated tests passed. Integration cases cover bad/duplicate/incomplete Sheet values, native numeric conversion, cached restart, atomic write failure, fixed risk/funding bounds, retained inventory and existing stops, reservations canceled on updates, independent delivery recovery, uncertainty after interrupted network delivery, weekly time boundaries, future-fill exclusion, stale/partial coverage, daily adviser quotas, malformed/refused output and quote ticks while an integration is blocked.

The bounded [public-data run](public-demo.json) accepted eight quotes and made one new paper DCA fill. Feed and feature failures were zero. The new alert reached the local inbox. The [restart demo](restart-demo.json) created no duplicate delivery or trade. These short checks demonstrate plumbing and persistence, not seven days of reliability or profitable investing.

The operational ledger now has two fills, $9800.06 cash and 0.00240330 BTC. Total modeled fees are $1.20; independent cash difference is $0.00. The last saved marked equity is $9997.95; it is an observation from the bounded run, not a current live value. The original foundation example still holds its initial $10,000 and zero BTC. Archived research data retains its original SHA256.

The Monday report ends at 2026-10-12 09:00 Riyadh (06:00 UTC), before today's later demo trade. It therefore contains **one** fill and closes at $9999.38. Independent calculations reproduce its return and observed drawdown exactly. It labels a partial week and an old closing price. The later trade alert belongs to the next report period.

The native Google Sheet and the local inbox were visually inspected in the browser. The trade cards and full weekly report were readable. The temporary local preview server was stopped after inspection. The saved HTML and Markdown remain available without that server.

## How authority is limited

Sheet edits cannot change funding or core risk limits. Accepted changes preserve inventory and lifetime state and do not widen an existing swing stop. Invalid edits retain accepted configuration; a valid local baseline is the final fallback. The demo reads the verified initial local controls, while automatic private Sheet reads need separate Python OAuth.

The adviser is shadow-only. One record came from `deterministic-demo-fixture`, explicitly labeled as a test fixture. No actual LLM call or order influence occurred. There is no evidence here of LLM return improvement. Paid provider requests require account-specific credentials/model selection and spending limits; attempts/spacing/payload/output size are bounded locally.

Delivery failures do not create another trade. Local preview IDs survive restart. Live timeouts may be ambiguous and are held for inspection, because provider delivery cannot be guaranteed exactly once. Real email/Telegram delivery and OAuth have not been verified. No external messages were sent and no service remains running.

## Review stop

Milestone 4 implementation and the requested fake inbox demo are complete. Milestone 5 remains pending: Docker/service deployment, monitoring and **seven actual consecutive days** of observed paper operation. Historical replay and constructed stress cases will not count as those seven days. Wait for the owner's next **continue** before beginning that work.
