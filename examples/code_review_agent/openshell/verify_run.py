# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Validate the live two-turn demo using each harness's available evidence."""

import argparse
import gzip
import json
from pathlib import Path


def _relay_correlated(event, result):
    metadata = event.get("metadata") or {}
    prefix = result["runtime_id"] + ":" + result["request_id"] + ":"
    return any(
        str(metadata.get(key, "")).startswith(prefix)
        for key in ("api_request_id", "turn_id")
    )


def verify_relay(results):
    """Validate actual task-emitted model/tool events in collected snapshots."""
    invocations = []
    all_reads = []
    for result in results:
        records, trajectories = [], []
        for artifact in result["artifacts"]["artifacts"]:
            if artifact["kind"] not in {"atif", "atof"}:
                continue
            path = Path(artifact["path"])
            if (
                result["runtime_id"] not in path.parts
                or result["invocation_id"] not in path.parts
            ):
                raise ValueError(
                    "Relay snapshots must be scoped to the runtime and invocation"
                )
            with gzip.open(path, "rt") as stream:
                content = stream.read(16 * 1024 * 1024 + 1)
            if len(content) > 16 * 1024 * 1024:
                raise ValueError("Relay evidence exceeds the verification limit")
            if artifact["kind"] == "atof":
                records.extend(json.loads(line) for line in content.splitlines())
            else:
                trajectories.append(json.loads(content))
        correlated = [event for event in records if _relay_correlated(event, result)]
        completed = {
            event["uuid"]: event
            for event in correlated
            if event.get("scope_category") == "end"
        }
        model_calls, reads = [], []
        for event in correlated:
            if event.get("scope_category") != "start" or event["uuid"] not in completed:
                continue
            end = completed[event["uuid"]]
            if end.get("metadata", {}).get("otel.status_code") != "OK":
                continue
            if event["name"] == "openai.chat_completions":
                model = event["data"]["content"]["model"]
                if model == result["output"]["model"]:
                    model_calls.append(event["uuid"])
            elif event["name"] == "read_file":
                reply = (
                    json.loads(end["data"])
                    if isinstance(end["data"], str)
                    else end["data"]
                )
                if (
                    isinstance(reply, dict)
                    and isinstance(reply.get("content"), str)
                    and not reply.get("error")
                    and not reply.get("not_found")
                    and reply.get("success") is not False
                ):
                    reads.append(event["data"]["path"])
        if not model_calls:
            raise ValueError(
                "Relay must contain a successful model call correlated to each request"
            )
        if not any(
            trajectory.get("steps")
            and any(
                _relay_correlated(event, result)
                for event in trajectory.get("extra", {}).get("observed_events", [])
            )
            for trajectory in trajectories
        ):
            raise ValueError(
                "Relay must contain an ATIF trajectory correlated to each request"
            )
        all_reads.extend(reads)
        invocations.append(
            {
                "request_id": result["request_id"],
                "invocation_id": result["invocation_id"],
                "model_calls": len(model_calls),
                "file_reads": reads,
                "atof_records": len(records),
                "atif_trajectories": len(trajectories),
            }
        )
    if not any(path.endswith("calculator.py") for path in all_reads):
        raise ValueError("Relay must show a successful calculator.py read")
    return {"invocations": invocations, "file_reads": all_reads}


def hermes_read_calls(messages):
    calls = {}
    completed = set()
    for message in messages:
        for call in message.get("tool_calls", []):
            function = call["function"]
            if function["name"] == "read_file":
                arguments = function["arguments"]
                if isinstance(arguments, str):
                    arguments = json.loads(arguments)
                calls[call["id"]] = arguments["path"]
        if message.get("role") == "tool":
            content = message.get("content", "")
            try:
                result = json.loads(content) if isinstance(content, str) else content
            except json.JSONDecodeError:
                result = None
            if (
                isinstance(result, dict)
                and isinstance(result.get("content"), str)
                and not result.get("error")
                and not result.get("not_found")
                and result.get("success") is not False
            ):
                completed.add(message["tool_call_id"])
    return [path for identifier, path in calls.items() if identifier in completed]


def verify_results(results, evidence, *, harness, label):
    if len(results) != 2 or any(r["status"] != "succeeded" for r in results):
        raise ValueError("both review turns must succeed")
    if results[0]["runtime_id"] != results[1]["runtime_id"]:
        raise ValueError("both turns must use one Fabric runtime")
    if "calculator.py" not in results[0]["output"]["response"]:
        raise ValueError("the review must reference calculator.py")
    if label not in results[1]["output"]["response"]:
        raise ValueError("conversation continuity failed")
    roles = [p["role"] for p in evidence["processes"].values()]
    if roles.count("adapter") != 1:
        raise ValueError("the adapter process must persist across both turns")
    if harness == "openclaw":
        if not any(count == 2 for count in evidence["user_turns_by_session"].values()):
            raise ValueError("one OpenClaw session must contain both turns")
        persistent = [
            set(turns.get("1", [])) & set(turns.get("2", []))
            for session, turns in evidence["gateway_pids_by_user_turn"].items()
            if evidence["user_turns_by_session"][session] == 2
        ]
        if not any(len(pids) == 1 for pids in persistent):
            raise ValueError(
                "one OpenClaw Gateway PID must be present during both turns"
            )
        paths = [read["path"] for read in evidence["read_calls"]]
    else:
        paths = hermes_read_calls(results[0]["output"]["messages"])
        if results[0]["output"]["hermes_home"] != results[1]["output"]["hermes_home"]:
            raise ValueError("Hermes state must remain in the same task runtime")
    if not any(path.endswith("calculator.py") for path in paths):
        raise ValueError("the harness must read calculator.py")
    return {
        "harness": harness,
        "runtime_id": results[0]["runtime_id"],
        "label_recalled": True,
        "file_reads": paths,
        "processes": evidence["processes"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=["openclaw", "hermes"], required=True)
    parser.add_argument("--results-file", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--relay", action="store_true")
    args = parser.parse_args()
    results = json.loads(args.results_file.read_text())["results"]
    evidence = json.loads(args.results_file.with_name("task-evidence.json").read_text())
    summary = verify_results(results, evidence, harness=args.harness, label=args.label)
    if args.relay:
        summary["relay"] = verify_relay(results)
    args.results_file.with_name("verification.json").write_text(
        json.dumps(summary, indent=2) + "\n"
    )
    print(
        f"Verified {args.harness}: two successful turns, label recall, task process persistence, and file reading."
    )


if __name__ == "__main__":
    main()
