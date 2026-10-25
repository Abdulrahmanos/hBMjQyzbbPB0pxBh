# Free open model and prospective cloud week

Updated 25 October 2026. The owner chose a free open model instead of paid OpenAI API access. No API key is needed for this deployment. The previously prepared AWS image and market-feed check passed; the new model and timed account still require verification on that server.

## Model and purpose

The model is Qwen2.5-0.5B-Instruct in Q4_K_M GGUF format, downloaded from [Qwen's own repository](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF). Its exact repository revision, 491,400,032-byte size and SHA-256 are stored in config/open-model.json. The CPU-only [llama.cpp server image](https://github.com/ggml-org/llama.cpp/blob/master/docs/docker.md) is also pinned by digest.

The model reads completed-bar features and simulated balances, then returns one permitted shadow suggestion with a short explanation. The paper rules retain exclusive control of trades, allocations and risk limits. A real model response demonstrates model use; it does not establish improved returns. This remains separate from the historical LSTM study.

Only the model downloader can write the weights volume. Inference and adviser mount it read-only and verify its checksum. The model server and adviser use an internal Docker network with no external route or published ports. There is no paid API fallback and no cloud inference account. Public model downloads and Coinbase data access use the separate setup/paper network.

## Resource limits

The model service has a 768 MiB memory limit, one CPU thread, a 2,048-token context, one inference slot, 128-token processing batches and a 256-token output limit. Explanations are limited to 200 characters each through schema-constrained generation. Paper and adviser services have separate memory limits. The 2 GiB server must still pass its real preflight and be checked for OOM/restarts; small weight size alone does not prove reliable coexistence.

Runtime attempts are reserved before I/O, with four attempts per UTC day, six hours between attempts and 28 total. Failure consumes quota. Advice never changes the ledger. Source, architecture, model checksum, configuration, protocol and calculation-library versions are frozen for the new account.

## Hosting

The owner's AWS Free plan was verified privately. AWS credits are consumed by the VM, disk and public IPv4; do not upgrade to a Paid plan or add unrelated resources. The setup targets Ubuntu 24.04 x86_64, a t3.small, and a 30 GiB encrypted gp3 root disk.

SSH login remains restricted. The owner's EC2 Instance Connect browser session works after allowing AWS's regional managed prefix list. Do not expose the model port to the internet. All addresses, SSH keys and account identifiers stay out of Git.

For a new server, install Docker using deploy/linux/bootstrap-ubuntu.sh. For the already prepared server, keep the existing folder and update only to a reviewed, CI-verified commit before the new account starts.

## Prepare the model without starting the week

From /opt/bitcoin on the server, after checking out the verified new commit:

~~~bash
sudo docker compose -f compose.evaluation.yaml build paper model
sudo docker compose -f compose.evaluation.yaml run --rm --no-deps model-download
sudo docker compose -f compose.evaluation.yaml up -d --no-build --wait model
sudo docker compose -f compose.evaluation.yaml run --rm --no-deps paper python tools/cloud_evaluation.py warmup
sudo docker compose -f compose.evaluation.yaml run --rm --no-deps adviser python tools/cloud_evaluation.py probe --runtime /app/evidence/cloud-week --output var/cloud-adviser/probe
sudo docker stats --no-stream
~~~

The downloader verifies size and SHA-256 before activating the model. Every model-container start checks the checksum again; the adviser checks the read-only file independently. Warm-up writes only historical candles/features and refuses an existing declared account. The probe uses those freshly completed real-data features with a separate initial simulated portfolio.

A successful probe reports provider_response_verified=true and counts_as_week_evidence=false. It saves the advice, local inference response ID, model digest and token counts in the adviser volume. Missing metadata, a timeout, truncated output, invalid JSON or missing model receipts cannot pass the preflight. The CLI fails rather than claiming success.

CI also makes an actual CPU inference request using explicitly synthetic context. Its receipt verifies inference plumbing and resource behavior, not a prospective day or a trading result. Do not present the CI smoke context as observed market data.

## Declare and start the separate account

Do this only after the model preflight succeeds, memory is acceptable and no OOM/restart is reported:

~~~bash
sudo install -m 0644 deploy/linux/bitcoin-evaluation.service /etc/systemd/system/bitcoin-evaluation.service
sudo systemctl daemon-reload
sudo systemctl enable --now bitcoin-evaluation
sudo docker compose -f compose.evaluation.yaml ps
sudo docker compose -f compose.evaluation.yaml exec paper python tools/cloud_evaluation.py health
sudo docker compose -f compose.evaluation.yaml exec paper python -m bitcoin_agent --db var/cloud-week/ledger.sqlite3 forward-report --output var/cloud-week/reports
~~~

The new paper account uses the bitcoin-evaluation_cloud_paper volume. The adviser uses bitcoin-evaluation_cloud_adviser, and model weights use bitcoin-evaluation_model_weights. The old Windows accounts are never imported or reset.

The week starts at the first actual accepted quote. Server creation, container build, model tests and historical candles count as zero prospective time. The source/config/environment manifest is saved before the account is initialized. Every accepted quote must pair with its candidate and three baseline observations.

The boot service restores existing containers without rebuilding images or clearing STOP/COMPLETE. Collection ends automatically at the original seven-day boundary. Each of seven anchored 24-hour days must meet the existing 99% quote-age coverage criterion. Recovery never fills missing observations.

Swing entries remain disabled under the previously declared operational policy. No live Telegram/Gmail delivery or remote Sheet edits are enabled in this frozen experiment. Their setup and older verification remain separate.

## Inspect and preserve

~~~bash
sudo docker compose -f compose.evaluation.yaml exec paper python tools/cloud_evaluation.py health
sudo docker compose -f compose.evaluation.yaml exec adviser cat var/cloud-adviser/status.json
sudo docker compose -f compose.evaluation.yaml logs --tail 40 model adviser
sudo docker stats --no-stream
df -h /var/lib/docker
~~~

The supervisor writes reports each minute and audited backups hourly. Watch disk capacity: a full week with hourly snapshots can occupy tens of gigabytes. No Codex heartbeat or recurring chat messages are created. Closing the browser does not stop a running systemd-managed deployment.

Never pull code, rebuild the images, replace weights, change the model protocol or upgrade calculation libraries while the account is frozen. A failed window must retain its original identity, start and deadline.

For an interim review:

~~~bash
sudo docker compose -f compose.evaluation.yaml run --rm --no-deps adviser python tools/cloud_evaluation.py export --runtime /app/evidence/cloud-week
~~~

At the actual seven-day boundary:

~~~bash
sudo docker compose -f compose.evaluation.yaml run --rm --no-deps adviser python tools/cloud_evaluation.py export --runtime /app/evidence/cloud-week --require-complete
~~~

Finalization verifies daily coverage, four reconciled books, quote pairing, source/config/environment freeze, stopped collectors, backups, matching inference receipts, the model-file checksum and the attempt budget. COMPLETE is only a time-boundary marker; it is not a validation pass.

Copy reports and both evidence volumes privately and verify hashes before stopping or deleting server storage. Model weights can be downloaded again from their pinned source; ledgers and observations cannot. The optional OpenAI adapter remains available elsewhere in the repository, but this deployment never uses it or any inherited API credential.

Gmail OAuth and runtime Google Sheet OAuth remain separately unverified. YouTube is waived by the owner. GitHub source publication is authorized; Apziva submission remains the owner's action.
