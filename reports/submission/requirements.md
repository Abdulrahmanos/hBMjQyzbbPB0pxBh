# Apziva requirements audit

Current evidence as of **27 October 2026**, reviewed against the full brief supplied by the owner. Source and research are ready for review. **The new seven-day experiment and first scheduled weekly email have not yet completed.**

| Requirement | Actual status and evidence |
| --- | --- |
| Configurable budget, DCA, ATR swing stops and strategy modes | Implemented and tested with simulated capital; [engine](../../bitcoin_agent/paper_engine.py) and [methodology](../../docs/methodology.md). The current frozen configuration has swing disabled. |
| Adaptive behavior and justified LLM use | Rules operate; genuine free local-model inference and an in-run receipt were observed on AWS. The model is a shadow adviser and does not control trades or prove improved returns. |
| Hourly Google Sheets controls and cached fallback | AWS service-account read of all 16 controls verified; hourly validated cache worker running. **Edits are staged during the frozen experiment**, not applied to its trades. [Deployment and limitations](../../docs/cloud-connectors.md). |
| Telegram alert on each committed buy/sell | AWS worker running; both existing candidate paper fills delivered as labelled catch-up, once each with receipts. Earlier synthetic BUY/SELL checks remain separate. [AWS evidence](aws-connectors-verification.json). |
| Gmail summary Monday at 09:00 | Local and AWS tests have provider receipts. Separate sender runs on AWS, due Monday 09:00 Asia/Riyadh. First scheduled delivery is 2 November and remains unobserved. [Gmail evidence](gmail-verification.json). |
| Dockerized, cloud-deployable operation | AWS services are deployed and running; Linux CI checks passed. Current healthy operation is not proof of uninterrupted seven-day uptime. |
| One-week real-data evaluation | AWS window started 26 October 09:52:34 and ends 2 November 09:52:34 Asia/Riyadh. Await the actual boundary and independent coverage, freeze, ledger, quote-pairing and backup audit. Earlier interrupted accounts remain preserved. |
| Clean code and setup/system report | [README](../../README.md), [project report](../../docs/project-report.md), deployment guides, tests and redacted evidence provided. |
| Public YouTube video | Explicitly waived by the owner; not a remaining completion requirement. |
| Owner-requested historical LSTM comparison | Completed exploratory experiment; the model chose cash throughout the test and did not outperform the cash baseline. [Results](../lstm-comparison/review.md). |

## Remaining evidence and limits

The final week cannot be manufactured from historical data, synthetic tests or elapsed calendar time. The run's initial adviser outage and storage repair must remain in its report. No reset, extension or change to the frozen source/settings is authorized by these integration deployments.

The first Monday Gmail snapshot occurs about 52 minutes before the seven-day experiment closes, so it is explicitly a partial report, not the final audit. Gmail's External/Testing authorization expires after seven days and needs renewal for later weekly deliveries. The Sheets service account is separate and does not inherit that seven-day user-token expiry.

Sheet reads, validation, cache fallback and staging are verified. Live application of edited controls to the current frozen account is deliberately disabled; do not describe staged changes as trades driven by the Sheet. A later declared configuration can use the existing validated configuration mechanism.

Personal destinations, Sheet identifiers, account IDs and credentials remain outside public source. No new Codex heartbeat or Windows collector task was created. The owner submits the repository; no Apziva submission or video upload has been performed.

The brief does not require profitable results, LSTM, a 99% uptime threshold or seven separately qualifying daily windows. Those model/protocol choices are reported honestly, including failures. The project still needs the actual prospective evidence and final review before claiming full validation.
