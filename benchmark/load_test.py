"""
HTTP load test against deployed endpoints.

Used to reproduce the experiment on REAL cloud infrastructure once the
Docker container and Lambda function have been deployed.

Usage:
    python load_test.py --url http://localhost:8080/invoke --arch docker
    python load_test.py --url https://abc.execute-api.ap-southeast-1.amazonaws.com/invoke --arch serverless

The CSV it writes has the same schema as local_simulator.py, so
analysis/analyze.py works on either dataset without modification.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import time
from pathlib import Path

import requests

SCENARIOS = [
    ("cold_warm_probe", "light", 30, 0.2, True),
    ("light_load",      "light", 120, 10.0, False),
    ("medium_load",     "medium", 120, 10.0, False),
    ("heavy_load",      "heavy", 60, 5.0, False),
]


def run(url: str, arch: str, out_path: Path) -> None:
    session = requests.Session()
    rows: list[dict] = []
    for name, intensity, n, rps, cold_probe in SCENARIOS:
        print(f"[run] {arch} :: {name}", flush=True)
        interval = 1.0 / rps
        t_start = time.perf_counter()
        for i in range(n):
            target = i * interval
            delta = target - (time.perf_counter() - t_start)
            if delta > 0:
                time.sleep(delta)
            # cold probe: insert long pause between groups for serverless
            if cold_probe and arch == "serverless" and i > 0 and i % 3 == 0:
                # AWS Lambda's warm window is typically several minutes;
                # on a real benchmark you'd extend this. Here we keep it
                # short for tractability.
                time.sleep(60)
            body = {"intensity": intensity, "payload": {"i": i}}
            t0 = time.perf_counter()
            try:
                r = session.post(url, json=body, timeout=30)
                resp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            except Exception as e:
                resp = {"error": str(e)}
            latency_ms = (time.perf_counter() - t0) * 1000
            rows.append({
                "scenario": name,
                "arch": arch,
                "i": i,
                "ts": time.time(),
                "intensity": intensity,
                "latency_ms": round(latency_ms, 3),
                "server_total_ms": resp.get("server_total_ms"),
                "preprocess_ms": (resp.get("metrics") or {}).get("preprocess_ms"),
                "core_ms": (resp.get("metrics") or {}).get("core_ms"),
                "postprocess_ms": (resp.get("metrics") or {}).get("postprocess_ms"),
                "cold_start": bool(resp.get("cold_start", False)),
            })
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"[done] {len(rows)} rows -> {out_path}")
    for name, _, _, _, _ in SCENARIOS:
        xs = [r["latency_ms"] for r in rows if r["scenario"] == name]
        print(f"  {arch} {name:18s} median={statistics.median(xs):.2f}ms")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--arch", choices=["docker", "serverless"], required=True)
    ap.add_argument("--out", type=Path, default=None)
    a = ap.parse_args()
    out = a.out or Path(__file__).resolve().parents[1] / "data" / "raw" / f"latency_{a.arch}_remote.csv"
    run(a.url, a.arch, out)
