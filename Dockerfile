FROM python:3.14-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app
COPY requirements-runtime.lock.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.lock.txt
COPY bitcoin_agent ./bitcoin_agent
COPY config ./config
COPY tools/check_service_health.py tools/cloud_evaluation.py ./tools/
COPY compose.evaluation.yaml ./compose.evaluation.yaml
RUN mkdir -p /app/var && chown 10001:10001 /app/var
USER 10001:10001
CMD ["python", "-m", "bitcoin_agent", "--config", "config/paper.json", "--db", "var/service/ledger.sqlite3", "run-paper", "--cycles", "0", "--runtime", "var/service/market", "--output", "var/service/last-run.json"]
