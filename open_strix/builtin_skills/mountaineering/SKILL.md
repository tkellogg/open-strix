---
name: mountaineering
description: Autonomous hill-climbing loops for continuous improvement. Use when optimizing something measurable — prompts, configs, code, predictions — through iterative propose/test/keep-or-revert cycles. Also use when figuring out WHAT to optimize and how to measure it.
---

# Mountaineering

The mountaineering skill teaches agents how to climb hills. For any hill that can be identified, set up guardrails and climb it.

This is autoresearch applied as a discipline: assess the mountain, choose the route, pack the right gear, know when to turn back.

## The Five Laws

Every successful climb requires five conditions to hold. See `laws.md` for the full treatment with examples and failure modes.

1. **Orderable Outcomes** — The optimizer must be able to say "this is better than that"
2. **Measurement Consistency** — The metric must score the same way twice
3. **Safe Exploration** — Failed experiments must be fully reversible
4. **Scope Separation** — The optimizer must not control the evaluation
5. **Informed Search** — The optimizer needs domain knowledge to generate targeted hypotheses

If any law is violated, the loop will fail — often expensively.

## Four-Phase Architecture

### Phase 0: Climb Design

The hardest part of mountaineering is figuring out WHAT to climb. This phase turns "I want to improve X" into a fully specified climb. See `climb-design.md` for the complete protocol.

