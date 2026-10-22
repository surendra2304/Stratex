"""Stratex journey hop: consume Futuris advisories, record paper-only decisions.

Uses Stratex's real durable cursor and real execution gates. Every decision is
paper-only (no order is placed, live or testnet) and is published to Memora's
mesh under the SAME correlation ID as the journey.

Usage:
    python scripts/run_journey_decision.py [--consumer-id stratex-journey]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from autonomy.memora_client import memora_client
from autonomy.mesh_decision import consume_and_decide


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--consumer-id",
        default="stratex-journey",
        help="Stratex consumer id owning the independent durable cursor",
    )
    args = parser.parse_args()

    result = consume_and_decide(memora_client, consumer_id=args.consumer_id)
    print(json.dumps(result, default=str))
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
