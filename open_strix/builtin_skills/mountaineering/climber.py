#!/usr/bin/env python3
"""
Mountaineering climber runtime.

A loop-based subprocess that uses a LangGraph DeepAgent to propose changes,
test them via supervisor-provided evaluation, and keep or revert based on
results. Each iteration is a fresh agent invocation with fixed context —
no accumulated conversational history.

Usage:
    python climber.py /path/to/climb/directory [options]

The climb directory must contain:
    - program.md    (frozen S5 — goal, constraints, scope)
    - config.json   (climb configuration)
    - eval/         (evaluation scripts — run by supervisor, not climber)
    - workspace/    (mutable surface)
    - logs/         (results log)

The climber uses LangGraph DeepAgent for all operations — file reading,
editing, and git are handled by the agent's built-in tools. The model
is configured via the --model flag (e.g., "openai:gpt-4o-mini",
"anthropic:claude-sonnet-4-6").
"""

import json
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path


def _start_heartbeat_monitor(fd: int):
    """Monitor the heartbeat pipe from the supervisor.

    When the supervisor dies (any OS), the write end closes, read returns
    EOF, and we exit. Cross-platform: no prctl, no Windows Job Objects.
    """
    def _monitor():
        try:
            os.read(fd, 1)  # blocks until parent dies → EOF
        except OSError:
            pass
        os._exit(0)

    t = threading.Thread(target=_monitor, daemon=True)
    t.start()


