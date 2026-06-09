"""
Core workload module.

The identical computational logic that both the Docker container and the
Serverless FaaS function execute. Deploying the same source code on both
architectures eliminates logic-based performance variance (Methodology, 3.4).

The workload is intentionally moderate-CPU + small-memory so that it
exercises the architectural difference (always-on vs cold-start) rather
than being dominated by the work itself.
"""

import hashlib
import json
import math
import time


def _light_compute(payload: dict) -> dict:
    """Light work: hash the payload and run a small prime check loop."""
    raw = json.dumps(payload, sort_keys=True).encode()
    digest = hashlib.sha256(raw).hexdigest()

    n = 5_000
    primes = 0
    for x in range(2, n):
        is_prime = True
        for d in range(2, int(math.isqrt(x)) + 1):
            if x % d == 0:
                is_prime = False
                break
        if is_prime:
            primes += 1

    return {"digest": digest, "primes_below_n": primes, "n": n}


def _medium_compute(payload: dict) -> dict:
    """Medium work: 50k SHA-256 rounds + Fibonacci."""
    raw = json.dumps(payload, sort_keys=True).encode()
    h = raw
    for _ in range(50_000):
        h = hashlib.sha256(h).digest()

    a, b = 0, 1
    for _ in range(30_000):
        a, b = b, a + b

    return {"digest": h.hex(), "fib_bits": b.bit_length()}


def _heavy_compute(payload: dict) -> dict:
    """Heavy work: 200k SHA-256 rounds + larger prime sieve."""
    raw = json.dumps(payload, sort_keys=True).encode()
    h = raw
    for _ in range(200_000):
        h = hashlib.sha256(h).digest()

    n = 20_000
    sieve = [True] * (n + 1)
    sieve[0] = sieve[1] = False
    for i in range(2, int(math.isqrt(n)) + 1):
        if sieve[i]:
            for j in range(i * i, n + 1, i):
                sieve[j] = False
    primes = sum(sieve)

    return {"digest": h.hex(), "primes_below_n": primes, "n": n}


_WORKLOADS = {
    "light": _light_compute,
    "medium": _medium_compute,
    "heavy": _heavy_compute,
}


def handle_request(event: dict) -> dict:
    """Pipeline matching Methodology 3.2:

      input -> preprocessing -> core logic -> post-processing -> output

    `event` should look like:
        {"intensity": "light" | "medium" | "heavy", "payload": {...}}
    """
    t0 = time.perf_counter()

    # Preprocessing: validate and parse
    intensity = (event or {}).get("intensity", "light")
    if intensity not in _WORKLOADS:
        raise ValueError(f"unknown intensity: {intensity}")
    payload = (event or {}).get("payload", {})

    t1 = time.perf_counter()

    # Core logic
    result = _WORKLOADS[intensity](payload)

    t2 = time.perf_counter()

    # Post-processing: format response and record per-stage metrics
    response = {
        "status": "ok",
        "intensity": intensity,
        "result": result,
        "metrics": {
            "preprocess_ms": round((t1 - t0) * 1000, 3),
            "core_ms": round((t2 - t1) * 1000, 3),
            "postprocess_ms": None,  # filled below
            "total_ms": None,
        },
    }

    t3 = time.perf_counter()
    response["metrics"]["postprocess_ms"] = round((t3 - t2) * 1000, 3)
    response["metrics"]["total_ms"] = round((t3 - t0) * 1000, 3)
    return response


if __name__ == "__main__":
    import sys
    intensity = sys.argv[1] if len(sys.argv) > 1 else "light"
    out = handle_request({"intensity": intensity, "payload": {"x": 1}})
    print(json.dumps(out, indent=2))
