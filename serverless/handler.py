"""
Serverless side: AWS Lambda handler.

Architectural property under test:
    Event-driven / cold-start. The function only runs when invoked. If the
    underlying execution environment has been recycled, the first call pays
    a provisioning + import cost (cold start). Subsequent calls within the
    keep-alive window hit a warm container and pay only the request cost.
"""

import json
import os
import sys
import time

# Lambda's working directory is /var/task. Make the core package importable
# from there.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.workload import handle_request  # noqa: E402

# Marker that lets us detect cold vs warm within a single execution
# environment. On a cold start this module is re-imported and the
# variable is freshly False; on a warm invocation it stays True.
_WARM = False


def lambda_handler(event, context):
    global _WARM
    was_cold = not _WARM
    _WARM = True

    t0 = time.perf_counter()

    # API Gateway proxies wrap the body in a string; raw invocations don't.
    if isinstance(event, dict) and "body" in event and isinstance(event["body"], str):
        try:
            body = json.loads(event["body"])
        except json.JSONDecodeError:
            body = {}
    else:
        body = event or {}

    result = handle_request(body)
    t1 = time.perf_counter()
    result["server_total_ms"] = round((t1 - t0) * 1000, 3)
    result["cold_start"] = was_cold

    return {
        "statusCode": 200,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(result),
    }


if __name__ == "__main__":
    # Allow local invocation for sanity checking
    test = {"intensity": "light", "payload": {"x": 1}}
    print(lambda_handler(test, None))
