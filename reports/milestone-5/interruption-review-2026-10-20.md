# Overnight paper-run interruption, 20 October 2026

The 19–26 October prospective window cannot pass the declared seven-day protocol. Its first anchored day has exceeded the 864-second uncovered-time allowance. Collection is running again on the same account, and no new experiment has been started.

## Observed evidence

- Last quote before the outage: 19 October 2026 at 18:28:33.420323 UTC (21:28:33 Riyadh).
- Windows Kernel-Power event 506 at 18:28:33.4653103 UTC records Modern Standby with reason `Lid`.
- Later Windows events record battery-budget standby and hibernation. Resume from low power was recorded at 20 October 06:19:27 UTC.
- Next received prospective quote: 20 October 06:21:15.259062 UTC. The observed quote gap was 42,761.838739 seconds, approximately 11 hours 53 minutes.
- The existing supervisor, PID 43348, recorded the old worker's exit and launched worker PID 41052 at 06:21:02 UTC. No agent-initiated termination, restart, account reset or source/configuration change was performed.
- At 06:22:33 UTC, the collector was healthy, all 6,565 accepted quotes had matching forward observations, the candidate and all three baselines reconciled, and source/configuration/calculation-environment checks passed.

The Windows power events explain the long interruption. The supervisor's scoped idle-sleep prevention request does not override lid-triggered standby or hibernation. No permanent Windows power settings were changed.

## Preserved evidence and accounting

The independently verified SQLite snapshot is `var/completion/snapshots/interruption-20261020T0620.sqlite3`, created at 06:21:46 UTC. SHA-256: `f3e24881394b0c328eea68439c1f543a3f3e4c098e79148e732b2289bf10c78b`.

Its audit reconstructed candidate cash of $9,900.03, 0.00120023 BTC in DCA inventory, $0.60 fees and one fill, with zero cash difference. All three baseline books reconciled. The scheduled backup created at 06:20:59 UTC also passed hash and ledger checks.

Windows power events and task details are saved under `var/completion/incidents/20261020-overnight/`. The current account remains at `var/forward/`; the original October 16 account and October 19 recovery rehearsal remain archived separately. The gap remains missing data; historical candles and elapsed time do not replace it.

## Next step

Keep monitoring this account as interrupted operational evidence while confirming whether the computer can remain plugged in, signed in, online and with its lid open for a separate seven-day test. The original start, deadline, threshold and frozen policy remain unchanged. No passing result or final project completion is claimed. Uploads remain disabled, and YouTube is not required.

## Later 20 October check

Day 1 closed with 50.460% quote coverage and did not qualify. Windows recorded another lid-triggered standby from approximately 10:01:48 to 10:27:23 UTC. By 10:32:30 UTC, day 2 had accumulated 1,466.96346 uncovered seconds, exceeding the same 864-second allowance. The existing supervisor recovered the worker on the same account. A transient feature warning during startup cleared; all 9,213 accepted quotes were paired, all four books reconciled, and source/configuration/environment checks passed. Evidence is recorded in `var/completion/incidents/20261020T100148-lid-standby-recovered.json`. This is a recurrence of the already reported power interruption; the pending availability question remains unchanged.
