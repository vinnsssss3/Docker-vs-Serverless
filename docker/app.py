"""
Docker side: a long-running Flask HTTP server.

Architectural property under test:
    Always-on. The process is started once and kept alive. Subsequent
    requests pay no initialization cost; CPU and RAM are reserved
    regardless of whether the system is currently serving a request.
"""

import os
import sys
import time

from flask import Flask, jsonify, request

# Make the shared core importable when running from the project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.workload import handle_request  # noqa: E402

app = Flask(__name__)
_started_at = time.time()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "uptime_s": round(time.time() - _started_at, 2)})


@app.route("/invoke", methods=["POST"])
def invoke():
    t0 = time.perf_counter()
    body = request.get_json(force=True, silent=True) or {}
    result = handle_request(body)
    t1 = time.perf_counter()
    # Wall-clock latency observed inside the container, separate from
    # the more granular per-stage metrics that handle_request records.
    result["server_total_ms"] = round((t1 - t0) * 1000, 3)
    return jsonify(result)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    # threaded=True keeps a single process model; matches a real
    # production deployment of a Python service inside one container.
    app.run(host="0.0.0.0", port=port, threaded=True)
