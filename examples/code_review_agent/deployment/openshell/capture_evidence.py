# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Observe this pinned demo's processes and read calls before temporary state is removed.

This is test instrumentation, not a Fabric API or a portable OpenClaw contract.
OpenClaw exports process identities, session IDs, turn counts, and read paths.
For Hermes this observer captures only process identity; tool evidence comes from
the adapter result, without reading its session database.
"""

import argparse
import json
import sqlite3
import time
from pathlib import Path


def snapshot(harness="openclaw"):
    processes = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            args = (entry / "cmdline").read_bytes().split(b"\0")
        except OSError:
            continue
        if f"nemo_fabric_adapters.{harness}.adapter".encode() in args:
            processes[entry.name] = "adapter"
        elif args and (
            args[0].startswith(b"openclaw-gateway")
            or (b"gateway" in args and b"run" in args)
        ):
            processes[entry.name] = "openclaw_gateway"
    return processes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=["openclaw", "hermes"], default="openclaw")
    args = parser.parse_args()
    started = time.monotonic()
    identities = {}
    turns = {}
    reads = set()
    gateways_by_turn = {}
    saw_gateway = False
    last_live = started
    while time.monotonic() - started < 650:
        live = snapshot(args.harness)
        now = time.monotonic() - started
        for pid, role in live.items():
            identities.setdefault(
                pid, {"role": role, "first_seen_seconds": round(now, 2)}
            )
            identities[pid]["last_seen_seconds"] = round(now, 2)
        saw_gateway |= (
            "openclaw_gateway" if args.harness == "openclaw" else "adapter"
        ) in live.values()
        if live:
            last_live = time.monotonic()
        for path in (
            Path("/sandbox/tmp").glob("nemo-fabric-openclaw-*/state/**/*.sqlite*")
            if args.harness == "openclaw"
            else []
        ):
            if path.name.endswith(("-shm", "-wal")):
                continue
            try:
                with sqlite3.connect(
                    f"file:{path}?mode=ro", uri=True, timeout=0.1
                ) as database:
                    events = database.execute(
                        "SELECT session_id, event_json FROM transcript_events"
                    ).fetchall()
            except sqlite3.Error:
                continue
            counts = {}
            for session, raw in events:
                message = json.loads(raw).get("message", {})
                if message.get("role") == "user":
                    counts[session] = counts.get(session, 0) + 1
                content = message.get("content", [])
                if not isinstance(content, list):
                    continue
                for item in content:
                    if item.get("type") == "toolCall" and item.get("name") == "read":
                        arguments = item.get("arguments", {})
                        name = arguments.get("path", arguments.get("file_path"))
                        if isinstance(name, str):
                            reads.add((session, name))
            for session, count in counts.items():
                turns[session] = max(turns.get(session, 0), count)
                if count in {1, 2}:
                    gateways_by_turn.setdefault(session, {}).setdefault(
                        str(count), set()
                    ).update(
                        pid for pid, role in live.items() if role == "openclaw_gateway"
                    )
        if saw_gateway and time.monotonic() - last_live > 1:
            break
        if not saw_gateway and time.monotonic() - started > 60:
            break
        time.sleep(0.2)
    print(
        json.dumps(
            {
                "processes": identities,
                "user_turns_by_session": turns,
                "gateway_pids_by_user_turn": {
                    session: {count: sorted(pids) for count, pids in counts.items()}
                    for session, counts in gateways_by_turn.items()
                },
                "read_calls": [
                    {"session_id": session, "path": path}
                    for session, path in sorted(reads)
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
