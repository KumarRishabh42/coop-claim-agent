"""CLI entry point: seeds the database and runs the pipeline over every
rendered packet, so the demo starts fully populated. See SPEC.md M1/M4 and
the "make data" target. Logic lives in agent/seed.py so tests can reuse it."""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from agent.db import reset_db
from agent.seed import run_all_packets, seed_bmw_program, seed_program_and_rules


def main() -> None:
    conn = reset_db()
    seed_program_and_rules(conn)
    print("Seeded program, rules, dealer, opening ledger.")
    decisions = run_all_packets(conn)
    for packet_id, decision in decisions.items():
        print(f"  packet {packet_id}: {decision.decision}")
    print("Ran pipeline for all packets.")
    seed_bmw_program(conn)
    print("Seeded BMW Motorrad program and packet G.")


if __name__ == "__main__":
    main()
