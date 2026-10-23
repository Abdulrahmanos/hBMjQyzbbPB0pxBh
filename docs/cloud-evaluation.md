# Free-plan server and real OpenAI verification

Prepared 24 October 2026. **Deployment has not started; no new prospective week is claimed.** Earlier interrupted accounts and their dates remain preserved. No Windows task or chat heartbeat is re-enabled.

## Owner setup

Create an account through [AWS Free Tier](https://aws.amazon.com/free/) and select **Free account plan**, if eligible. AWS currently gives new customers $100 of credits; Free plan ends after six months or exhausted credits and does not charge unless upgraded to Paid. Check eligibility and remaining credits in the console. Do not upgrade to Paid for this test.

[Official plan rules](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html) and [eligible EC2 options](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-free-tier-usage.html).

OpenAI API usage is separate from AWS credits and is not promised free. Configure a restricted project API key and billing controls privately. Put your own OPENAI_API_KEY in the ignored local .env file. Never paste or commit it. The helper explicitly requires this file and ignores inherited executor credentials.

The protocol requests gpt-6-luna, reasoning effort none, strict JSON, no tools, store=false, a 300-token output cap and a 20-second timeout. Only whitelisted market features, simulated balances and risk limits are sent. This is an API adviser, separate from the historical LSTM. The requested model name is frozen; the provider may update underlying weights. Receipts record the actual returned model and usage.

[Model documentation](https://developers.openai.com/api/docs/models/gpt-6-luna) and [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).

## Real model demonstration before deployment

Once the private key is saved, from Windows PowerShell:

~~~powershell
.\.venv\Scripts\python.exe -B tools/cloud_evaluation.py probe --secret-file .env --output var/llm-proof
.\.venv\Scripts\python.exe -B tools/cloud_evaluation.py prepare-secret
~~~

The probe uses freshly refreshed completed candles and a separate initial simulated portfolio. It records a validated real provider response privately, places no trades and counts as zero prospective days. Missing receipts, refusals or unavailable access are not model verification. Repeat attempts consume the persistent quota.

Only OPENAI_API_KEY is copied to ignored var/secrets/cloud-openai.env. Telegram/Gmail/Google credentials and personal destinations are excluded.

## Create one EC2 instance

Use [the EC2 console](https://console.aws.amazon.com/ec2/) and [AWS's launch guide](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/EC2_GetStarted.html).

- Select one t3.small x86_64 instance (2 GB RAM), only if marked eligible under your current Free plan.
- Choose official Canonical Ubuntu Server 24.04 LTS x86_64 without paid Marketplace software.
- Use a 30 GiB encrypted gp3 root disk. Leave extra disks, load balancers, NAT gateways and Elastic IP allocations off.
- Use on-demand capacity; interruptible Spot capacity is unsuitable for this week.
- Download an SSH key pair privately. Ubuntu's login user is ubuntu.
- Permit inbound SSH port 22 only from your current public IP. The collector needs outbound HTTPS/DNS and no public application ports.
- Check the final estimate and credit balance. Instance, disk and public IPv4 usage consume credits; the Free plan prevents charges without a paid upgrade.
- Share only the public server address, SSH username and local private-key path with the working agent. Never paste private keys or credentials.

Verify public Coinbase product/ticker connectivity before declaring the account. If a provider blocks a region, preserve any started evidence and declare a separate deployment; never relabel a failed account.

## Prepare the new Ubuntu host

These commands install software and build the image without starting the week:

~~~bash
sudo install -d -o ubuntu -g ubuntu /opt/bitcoin
git clone https://github.com/Abdulrahmanos/qytbcf1pULIvwxUO.git /opt/bitcoin
cd /opt/bitcoin
git rev-parse HEAD
sudo bash deploy/linux/bootstrap-ubuntu.sh
sudo docker compose -f compose.evaluation.yaml build
~~~

Use a reviewed published commit. Preserve that checkout and image throughout collection: do not pull, rebuild, change the model/configuration or upgrade calculation libraries. The bootstrap is restricted to a fresh Ubuntu 24.04 host and follows [Docker's official Ubuntu installation](https://docs.docker.com/engine/install/ubuntu/).

Copy the scoped secret from Windows with your actual key path and server address:

~~~powershell
scp -i "<private-key-path>" var/secrets/cloud-openai.env ubuntu@<server-address>:/opt/bitcoin/cloud-openai.env
~~~

Then on the server:

~~~bash
cd /opt/bitcoin
sudo install -d -m 0700 var/secrets
sudo install -o 10001 -g 10001 -m 0400 cloud-openai.env var/secrets/cloud-openai.env
unlink cloud-openai.env
sudo docker compose -f compose.evaluation.yaml config --quiet
sudo docker run --rm bitcoin-evaluation:local python -c "from bitcoin_agent.paper_service import public_json, PRODUCT_URL; print(public_json(PRODUCT_URL)['id'])"
~~~

File-backed Compose secrets retain host ownership. UID 10001 and mode 0400 let the nonroot adviser read its key; the paper container receives no secret. The key is excluded from Git and the image build context.

## Start the new week

~~~bash
cd /opt/bitcoin
sudo install -m 0644 deploy/linux/bitcoin-evaluation.service /etc/systemd/system/bitcoin-evaluation.service
sudo systemctl daemon-reload
sudo systemctl enable --now bitcoin-evaluation
sudo docker compose -f compose.evaluation.yaml ps
sudo docker compose -f compose.evaluation.yaml logs --tail 30 paper adviser
~~~

The boot service restores existing containers after reboot without rebuilding, resetting volumes or removing STOP/COMPLETE. Recovery is bounded; actual outages still count as gaps.

The paper service freezes source/config/protocol/environment before creating the new account in the bitcoin-evaluation_cloud_paper volume. The clock starts at the **first actual accepted quote**, not server creation, launch or historical warm-up.

The adviser mounts that paper volume **read-only** and writes its own bitcoin-evaluation_cloud_adviser volume. It skips stale data and stops on STOP/COMPLETE. Attempts are reserved before network I/O: at most four per UTC day, six hours apart, and 28 total. Failures consume quota; restarts do not restore it. API failures cannot alter risk controls, orders or balances.

This frozen week has no live Telegram/Gmail delivery or remote Sheet edits. Those integrations remain separate. Swing entries remain disabled under the existing operational policy. This run verifies real LLM use and reliability, not LLM-controlled trading profitability or a new DCA-versus-swing optimum.

## Inspect progress without chat automation

~~~bash
cd /opt/bitcoin
sudo docker compose -f compose.evaluation.yaml exec paper python tools/cloud_evaluation.py health
sudo docker compose -f compose.evaluation.yaml exec paper python -m bitcoin_agent --db var/cloud-week/ledger.sqlite3 forward-report --output var/cloud-week/reports
sudo docker compose -f compose.evaluation.yaml exec adviser cat var/cloud-adviser/status.json
df -h /var/lib/docker
~~~

The supervisor writes reports each minute and audited SQLite backups hourly. Each of seven anchored 24-hour days must meet the existing 99% quote-age coverage protocol. Missing observations remain missing. Keep sufficient disk space: the full week and hourly backups may occupy tens of gigabytes. A healthy collector cannot repair a failed day. No hourly chat messages are scheduled.

For an independently audited interim review:

~~~bash
sudo docker compose -f compose.evaluation.yaml run --rm --no-deps adviser python tools/cloud_evaluation.py export --runtime /app/evidence/cloud-week
~~~

## At the actual seven-day boundary

The supervisor stops collection and writes COMPLETE after seven elapsed days. COMPLETE alone is **not a pass**. The adviser stops API calls. Finalize independently:

~~~bash
sudo docker compose -f compose.evaluation.yaml run --rm --no-deps adviser python tools/cloud_evaluation.py export --runtime /app/evidence/cloud-week --require-complete
~~~

The exporter checks every daily threshold, candidate and three baseline ledgers, paired quotes, source/config/environment freeze, observer integrity, stopped collectors, backups, matching API receipts and attempt budget. Failure writes an honest interim report and returns a failing status. Never extend or re-anchor an experiment to manufacture a pass.

Copy reports and both preserved volumes privately before terminating the server or losing free-plan access. After services exit, their stopped containers remain available for docker cp. Report files are under /app/var/cloud-adviser/reports in the adviser container. Do not publish raw secrets or private identifiers.

Stop the service after exporting:

~~~bash
sudo systemctl stop bitcoin-evaluation
~~~

Verify offline copies and backup hashes before deleting server storage. This project never auto-upgrades an account or purchases paid services. Sanitized report publication is separate and requires actual evidence.
