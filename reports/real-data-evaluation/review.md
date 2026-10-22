# Real-data operation and interruption log

**The system did not achieve continuous 24/7 operation.**

This log exports actually received quotes and paper-account marks. It spans more than one calendar week, but contains substantial outages. A calendar span is not a continuous week of testing. Separate accounts and the recovery rehearsal remain separate; their returns are not joined into a fictional portfolio.

| Account | First actual quote (UTC) | Last actual quote (UTC) | Accepted quotes | Largest quote gap (hours) | Audit |
| --- | --- | --- | ---: | ---: | --- |
| public_connectivity_demo | 2026-10-11T10:51:03.843432+00:00 | 2026-10-12T17:42:25.818997+00:00 | 14 | 29.90 | PASS |
| oct16_interrupted | 2026-10-16T11:02:48.767398+00:00 | 2026-10-19T08:34:07.803825+00:00 | 433 | 68.92 | PASS |
| oct19_recovery_rehearsal | 2026-10-19T08:54:30.393551+00:00 | 2026-10-19T09:12:21.501656+00:00 | 210 | 0.01 | PASS |
| oct19_interrupted | 2026-10-19T09:12:29.536612+00:00 | 2026-10-22T08:23:14.486673+00:00 | 16255 | 18.05 | PASS |

## Evidence and interpretation

- `daily-coverage.csv` includes empty days inside each account's observation span. Coverage counts only the 120-second validity interval of an actual received quote.
- `observed-hourly-marks.csv` contains the last actual mark in each observed hour; missing hours are not manufactured.
- `evaluation.json` records provenance, fills, independently reconstructed cash/BTC, and hashes of consistent private SQLite snapshots.
- The original October 16 account, October 19 recovery rehearsal and replacement account retain distinct identities.
- Windows evidence attributes repeated long gaps to lid-triggered standby and battery-budget hibernation; other unproven causes remain unassigned.
- The owner's local-computer choice was used. Reliable always-on operation and on-time Monday email require an available process, network and credentials.
- The earlier 99% daily threshold was an additional project protocol, not a numerical requirement in the supplied Apziva brief. Its failure is preserved rather than relabelled as success.

The paper collector was stopped during final packaging, with its account preserved. No live-money orders were submitted. No historical warm-up or constructed stress path is counted as prospective evidence.
