# Docker vs Serverless FaaS — Benchmark Repository

Companion code, data, and reproduction artifacts for the paper
**"Comparative Analysis of Latency and Resource Utilization in Docker
and Serverless FaaS Architectures."**

The goal of this repository is to make every claim in the *Experiment
Results* and *Conclusion* chapters independently verifiable. Anyone who
clones it should be able to:

1. re-run the local controlled benchmark (no cloud account required), or
2. deploy the same source code to real Docker + AWS Lambda and re-run
   the experiment against real infrastructure, and
3. reproduce every plot in the paper from the resulting raw CSVs.

---

## Repository layout

    .
    ├── core/                # Shared business logic (identical on both architectures)
    │   └── workload.py
    ├── docker/              # Always-on target
    │   ├── Dockerfile
    │   ├── app.py           # Flask HTTP server wrapping core.workload
    │   ├── docker-compose.yml
    │   └── requirements.txt
    ├── serverless/          # FaaS target
    │   ├── handler.py       # AWS Lambda handler wrapping core.workload
    │   ├── serverless.yml   # Serverless Framework config
    │   └── requirements.txt
    ├── benchmark/
    │   ├── local_simulator.py  # In-process controlled experiment
    │   └── load_test.py        # HTTP load test against deployed endpoints
    ├── analysis/
    │   ├── analyze.py       # Statistics + plot generation
    │   ├── plots/           # Generated figures
    │   └── results_summary.md
    ├── data/
    │   ├── raw/             # Raw per-request CSVs (latency.csv, resources.csv)
    │   └── processed/       # Aggregated CSVs (latency_summary.csv, resource_summary.csv)
    └── docs/
        ├── methodology.md
        └── reproduction_guide.md

The same workload code (`core/workload.py`) is loaded by both wrappers,
so any latency difference observed between the two is attributable to
the architecture, not to the business logic — see Methodology §3.4.

---

## Quick start: reproduce the experiment locally

Requires Python 3.10+. No Docker daemon or AWS account needed.

```bash
pip install -r requirements.txt
python benchmark/local_simulator.py
python analysis/analyze.py
```

Outputs:

- `data/raw/latency.csv` — per-request observed latency
- `data/raw/resources.csv` — CPU% / RSS samples
- `data/processed/latency_summary.csv` and `resource_summary.csv`
- `analysis/plots/*.png` — figures reproduced in the paper
- `analysis/results_summary.md` — markdown summary tables

A full run takes ~3–4 minutes on a laptop and produces ~430 latency
observations across four scenarios for each architecture.

---

## Reproducing against real cloud infrastructure

The local benchmark isolates the architectural variable of interest
(process lifecycle: always-on vs cold-start) on identical hardware. For
researchers who additionally want network and cloud-platform effects
included, both deployment targets are provided.

### Docker target

```bash
cd docker
docker compose up --build -d
# Service is at http://localhost:8080/invoke
python ../benchmark/load_test.py --url http://localhost:8080/invoke --arch docker
```

### Serverless target (AWS Lambda)

```bash
cd serverless
npm install -g serverless
serverless deploy
# Note the endpoint URL printed by `serverless deploy`
python ../benchmark/load_test.py \
    --url https://<your-id>.execute-api.ap-southeast-1.amazonaws.com/invoke \
    --arch serverless
```

Both runs append rows to `data/raw/latency_<arch>_remote.csv` using
exactly the schema that `analysis/analyze.py` consumes.

---

## What the experiment measures

Four scenarios, run independently against each architecture:

| Scenario          | Intensity | Request count | Target rate |
| ----------------- | --------- | ------------- | ----------- |
| cold_warm_probe   | light     | 15            | 1 RPS, with periodic idle injection |
| light_load        | light     | 80            | 20 RPS |
| medium_load       | medium    | 80            | 15 RPS |
| heavy_load        | heavy     | 40            | 8 RPS |

Two metric families are collected:

- **Latency** — wallclock duration from request submission to response,
  with per-stage sub-timings (preprocess / core / postprocess) recorded
  by the workload itself.
- **Resource utilisation** — per-process CPU % and RSS (resident set
  size), sampled at 4 Hz with `psutil`.

The `cold_warm_probe` scenario forces serverless cold starts at a known
cadence so cold vs warm latency can be cleanly separated.

---

## How the local benchmark models each architecture

The two architectures are modelled by their *process lifecycle*, which
is the single property that distinguishes them at the latency layer:

- **Docker (always-on):** one long-lived Python subprocess is spawned
  before the first request, handles every request over stdin/stdout, and
  is shut down only when the scenario ends. No request pays a startup
  cost.
- **Serverless (cold/warm-start):** a fresh `python` interpreter is
  spawned on cold invocations. Interpreter startup + module import is
  included in the measured latency — this is what AWS Lambda's cold-start
  cost actually comprises at the runtime level. A warm subprocess is
  cached for an 8-second window (configurable) to serve subsequent
  requests at warm-start cost.

Both workers execute byte-identical code from `core/workload.py`, so
differences in observed latency or resource usage are attributable to
the lifecycle model, not to the workload.

For a full discussion of validity, see `docs/methodology.md`.

---

## License

Code is released under the MIT License (see `LICENSE`). The raw
experimental data is released under CC BY 4.0.

## Citation

If you use this repository in your own work, please cite the paper as
listed in the *Open Data* section of the manuscript.