Six steps:
1. **Name the objective** in plain language — the S5 anchor
2. **Instrument** — inventory what data and signals you already have
3. **Candidate metrics** — list 2-3 options, evaluate each against Laws 1-2
4. **Mutable surface** — define what the climber can change and the blast radius
5. **Mutation types** — enumerate edit types for YOUR understanding (not the climber's constraint)
6. **Candidate pipeline** — rank what to try first; this becomes program.md's Context section

Without Phase 0, you arrive at pre-flight with a vague objective and no metric. Pre-flight correctly rejects it, but doesn't help you get ready. Climb design is where you get ready.

### Phase 1: Pre-Flight

Run the pre-flight protocol (`preflight.md`) before starting any climb. Pre-flight is a collaboration between the agent and the operator — the agent runs mechanical checks, the operator provides judgment. A failed pre-flight saves tokens.

Phase 0 outputs map directly to pre-flight inputs — the selected metric feeds Law 1-2 checks, the mutable surface feeds Law 3-4 checks, the mutation inventory feeds Law 5 checks.

### Phase 2: Harness Setup

The harness is the structural scaffolding that enforces the five laws during a climb. See `harness.md` for directory structure templates, config schemas, program.md templates, and evaluation script patterns.

### Phase 3: Climbing (The Loop)

The iteration loop: propose change → test → score → keep or revert → repeat. One change at a time for interpretability. The climber reads failing cases and hypothesizes fixes — Law 5 in action.

**Fast feedback:** Use proxy metrics (binary pass/fail, immediate signals) for the first 10 iterations to confirm the climb is producing movement. Switch to the full metric for trend analysis after stabilization. Any signal that it's working or not is critical early on — don't wait for statistical significance before checking whether the climb is alive.

## The Climber Subagent

The climber is a fundamentally different kind of subagent from identity agents:

| | Identity Agent | Climber |
|---|---|---|
| **Loop** | Event-driven | Infinite loop |
| **Memory** | Blocks + files + journal | Files only (sliding window) |
| **Identity** | Rich persona | None (goal + constraints) |
| **S5** | Scaffolding (blocks, prompts) | Code + program.md (frozen) |
| **Context per turn** | Variable | Fixed budget |
| **Lifespan** | Persistent | Scoped to a climb |

### Climber Memory (Three Layers)

1. **program.md** — Frozen S5. Goal, constraints, scope. The climber cannot edit this.
2. **Workspace + evaluation** — The harness. Evaluation logic is held in supervisor memory (not on disk). The climber operates within the workspace scope.
3. **Results log** — Sliding window of recent results (last N entries via ring buffer). This prevents context growth while maintaining enough history for informed search (Law 5).

### Skill Inheritance

The climber inherits whatever skills and tools the parent agent has configured. If the operator has set up a coding agent (e.g., acpx), the climber gets it automatically — no separate configuration needed. If not, the climber still works with built-in file tools.

This means the first test climb from an agent without a coding agent gets lightweight tools (file read/write/edit). A code-optimization climb launched from an agent WITH a coding agent gets the full toolset. Zero config, correct by default.

### Fixed Context Constraint

**This is load-bearing.** Every iteration must wake up with roughly the same sized context. No accumulated conversational history. Each iteration is a fresh agent invocation that reads: program.md + current workspace files + last N log entries. That's it.

The results log is the climber's only memory between iterations. After each iteration, the climber should read the last N entries to understand what has been tried and what worked. The log is not a record for the operator — it is the climber's operational memory.

### Autonomy Principle

**The climber runs forever until explicitly stopped.** This is the default, not an option.

A climb that finishes and waits for human input is not autonomous — it's a script. The whole point is that the climber keeps going: trying new mutations, backtracking to earlier checkpoints, starting new bases. The human supervises; the climber explores.

What "forever-running" means in practice:
- A plateau on one approach → try a different mutation type, not stop
- A completed run (model converged) → start a new run with different base or parameters
- A crash → self-diagnose, fix, retry — don't wait for human intervention
- Budget exhausted on one climb → report results, start the next climb in the queue

The climber should be harder to stop than to start. If you have to check on it constantly, the autonomy isn't working.

### Loop Structure

Different climbs need different loop structures. There is no universal architecture — the loop should match the problem. One pattern that emerged from SAE training:

1. **Inner loop** (exploration) — try mutations from the current checkpoint. Needs a cap to prevent thrashing, but the cap is per-attempt, not per-climb.
2. **Middle loop** (construction) — consecutive checkpoints building one model/artifact. Stop-checking lives here (overtrain prevention, convergence detection).
3. **Outer loop** (autonomy) — when a run finishes or the middle loop's stop condition fires, keep going. New base, backtrack to earlier checkpoint, try a different direction.

**The critical mistake:** collapsing the middle and outer loops so that the middle loop's stop condition kills the entire climb. The stop-check ("consecutive runs didn't improve") is correct for the middle loop but the outer loop should catch that termination and treat it as "this path is done, try another" — not "we're done entirely."

This three-loop structure is NOT universal. It's one design for one class of climbs (long training runs with checkpoints). Other climbs may be single-loop. The principle is: make sure your loop structure separates "this attempt is done" from "the whole search is done."

### Supervision Protocol

The supervisor monitors; the climber runs. Not the other way around.

**Supervisor responsibilities:**
- Watch for structural problems the climber can't self-diagnose
- Inject new information when the search space needs expansion (git commits to workspace)
- Kill climbs that are no longer relevant (goal changed, not just stuck)
- Declare peaks and set up the next climb

**Intervention decisions:**
- **Keep running** — trend positive or climber is self-recovering from plateaus
- **Investigate** — climber is self-recovering but the recovery pattern looks wrong
- **Inject information** — make git commits that change the workspace; the climber picks up changes next iteration
- **Kill** — goal changed, budget hard-capped, or the hill itself was wrong (not just stuck)

**Peak detection → ridgeline traversal:** When the hill is peaked, the supervising agent declares the peak, selects the next hill, and either reconfigures the current climb or starts a new one. See `philosophy.md` for the full framework.

## Remote Deployment

Climbers often need to run on machines with resources the supervising agent doesn't have (GPU, large datasets, specific hardware). This creates a communication gap.

**The pattern:**
1. **Ship the loop** — the entire climb directory (program.md, config.json, eval/, workspace/) gets deployed to the remote machine
2. **Climber runs independently** — no real-time communication with the supervisor
3. **Results flow back** — logs/results.jsonl is the interface. Pull periodically, or push on completion.
4. **Supervisor operates asynchronously** — reads results, decides on intervention, pushes workspace changes

**What this means for the skill:**
- The climb directory must be self-contained — everything the climber needs is in that directory
- The eval script must work on the target machine (correct deps, data paths)
- The climber's self-recovery is more important when there's no real-time supervisor
- Fast feedback matters MORE on remote machines — you can't watch the terminal

**Communication gap workarounds:**
- Structured log files that the supervisor can pull and parse
- Checkpoint notifications (webhook, file write to shared storage, message to channel)
- Heartbeat files (write timestamp every N iterations — if stale, something died)

This is an active gap in the framework. Good messaging between the supervisor and a remote climber is an unsolved problem. Document your approach when you find one that works.

## Background Reading

For the theoretical framework behind mountaineering — VSM mapping, anti-gaming philosophy, recursive nesting, metric over-adherence — see `philosophy.md`. Understanding this background helps the supervising agent make better judgment calls, but it is not required for basic operation.
