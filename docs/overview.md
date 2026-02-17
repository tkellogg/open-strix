This is the code for a general highly-autonomous agent. It's modeled after Strix, who is an agent built on
Claude Code. Here, we use LangGraph DeepAgents to construct a similar experience.

# The Home Repo
This repo here is where the code exists. The home repo is somewhere else. This repo here is just a library with an entry point.

A user would create a fresh open-strix agent by doing:
1. `mkdir new-agent`
2. `uv init --python 3.11`
3. `uv add open-strix`
4. `uv run open-strix`

From there, the repo structure is initialized, Git hooks are installed, and then the agent starts. The next
time the agent starts without any initialization. When the agent starts, it connects to discord and subscribes to
events.

The home repo structure looks like:

```
skills/
  ...
state/
  ...    # markdown files & directories that constitute the agent's long-term memory
blocks/
  ...    # yaml files, one per memory block. Manipulated via tools. Contains a name, sort order & text
logs/
  events.jsonl  # Documents everything that happens over the course of the agent. Roll these logs every 1 MB ish
  journal.jsonl # Written mostly by the agent as a log for what they've done
scheduler.yaml  # A list of jobs that are currently scheduled
config.yaml
```

# User Interface: Discord
There are `send_message` and `list_messages` tools. Both take a channel ID, but default to the channel the last message came in on.
The bot should support all file attachments. However, file attachments aren't inserted into the prompt by default,
only the file name is (where the agent can read it from a file, it's been saved to disk first.

The final message is generally thrown out (actually, we log it to events.jsonl)

# The Prompt
The main system prompt just tells the agent how to work with the tools it has available to it, and to what end.
The majority of the character of the agent is crafted & evolved through memory blocks (mostly) as well as files.

The user prompt looks like this:
1. last 90 journal entries (configurable)
2. memory blocks
3. last 10 discord messages (configurable)
4. current discord message + reply channel

# Logs
all dates everywhere are UTC and only converted to the user's local time when being displayed.

## events.jsonl
Every tool call is recorded here. Every error is recorded here. Every other event is recorded here, like scheduler
triggers, discord incoming messages, etc. Also, every final message (that gets thrown out otherwise) is logged 
here too.

The schema here is structured but not verbose. The intent is for the agent to be able to trace what it was doing,
debug itself, as well as allowing the user to monitor what's happening.

## journal.jsonl
The structure of the journal entry is:
- timestamp
- user_wanted
- agent_did
- predictions (make predictions about how the user will respond, or if a memory block change will actually change behavior, etc.) Predictions are revisited later.

Entries are captured via the `journal` tool. After calling this tool, the prompt in `checkpoint.md` is read and 
returned from the `journal` tool. This file will evolve with the agent, but a good starting point is a list of
questions for the agent, about it's own behavior, and some encouragement to try to remedy things that didn't go well.

# Scheduler
The agent has tools for scheduling jobs via APScheduler. There are tools for manipulating schedules. The yaml file
contains jobs: 
- name
- cron expression (if recurring)
- time of day (if not recurring)
- prompt

When the scheduler goes off, it sends a message to the agent.

# Agent Loop
While the agent is processing an event, no other events can be processed. They wait in a queue, patiently for their
turn. Scheduler events should never have duplicate events sitting in the queue. The queue isn't durable. If the agent
shuts down, those events aren't processed.

The model is [MiniMax M-2.5](https://www.minimax.io/news/minimax-m25) accessed via the Anthropic API.

The harness should strongly discourage editing outside `state/`. In this minimal implementation, writes outside
`state/` are blocked by the backend policy.

# Git
The agent should commit and push everything in it's home directory before it writes the final message. The logs are
not under Git, but everything else is. The Git history provides a way for both user and agent to have clarity on 
what's going on.

# Skills
Some skills are packaged with open-strix. These show the agent how to use the harness properly, and also encode
some cybernetics-rooted processes.

# config.yaml
Where most harness config goes. There's also a `.env` for secrets, please load that as environment variables.
