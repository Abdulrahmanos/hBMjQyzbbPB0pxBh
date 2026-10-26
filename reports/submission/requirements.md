# Apziva requirements audit

Reviewed against the full brief pasted by the owner on **22 October 2026**. The repository is ready for code/research review, but **not every live integration or deployment criterion has been verified**. Credentials and a reliable host cannot be invented by a backtest.

| Requirement | Evidence | Actual status |
| --- | --- | --- |
| Configurable budget; time/dip DCA; ATR swing stops; strategy modes | [Operational engine](../../bitcoin_agent/paper_engine.py), [configuration](../../config/paper.json), tests | Implemented and tested with simulated capital |
| Adaptive strategy behavior; justified LLM use | [Methodology](../../docs/methodology.md), [adviser](../../bitcoin_agent/adviser.py) | Rule adaptation works; LLM is restricted to shadow suggestions; real calls unverified |
| Hourly Google Sheet controls and cached fallback | [Configuration module](../../bitcoin_agent/remote_config.py), [integration review](../milestone-4/review.md) | Code/cache tests and native Sheet demonstration complete; runtime OAuth unverified |
| Telegram alerts for every buy and sell | [Notification module](../../bitcoin_agent/notifications.py), [real delivery verification](telegram-verification.json) | Two synthetic fills passed through the real Telegram outbox with BUY/SELL receipts, one attempt each and no duplicate delivery on re-scan |
| Gmail report each Monday at 09:00 | [Notification setup](../../docs/notification-setup.md), [saved report](../milestone-4/weekly-report.md) | Real Gmail self-test has a provider receipt; Monday timing and isolated outbox tested; AWS installation pending ([record](gmail-verification.json)) |
| Dockerized and cloud-deployable operation | [Dockerfile](../../Dockerfile), [Compose](../../compose.yaml), [actual CI run](https://github.com/Abdulrahmanos/qytbcf1pULIvwxUO/actions/runs/39750052488) | Image build, Compose validation and container configuration command passed on Linux; no cloud server or uninterrupted service verified |
| One-week real-data evaluation log | [Actual observations and daily coverage](../real-data-evaluation/review.md) | Logs span 11–22 October with separate accounts and explicit gaps; continuous 24/7 week failed |
| Clean code and setup/system report | [README](../../README.md), [project report](../../docs/project-report.md) | Provided |
| Public YouTube video | Owner's explicit correction | Waived by owner; no video upload performed |
| LSTM comparison requested by owner | [Protocol](../../docs/lstm-experiment.md), [results](../lstm-comparison/review.md) | Complete exploratory historical experiment; no advantage over always holding cash |

The brief does not mandate LSTM, a 99% uptime threshold, or seven separately qualifying daily windows. Those were additional research/operational choices. Their failures remain documented. The supplied brief still asks for real-data evaluation and robust operation; an interrupted log cannot prove reliable 24/7 deployment.

## What the owner still needs to supply

Telegram bot/private-chat setup and real BUY/SELL delivery have been verified using synthetic prices. Complete Gmail Desktop OAuth using the [guide](../../docs/notification-setup.md), then run its explicit delivery test and retain the provider receipt. Phone numbers and private usernames alone cannot configure Telegram Bot API delivery. Personal destinations and secrets stay in ignored `.env`.

For unattended operation, keep the process/network available or deploy the supplied container on an appropriate host. Google Sheet and optional LLM providers require their own credentials and verification. The noisy Codex heartbeat was deleted, and the obsolete Windows collector task was disabled. Nothing in this repository silently reinstalls them.

## 24 October cloud preparation

The owner requested real LLM use and a new week on an always-on server, then selected a free hosting alternative. [AWS Free-plan setup](../../docs/cloud-evaluation.md), a separate paper account and a read-only OpenAI observer are prepared. All 127 local tests pass, including ten new cloud checks. The owner is creating the free account and saving their own API key locally. **No server deployment, actual OpenAI response or new prospective day is yet verified.** Earlier results and missing Gmail/Sheet authorization remain as stated above.

## 25 October free-model update

The owner chose a free local Qwen model instead of OpenAI API billing. A pinned CPU runtime, model-file verification, private inference network and bounded shadow adviser are prepared. All 143 local tests pass. The AWS base image and market-feed preflight are confirmed; a real model response on the owner's server and a completed continuous week remain pending. No API key is required for this deployment. Gmail and runtime Sheet OAuth remain separately unverified.

## Gmail update — 26 October 2026

Send-only OAuth and one labelled real Gmail test email have been verified. The isolated cloud sender passes eight new tests plus 25 existing integration tests. It has not yet been installed on AWS, and no scheduled weekly report has yet been observed. See [cloud Gmail setup](../../docs/cloud-gmail.md). Personal destinations and secrets remain private.
