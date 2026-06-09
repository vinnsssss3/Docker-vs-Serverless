# Methodology notes

This document expands on Chapter 3 of the paper. It explains the
design choices in the benchmark and why they support the validity
claims in §3.5.

## 1. Why a local controlled benchmark

A direct deploy-and-measure on AWS Lambda and an arbitrary Docker host
introduces several confounding variables that the paper's research
questions do not target:

- inter-region network latency,
- API Gateway buffering and TLS handshake costs,
- AWS Lambda's opaque, time-varying internal scheduling,
- VM "noisy neighbour" effects in the cloud provider's fleet,
- variable Docker host hardware across re-runs.

The research questions in §1.2 are about the architectural property
itself — always-on vs cold-start — not about a specific cloud
provider's implementation. The local benchmark controls for everything
except that property, which is the standard recipe for an internal
validity argument in experimental computer-systems research.

Researchers who additionally need network/cloud effects in scope can
re-run the same experiment via `benchmark/load_test.py` against the
deployed `docker/` and `serverless/` artifacts; the analysis script
consumes either dataset.

## 2. How each architecture is modelled

| Architecture | Implementation in local benchmark | What it captures from the real system |
|---|---|---|
| Docker (always-on) | One Python subprocess kept alive for the full scenario, requests over stdin/stdout. | The fact that under always-on operation, no request pays a startup cost; CPU/RAM are reserved continuously. |
| Serverless (cold/warm) | A fresh `python` interpreter is spawned on every cold invocation; module import is included in the measured latency. A warm subprocess is cached for an 8 s window. | Interpreter startup + import is exactly the work AWS Lambda performs on a cold start in addition to the workload itself. |

The local cold-start cost is smaller in absolute terms than what AWS
Lambda exhibits (~20 ms here vs 200–500 ms in production), because
the benchmark omits container provisioning and code-package download.
What it preserves is the *direction* and *qualitative shape* of the
difference — and the relative gap shrinks under heavier workloads,
which is what the paper analyses.

## 3. Variables controlled

- **Hardware:** identical (same machine for both architectures).
- **OS / Python build:** identical.
- **Workload code:** byte-identical (`core/workload.py`).
- **Memory budget:** 512 MB allocation (Docker `mem_limit`, Lambda
  `memorySize`).
- **Request payload:** same intensity-keyed payload across architectures.
- **Random seed:** fixed (`--seed 42` by default).

## 4. Variables manipulated

- **Architecture** — `docker` vs `serverless`.
- **Workload intensity** — `light` / `medium` / `heavy`.
- **Cold-start cadence** — controlled via `cold_inject_every_n` and the
  serverless warm window.

## 5. Variables observed

- Wallclock latency per request (ms).
- Per-stage timings reported by the workload (preprocess / core /
  postprocess).
- Boolean cold-start flag (from the FaaS worker itself).
- CPU% and RSS (MB) of the worker process, sampled at 4 Hz.

## 6. Threats to validity

- **External validity** — absolute numbers will differ on production
  cloud infrastructure. The repository includes the deployment
  artifacts and HTTP load-tester so the experiment can be re-run end
  to end on real AWS / Docker.
- **Construct validity** — the local serverless model includes only the
  runtime portion of cold start (interpreter + import). Container
  provisioning and code-package fetch, which dominate on real Lambda,
  are not modelled. The qualitative conclusions still hold; the
  magnitudes scale up on real infrastructure, where the gap is even
  larger than what we report.
- **Conclusion validity** — every scenario reports n ≥ 40 except the
  intentionally small `cold_warm_probe` (n = 15) used for the
  cold/warm story. Aggregate latency claims rely on the n ≥ 40
  scenarios.

## 7. Reproducibility checklist

- [x] Source code under version control
- [x] Pinned dependency versions
- [x] Fixed random seed
- [x] Raw data published alongside aggregated tables
- [x] Plot generation is deterministic from the raw CSVs
- [x] Both deployment artifacts (`docker/`, `serverless/`) included
- [x] Step-by-step reproduction guide (`docs/reproduction_guide.md`)