def load_config(climb_dir: Path) -> dict:
    """Load climb configuration."""
    config_path = climb_dir / "config.json"
    if not config_path.exists():
        print(f"ERROR: {config_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(config_path) as f:
        return json.load(f)


def load_program(climb_dir: Path) -> str:
    """Load the frozen program (S5)."""
    program_path = climb_dir / "program.md"
    if not program_path.exists():
        print(f"ERROR: {program_path} not found", file=sys.stderr)
        sys.exit(1)
    with open(program_path) as f:
        return f.read()


def load_recent_results(climb_dir: Path, window: int) -> list[dict]:
    """Load the last N results from the log using a ring buffer.

    Streams the file line-by-line with a bounded deque — O(window) memory
    regardless of log file size.
    """
    log_path = climb_dir / "logs" / "results.jsonl"
    if not log_path.exists():
        return []
    ring = deque(maxlen=window)
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    ring.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return list(ring)


def get_iteration_count(climb_dir: Path) -> int:
    """Get current iteration number by counting log lines.

    Counts lines without parsing JSON — O(1) memory.
    """
    log_path = climb_dir / "logs" / "results.jsonl"
    if not log_path.exists():
        return 0
    count = 0
    with open(log_path) as f:
        for line in f:
            if line.strip():
                count += 1
    return count


def append_result(climb_dir: Path, result: dict):
    """Append a result to the log."""
    log_dir = climb_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "results.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(result) + "\n")


def run_eval(climb_dir: Path, config: dict) -> dict | None:
    """Run the evaluation script and return the result.

    The eval runs in the climb directory context. The climber calls this
    but the eval script itself is managed by the supervisor (Law 4).
    """
    eval_cmd = config.get("eval_command", "python eval/eval.py")
    try:
        result = subprocess.run(
            eval_cmd,
            shell=True,
            cwd=str(climb_dir),
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            print(f"Eval error (exit {result.returncode}): {result.stderr}", file=sys.stderr)
            return None
        return json.loads(result.stdout.strip())
    except subprocess.TimeoutExpired:
        print("Eval timed out (300s)", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"Eval output not valid JSON: {e}", file=sys.stderr)
        return None


def create_climber_agent(
    model_name: str,
    climb_dir: Path,
    skills: list[str] | None = None,
):
    """Create a LangGraph DeepAgent configured for climbing.

    The agent gets built-in file tools (read, write, edit, glob, grep)
    and shell execution. Skills are inherited from the parent agent —
    if the operator has a coding agent configured, the climber gets it
    too. No memory blocks, just tools and a system prompt.

    Law 4 enforcement: The backend uses WriteGuardBackend to restrict
    writes to workspace/ only. The agent can READ program.md, config.json,
    eval/, and logs/ but can only MODIFY files in workspace/. This is
    architectural enforcement — "don't give them the lock" — not prompt-
    level enforcement ("please don't modify eval files").
    """
    from deepagents import create_deep_agent
    from langchain.chat_models import init_chat_model

    # Import from readonly_backend — lightweight module that doesn't
    # pull in discord/apscheduler/etc. Same class used by the main agent.
    from open_strix.readonly_backend import WriteGuardBackend

    model = init_chat_model(model_name)

    # Backend: read everything in climb_dir, write only to workspace/
    # This is Law 4 by architecture — the climber literally cannot modify
    # eval files, program.md, or config.json through its tools.
    backend = WriteGuardBackend(
        root_dir=climb_dir,
        writable_dirs=["workspace"],
    )

    system_prompt = (
        "You are a hill-climbing optimizer. Your job is to propose ONE small, "
        "targeted change per iteration to improve a score.\n\n"
        "You have file tools available (read_file, write_file, edit_file, glob, grep). "
        "Use them to examine the workspace and make changes directly.\n\n"
        "Rules:\n"
        "- Make exactly ONE change per iteration\n"
        "- Only modify files in the workspace/ directory\n"
        "- Read the recent results log to understand what's been tried\n"
        "- Base your proposal on patterns in the results — informed search, not random changes\n"
        "- After making a change, report what you changed and why\n"
    )

    return create_deep_agent(
        model=model,
        system_prompt=system_prompt,
        backend=backend,
        name="climber",
        skills=skills or None,
    )


def run_agent_iteration(
    agent,
    program: str,
    recent_results: list[dict],
    iteration: int,
) -> dict:
    """Run one iteration of the climbing agent.

    Returns a dict with keys: success, change_description, plateau
    """
    results_str = ""
    if recent_results:
        for r in recent_results:
            results_str += (
                f"  iter {r.get('iteration', '?')}: "
                f"score={r.get('score', '?')}, "
                f"decision={r.get('decision', '?')}, "
                f"change={r.get('change', '?')}\n"
            )
    else:
        results_str = "  (no previous results — this is the first iteration)\n"

    prompt = (
        f"## Your Program (DO NOT MODIFY)\n{program}\n\n"
        f"## Recent Results (last {len(recent_results)} iterations)\n{results_str}\n"
        f"## Current Iteration: {iteration}\n\n"
        "Examine the workspace files, analyze the recent results, and make ONE "
        "targeted change to improve the score. Use your file tools to read the "
        "current state and edit_file to make changes.\n\n"
        "After making your change (or deciding no change would help), respond with "
        "a JSON summary:\n"
        '```json\n{"change": "description of what you changed and why"}\n```\n'
        "Or if you believe the current state is optimal:\n"
        '```json\n{"plateau": true, "reasoning": "why no change would help"}\n```'
    )

    try:
        # Invoke the agent synchronously — each iteration is independent
        result = agent.invoke(
            {"messages": [{"role": "user", "content": prompt}]},
        )

        # Extract the agent's final message
        messages = result.get("messages", [])
        if not messages:
            return {"success": False, "change_description": "No response from agent"}

        last_msg = messages[-1]
        content = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Parse JSON from response
        if "```json" in content:
            json_text = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            json_text = content.split("```")[1].split("```")[0].strip()
        else:
            json_text = content.strip()

        try:
            parsed = json.loads(json_text)
            if parsed.get("plateau"):
                return {
                    "success": True,
                    "plateau": True,
                    "change_description": parsed.get("reasoning", "no reasoning"),
                }
            return {
                "success": True,
                "plateau": False,
                "change_description": parsed.get("change", content[:200]),
            }
        except json.JSONDecodeError:
            # Agent made changes but didn't format JSON — still counts
            return {
                "success": True,
                "plateau": False,
                "change_description": content[:200],
            }

    except Exception as e:
        return {"success": False, "change_description": f"Agent error: {e}"}


def git_snapshot(climb_dir: Path, message: str):
    """Create a git commit of the current workspace state (Law 3)."""
    workspace = climb_dir / "workspace"
    try:
        subprocess.run(
            ["git", "add", str(workspace)],
            cwd=str(climb_dir),
            capture_output=True,
            timeout=30,
        )
        subprocess.run(
            ["git", "commit", "-m", message, "--allow-empty"],
            cwd=str(climb_dir),
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        print(f"Git snapshot failed: {e}", file=sys.stderr)


def git_revert_workspace(climb_dir: Path):
    """Revert workspace to the previous commit (Law 3)."""
    try:
        subprocess.run(
            ["git", "checkout", "HEAD~1", "--", str(climb_dir / "workspace")],
            cwd=str(climb_dir),
            capture_output=True,
            timeout=30,
        )
    except Exception as e:
        print(f"Git revert failed: {e}", file=sys.stderr)


def climb_loop(climb_dir: Path, model_name: str, skills: list[str] | None = None):
    """Main climbing loop. Runs until killed or budget exhausted."""
    config = load_config(climb_dir)
    program = load_program(climb_dir)
    max_iterations = config.get("max_iterations", 500)
    results_window = config.get("results_window", 20)
    sleep_between = config.get("sleep_between_iterations", 5)

    print(f"Climber starting: {config.get('climb_id', 'unknown')}")
    print(f"Model: {model_name}, Max iterations: {max_iterations}, Window: {results_window}")
    if skills:
        print(f"Inherited skills: {skills}")

    # Create the agent once — reused across iterations but each invocation
    # is independent (no accumulated conversation state)
    agent = create_climber_agent(model_name, climb_dir, skills=skills)

    while True:
        iteration = get_iteration_count(climb_dir)

        # Budget check
        if iteration >= max_iterations:
            print(f"Budget exhausted at iteration {iteration}")
            break

        # Read recent results (ring buffer — bounded memory)
        recent_results = load_recent_results(climb_dir, results_window)

        # Baseline eval (before change)
        baseline = run_eval(climb_dir, config)
        if baseline is None:
            print("Baseline eval failed, sleeping and retrying...", file=sys.stderr)
            time.sleep(30)
            continue
        baseline_score = baseline.get("score", 0)

        # Law 3: snapshot before change
        git_snapshot(climb_dir, f"pre-change-iter-{iteration}")

        # Run the agent for one iteration
        agent_result = run_agent_iteration(agent, program, recent_results, iteration)

        if not agent_result["success"]:
            result = {
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change": f"AGENT ERROR: {agent_result['change_description']}",
                "score": baseline_score,
                "previous_score": baseline_score,
                "decision": "skip",
            }
            append_result(climb_dir, result)
            print(f"[iter {iteration}] Agent error: {agent_result['change_description']}")
            time.sleep(30)
            continue

        # Plateau detection
        if agent_result.get("plateau"):
            result = {
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change": f"PLATEAU: {agent_result['change_description']}",
                "score": baseline_score,
                "previous_score": baseline_score,
                "decision": "plateau",
            }
            append_result(climb_dir, result)
            print(f"[iter {iteration}] PLATEAU: {agent_result['change_description']}")
            time.sleep(300)  # Sleep longer — supervisor should notice and intervene
            continue

        # Eval after change
        new_eval = run_eval(climb_dir, config)
        if new_eval is None:
            git_revert_workspace(climb_dir)
            result = {
                "iteration": iteration,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "change": f"EVAL FAILED after: {agent_result['change_description']}",
                "score": baseline_score,
                "previous_score": baseline_score,
                "decision": "revert",
            }
            append_result(climb_dir, result)
            print(f"[iter {iteration}] Eval failed after change, reverted")
            time.sleep(sleep_between)
            continue

        new_score = new_eval.get("score", 0)

        # Keep or revert
        if new_score >= baseline_score:
            decision = "keep"
            git_snapshot(
                climb_dir,
                f"keep-iter-{iteration}: {agent_result['change_description'][:80]}",
            )
            print(
                f"[iter {iteration}] KEEP: {baseline_score} -> {new_score} "
                f"({agent_result['change_description'][:60]})"
            )
        else:
            decision = "revert"
            git_revert_workspace(climb_dir)
            print(
                f"[iter {iteration}] REVERT: {baseline_score} -> {new_score} "
                f"({agent_result['change_description'][:60]})"
            )

        # Log result
        result = {
            "iteration": iteration,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "change": agent_result["change_description"],
            "score": new_score if decision == "keep" else baseline_score,
            "previous_score": baseline_score,
            "decision": decision,
            "details": new_eval.get("details", {}),
        }
        append_result(climb_dir, result)

        time.sleep(sleep_between)


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Mountaineering climber runtime")
    parser.add_argument("climb_dir", help="Path to climb directory")
    parser.add_argument(
        "--model",
        default=None,
        help="LangGraph model string (e.g., 'anthropic:claude-sonnet-4-6', 'openai:gpt-4o-mini'). "
        "Defaults to CLIMBER_MODEL env var or 'anthropic:claude-sonnet-4-6'.",
    )
    parser.add_argument(
        "--heartbeat-fd",
        type=int,
        default=None,
        help="File descriptor for heartbeat pipe from supervisor",
    )
    parser.add_argument(
        "--skills",
        nargs="*",
        default=None,
        help="Skill directory paths inherited from parent agent. "
        "The climber gets whatever tools the operator has configured.",
    )
    args = parser.parse_args()

    # Start heartbeat monitor if supervisor passed a pipe fd
    if args.heartbeat_fd is not None:
        _start_heartbeat_monitor(args.heartbeat_fd)

    climb_dir = Path(args.climb_dir).resolve()
    if not climb_dir.is_dir():
        print(f"ERROR: {climb_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Ensure required structure exists
    for required in ["program.md", "config.json"]:
        if not (climb_dir / required).exists():
            print(f"ERROR: {climb_dir / required} not found", file=sys.stderr)
            sys.exit(1)

    (climb_dir / "logs").mkdir(exist_ok=True)

    model_name = args.model or os.environ.get(
        "CLIMBER_MODEL", "anthropic:claude-sonnet-4-6"
    )

    try:
        climb_loop(climb_dir, model_name, skills=args.skills)
    except KeyboardInterrupt:
        print("\nClimber stopped by signal")
        sys.exit(0)


if __name__ == "__main__":
    main()
