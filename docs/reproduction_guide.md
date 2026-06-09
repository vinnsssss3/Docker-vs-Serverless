# Reproduction guide

This walks through reproducing every figure in the paper, from a fresh
clone of the repository.

## Prerequisites

- Python 3.10 or newer
- ~100 MB of free disk space
- ~5 minutes of compute time

Optional, for cloud reproduction:

- Docker 24+
- Node.js 18+ and the Serverless Framework (`npm install -g serverless`)
- An AWS account with permissions to deploy Lambda + API Gateway

## Step 1 — install Python dependencies

```bash
git clone <repo-url> docker-vs-faas-benchmark
cd docker-vs-faas-benchmark
python -m venv .venv
source .venv/bin/activate          # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
```

## Step 2 — verify the workload runs

```bash
python core/workload.py light
python core/workload.py medium
python core/workload.py heavy
```

Each invocation prints a JSON object with per-stage timings. Light
should be a few ms, medium a few tens of ms, heavy ~100 ms.

## Step 3 — run the experiment

```bash
python benchmark/local_simulator.py
```

This will print one line per (architecture, scenario) pair as it runs.
At the end it writes:

- `data/raw/latency.csv` (~430 rows)
- `data/raw/resources.csv` (~230 rows)

And prints a quick summary table.

## Step 4 — generate plots and statistics

```bash
python analysis/analyze.py
```

Outputs:

- `data/processed/latency_summary.csv`
- `data/processed/resource_summary.csv`
- `analysis/results_summary.md`
- `analysis/plots/latency_by_scenario.png`
- `analysis/plots/cold_vs_warm.png`
- `analysis/plots/latency_timeseries_probe.png`
- `analysis/plots/cpu_usage.png`
- `analysis/plots/memory_usage.png`
- `analysis/plots/always_on_overhead.png`

## Step 5 (optional) — reproduce against real cloud infrastructure

### Docker

```bash
cd docker
docker compose up --build -d
curl -X POST http://localhost:8080/invoke \
    -H 'Content-Type: application/json' \
    -d '{"intensity":"light","payload":{"hello":1}}'
python ../benchmark/load_test.py --url http://localhost:8080/invoke --arch docker
docker compose down
```

### AWS Lambda

```bash
cd serverless
serverless deploy
# Note the printed endpoint URL.
python ../benchmark/load_test.py \
    --url https://<your-id>.execute-api.ap-southeast-1.amazonaws.com/invoke \
    --arch serverless
serverless remove
```

Both write to `data/raw/latency_<arch>_remote.csv`. To compare against
the local dataset, copy them in beside the local CSVs and adjust the
file paths in `analyze.py`, or import them into a notebook of your own.

## Troubleshooting

- **`psutil.NoSuchProcess`** during a run is benign — it occurs when the
  serverless worker is recycled between resource samples; the sampler
  records a zero row and continues.
- **`port 8080 in use`** — change the `ports` mapping in
  `docker/docker-compose.yml` and the corresponding `--url` argument.
- **`serverless deploy` permission errors** — confirm your AWS
  credentials have IAM, Lambda, API Gateway, and CloudWatch permissions.
