---
name: patterns
description: Index of recurring problem shapes — environmental awareness (world-scanning, OS event hooks, browser automation), communication (messaging, multi-agent handoff, async waits), coordination between parallel things (avoiding collisions, oscillation, double-work), and self-correction (circuit-breaker, try-harder). Use when designing a non-trivial workflow, when the same shape just happened twice, or when the answer to "what should I do here?" isn't obvious. Don't read on every turn.
---

Having problems that look like this? 

* Out-of-date info — world-scanning.md
* Awareness of surroundings — world-scanning.md
* Getting ahead of problems — world-scanning.md
* Sending information or messages between computers — messaging.md
* Notifying another agent that something happened — messaging.md
* Reacting when the user opens their laptop, locks the screen, plugs in a USB, joins Wi-Fi, etc. — os-events-macos.md / os-events-windows.md / os-events-linux.md
* Hooking into anything the operating system already knows about — os-events-{macos,windows,linux}.md
* Scraping a site that has no API, especially behind a login wall — browser-automation.md
* Driving a SaaS dashboard to click a button or download a file — browser-automation.md
* Personal-data dashboards from services that don't expose one — browser-automation.md
* Need to wait for a human action (click, login, file drop) without burning tokens — async-tasks.md
* "Wake me up when X happens" where X is a one-shot event — async-tasks.md
* Pausing this conversation across an indeterminate wait without losing context — async-tasks.md
* Handing work to another agent (sub-agent, peer, fresh self) — multi-agent-handoff.md
* What survives across a fresh-context turn (async wake-up, schedule fire, compaction) — context-boundaries.md
* How to write journal entries that future-you / introspection-you will actually find useful — journal-as-breadcrumbs.md
* Picking the right temporal primitive (poller / schedule / loop / async-block / OS cron) — scheduling.md
* Building redundancy into channels, scrapers, sources without overengineering — fallback-chains.md
* Recognizing your own loops / runaway behavior and stopping yourself — circuit-breaker.md
* Stuck on something — what to do *instead* of grit (edit blocks, edit checkpoint.md, find conflicts) — try-harder.md
* The instinct to "try harder" / "do better" / "be more careful" — try-harder.md (almost always the wrong move)
* Two parallel things stepping on each other (schedules colliding, pollers in storm, double-messaging, oscillating state files) — coordination.md
* "It worked fine for weeks then suddenly two of X happened" — coordination.md (S2 collision)
* No durable place to track what you've noticed, committed to, or finished — interest-backlog.md
* "I should remember to look into X" / "that was weird but I don't have time now" — interest-backlog.md (log it, drain it later)

## Two rules worth pulling forward

These show up repeatedly in the sub-pages but are easy to miss if you only skim the index.

* **The artifact of trying harder is a diff someone else can verify, not a resolution someone else has to trust.** If your "fix" is a commitment to do better, it dies the moment your turn ends. See `try-harder.md`.
* **Journal entries should have handles, not prose.** `j_abc123` and `/tmp/rl.md` survive a context boundary; "I spawned a job and will check on it" doesn't. See `journal-as-breadcrumbs.md`.

It's worth running a five-why's on your problem to see if some of this info applies.

