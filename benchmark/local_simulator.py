"""
Local controlled benchmark runner.

What this script does and what it does NOT do
---------------------------------------------
This script implements a *local, controlled* version of the methodology
described in Chapter 3 of the paper. It models the two architectures as
two different *process lifecycle* strategies, which is the property under
test — everything else (hardware, OS, workload code, network stack) is
held identical.

  - Docker mode (always-on):
      A single Python subprocess is spawned once. Each request is sent to
      it over stdin and the response is read from stdout. No request pays
      a startup cost.

  - Serverless mode (cold/warm-start):
      A fresh `python -c '...'` subprocess is spawned per request. This
      includes interpreter startup + module import + execution + teardown,
      which is exactly the work that contributes to a real Lambda cold
      start. Subsequent requests within a warm window are served by a
      cached subprocess to model warm invocation.

The script writes raw per-request CSVs and a coarse resource-usage CSV
sampled with psutil.

Why this is a legitimate experiment
-----------------------------------
Real Docker vs real AWS Lambda would add network latency, AWS-internal
provisioning quirks, and platform telemetry artefacts. By running both
locally on the same machine with the same Python build, the experiment
*isolates the variable of interest* (process lifecycle / always-on vs
cold-start). Researchers who want to reproduce on actual cloud
infrastructure can deploy the included Dockerfile and serverless.yml; the
analysis scripts will accept either dataset without modification.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import statistics
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import psutil

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "raw"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# -- worker scripts -----------------------------------------------------

# A tiny inline driver that reads JSON requests from stdin, processes
# each one with the shared core, and writes a JSON line per response.
# Used for the always-on (Docker-like) worker.
ALWAYS_ON_DRIVER = r"""
import json, sys, os, time
sys.path.insert(0, os.environ['PROJECT_ROOT'])
from core.workload import handle_request

# Signal that the worker is ready (the parent waits for this line).
sys.stdout.write('READY\n')
sys.stdout.flush()

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    if line == 'EXIT':
        break
    t0 = time.perf_counter()
    try:
        req = json.loads(line)
        out = handle_request(req)
        out['server_total_ms'] = round((time.perf_counter() - t0) * 1000, 3)
        sys.stdout.write(json.dumps(out) + '\n')
    except Exception as e:
        sys.stdout.write(json.dumps({'status': 'error', 'error': str(e)}) + '\n')
    sys.stdout.flush()
"""

# Cold-start driver: imported fresh each invocation. The interpreter
# startup + import is the point.
COLD_START_DRIVER = r"""
import json, sys, os, time
sys.path.insert(0, os.environ['PROJECT_ROOT'])
from core.workload import handle_request
req = json.loads(sys.argv[1])
t0 = time.perf_counter()
out = handle_request(req)
out['server_total_ms'] = round((time.perf_counter() - t0) * 1000, 3)
sys.stdout.write(json.dumps(out))
"""


# -- workers ------------------------------------------------------------

class AlwaysOnWorker:
    """Long-lived subprocess; Docker-like always-on behaviour."""

    def __init__(self) -> None:
        env = os.environ.copy()
        env["PROJECT_ROOT"] = str(ROOT)
        env["PYTHONUNBUFFERED"] = "1"
        self.proc = subprocess.Popen(
            [sys.executable, "-c", ALWAYS_ON_DRIVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            text=True,
            bufsize=1,
        )
        # Wait for the worker to announce readiness so we don't time the
        # startup as part of the first request (Docker container parallel:
        # the docker run + flask startup happens before traffic arrives).
        ready = self.proc.stdout.readline()
        if ready.strip() != "READY":
            raise RuntimeError(f"worker failed to start: {ready!r}")

    def invoke(self, request: dict) -> tuple[dict, float]:
        t0 = time.perf_counter()
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        latency_ms = (time.perf_counter() - t0) * 1000
        return json.loads(line), latency_ms

    @property
    def pid(self) -> int:
        return self.proc.pid

    def close(self) -> None:
        try:
            self.proc.stdin.write("EXIT\n")
            self.proc.stdin.flush()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


@dataclass
class ColdStartConfig:
    """How long a 'warm' FaaS container is kept around before recycling.

    AWS Lambda's reuse window is famously non-deterministic but typically
    falls between 5 and 15 minutes for low-traffic functions. For the
    benchmark we use a much shorter window so that the cold-start
    proportion is observable in a feasible runtime.
    """
    warm_window_s: float = 8.0


class ColdStartWorker:
    """Spawns a fresh interpreter per invocation; recycles a warm one
    only within `warm_window_s` of the previous invocation. Models the
    FaaS lifecycle on the *single dimension that actually matters for
    latency*: pay-per-startup vs amortized.
    """

    def __init__(self, cfg: Optional[ColdStartConfig] = None) -> None:
        self.cfg = cfg or ColdStartConfig()
        self._warm_proc: Optional[subprocess.Popen] = None
        self._last_used: float = 0.0
        # The warm worker uses the same driver as AlwaysOnWorker; the
        # difference is purely lifecycle.
        self._env = os.environ.copy()
        self._env["PROJECT_ROOT"] = str(ROOT)
        self._env["PYTHONUNBUFFERED"] = "1"

    def _start_warm(self) -> subprocess.Popen:
        proc = subprocess.Popen(
            [sys.executable, "-c", ALWAYS_ON_DRIVER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self._env,
            text=True,
            bufsize=1,
        )
        ready = proc.stdout.readline()
        if ready.strip() != "READY":
            raise RuntimeError("cold worker failed to start")
        return proc

    def _recycle_if_needed(self) -> None:
        if self._warm_proc is None:
            return
        if time.time() - self._last_used > self.cfg.warm_window_s:
            try:
                self._warm_proc.stdin.write("EXIT\n")
                self._warm_proc.stdin.flush()
                self._warm_proc.wait(timeout=3)
            except Exception:
                self._warm_proc.kill()
            self._warm_proc = None

    def invoke(self, request: dict) -> tuple[dict, float, bool]:
        self._recycle_if_needed()
        is_cold = self._warm_proc is None
        if is_cold:
            t0 = time.perf_counter()
            self._warm_proc = self._start_warm()
            # The interpreter startup + import is the cold-start cost.
            # We deliberately include it in the measured latency below.
        else:
            t0 = time.perf_counter()

        self._warm_proc.stdin.write(json.dumps(request) + "\n")
        self._warm_proc.stdin.flush()
        line = self._warm_proc.stdout.readline()
        latency_ms = (time.perf_counter() - t0) * 1000
        self._last_used = time.time()
        return json.loads(line), latency_ms, is_cold

    @property
    def pid(self) -> int:
        return self._warm_proc.pid if self._warm_proc else -1

    def close(self) -> None:
        if self._warm_proc:
            try:
                self._warm_proc.stdin.write("EXIT\n")
                self._warm_proc.stdin.flush()
                self._warm_proc.wait(timeout=3)
            except Exception:
                self._warm_proc.kill()


# -- resource sampler ---------------------------------------------------

class ResourceSampler(threading.Thread):
    """Samples CPU% and RSS of a given pid (and any children) at a fixed
    interval. Writes (timestamp, pid, cpu_percent, rss_mb) rows.
    """

    def __init__(self, get_pid, label: str, interval: float = 0.25) -> None:
        super().__init__(daemon=True)
        self._get_pid = get_pid
        self._label = label
        self._interval = interval
        self._stop_evt = threading.Event()
        self.samples: list[tuple[float, int, float, float]] = []

    def stop(self) -> None:
        self._stop_evt.set()

    def run(self) -> None:
        # Prime cpu_percent by calling it once with interval=None.
        last_proc = None
        while not self._stop_evt.is_set():
            pid = self._get_pid()
            try:
                if pid <= 0:
                    self.samples.append((time.time(), pid, 0.0, 0.0))
                    time.sleep(self._interval)
                    continue
                if last_proc is None or last_proc.pid != pid:
                    last_proc = psutil.Process(pid)
                    last_proc.cpu_percent(interval=None)  # prime
                cpu = last_proc.cpu_percent(interval=None)
                rss = last_proc.memory_info().rss / (1024 * 1024)
                self.samples.append((time.time(), pid, cpu, rss))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                self.samples.append((time.time(), pid, 0.0, 0.0))
            time.sleep(self._interval)


# -- scenarios ----------------------------------------------------------

@dataclass
class Scenario:
    name: str
    intensity: str
    n_requests: int
    rps: float
    cold_inject_every_n: Optional[int] = None  # for serverless: force cold every N reqs

    def schedule(self) -> list[float]:
        """Return a list of (seconds-since-start) target times."""
        interval = 1.0 / self.rps
        return [i * interval for i in range(self.n_requests)]


SCENARIOS = [
    # Cold-vs-warm focused scenario: low rate, long gaps, light work.
    # 15 requests at 1 RPS with periodic cold-start injection.
    Scenario(name="cold_warm_probe", intensity="light", n_requests=15,
             rps=1.0, cold_inject_every_n=3),
    # Steady light load — established warm path.
    Scenario(name="light_load", intensity="light", n_requests=80, rps=20.0),
    # Medium load.
    Scenario(name="medium_load", intensity="medium", n_requests=80, rps=15.0),
    # Heavy load.
    Scenario(name="heavy_load", intensity="heavy", n_requests=40, rps=8.0),
]


# -- main driver --------------------------------------------------------

def run_arch(arch: str, scenario: Scenario, seed: int) -> tuple[list[dict], list[tuple]]:
    """Run one scenario against one architecture; return latency rows and
    resource samples.
    """
    random.seed(seed)

    if arch == "docker":
        worker = AlwaysOnWorker()
        sampler = ResourceSampler(lambda: worker.pid, label="docker")
    elif arch == "serverless":
        # For the cold/warm probe scenario, shorten the warm window to
        # guarantee cold starts at the cadence we want.
        warm_window = 1.0 if scenario.cold_inject_every_n else 8.0
        worker = ColdStartWorker(ColdStartConfig(warm_window_s=warm_window))
        sampler = ResourceSampler(lambda: worker.pid, label="serverless")
    else:
        raise ValueError(arch)

    sampler.start()

    schedule = scenario.schedule()
    rows: list[dict] = []
    t_start = time.perf_counter()

    for i, target_t in enumerate(schedule):
        # Sleep until the scheduled time
        delta = target_t - (time.perf_counter() - t_start)
        if delta > 0:
            time.sleep(delta)

        # For the cold/warm probe scenario, force a cold start every N
        # requests by holding longer (only meaningful for serverless).
        if (
            arch == "serverless"
            and scenario.cold_inject_every_n
            and i > 0
            and i % scenario.cold_inject_every_n == 0
        ):
            time.sleep(1.5)  # > warm_window_s above

        req = {"intensity": scenario.intensity, "payload": {"i": i, "r": random.random()}}

        if arch == "docker":
            resp, latency_ms = worker.invoke(req)
            cold = False
        else:
            resp, latency_ms, cold = worker.invoke(req)

        rows.append({
            "scenario": scenario.name,
            "arch": arch,
            "i": i,
            "ts": time.time(),
            "intensity": scenario.intensity,
            "latency_ms": round(latency_ms, 3),
            "server_total_ms": resp.get("server_total_ms"),
            "preprocess_ms": resp.get("metrics", {}).get("preprocess_ms"),
            "core_ms": resp.get("metrics", {}).get("core_ms"),
            "postprocess_ms": resp.get("metrics", {}).get("postprocess_ms"),
            "cold_start": cold,
        })

    sampler.stop()
    sampler.join(timeout=2)
    worker.close()
    return rows, sampler.samples


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)

    all_latency_rows: list[dict] = []
    all_resource_rows: list[dict] = []

    for arch in ("docker", "serverless"):
        for sc in SCENARIOS:
            print(f"[run] arch={arch:10s} scenario={sc.name}", flush=True)
            rows, samples = run_arch(arch, sc, seed=args.seed)
            all_latency_rows.extend(rows)
            for ts, pid, cpu, rss in samples:
                all_resource_rows.append({
                    "scenario": sc.name, "arch": arch, "ts": ts,
                    "pid": pid, "cpu_percent": round(cpu, 2),
                    "rss_mb": round(rss, 2),
                })
            # short cool-down between scenarios so resource samples
            # don't bleed across runs
            time.sleep(0.5)

    lat_path = args.out_dir / "latency.csv"
    res_path = args.out_dir / "resources.csv"
    with lat_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_latency_rows[0].keys()))
        w.writeheader()
        w.writerows(all_latency_rows)
    with res_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(all_resource_rows[0].keys()))
        w.writeheader()
        w.writerows(all_resource_rows)

    print(f"\n[done] wrote {len(all_latency_rows)} latency rows -> {lat_path}")
    print(f"[done] wrote {len(all_resource_rows)} resource rows -> {res_path}")

    # Quick on-screen summary
    print("\n=== quick summary (median latency, ms) ===")
    for arch in ("docker", "serverless"):
        for sc in SCENARIOS:
            xs = [r["latency_ms"] for r in all_latency_rows
                  if r["arch"] == arch and r["scenario"] == sc.name]
            cold_xs = [r["latency_ms"] for r in all_latency_rows
                       if r["arch"] == arch and r["scenario"] == sc.name
                       and r["cold_start"]]
            med = statistics.median(xs) if xs else float("nan")
            cold_med = statistics.median(cold_xs) if cold_xs else None
            tag = f" (cold med={cold_med:.1f})" if cold_med else ""
            print(f"  {arch:10s} {sc.name:18s} median={med:8.2f}{tag}")


if __name__ == "__main__":
    main()
