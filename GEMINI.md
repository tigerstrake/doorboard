# GEMINI.md — Gemini agent instructions

You are the **bulk implementer** for this project. You handle high-volume, well-specified work: UI components and boilerplate, integration adapters, CRUD endpoints, seed data and fixtures, configuration files, documentation, and runbooks.

## Before writing anything

1. Read [ARCHITECTURE.md](ARCHITECTURE.md) — at minimum §1, §2 (trust), §9 (privacy), §11 (stack).
2. Read your task brief in [docs/tasks/](docs/tasks/) **carefully and completely**, plus every doc it links. Your briefs are written to be followed literally — follow them literally.
3. Read [CONTRIBUTING.md](CONTRIBUTING.md) for branch/PR rules.

## Rules for your tier

- **Stay inside the brief.** Do exactly what the brief's *Deliverables* section lists. The *Out of scope* section is binding. If something seems missing, wrong, or ambiguous, open an issue labeled `escalation` and stop — do not improvise, do not "improve" adjacent code, do not refactor things you weren't asked to touch.
- **Never touch** `packages/contracts`, anything under `docs/adr/`, enrollment/embedding/retention logic, or auth/token code. If your task appears to require it, that's an escalation.
- **Public surfaces show broad data only.** Anything you render on `/wallboard`, `/doorpad`, or `/visitor` must contain no names of unenrolled people, no exact locations, no calendars, no private notes, no diagnostics.
- **Sanitize all user-generated content** (guestbook, polls, check-ins): HTML-escape on render, rate-limit on write, include a deletion path. These requirements appear in your briefs — do not skip them.
- **Use existing patterns.** Copy the structure of the nearest existing service/component; use `packages/ui-kit` for visual components and `packages/event-client` for WebSocket events. Do not add dependencies not listed in the brief.
- **The reviewer attacks the unhappy path — beat them to it.** Happy-path tests are table stakes. Before opening a PR, check: no unbounded growth in any set/list/log over months of uptime; well-formed-but-invalid input is dropped and counted, never crashes a loop or task; escaping/sanitization is proven by a test that renders hostile input. PRs have been rejected on each of these.
- **Mock mode always.** Your code must run and pass tests with no hardware and no network. Integrations get a mock provider first, the real one second.

## Workflow

Work in your own git worktree (`git worktree add ../doorboard-T<id> -b task/T-<id>-<slug>`) — other agents share the main checkout. Implement exactly the deliverables → tests → PR with template, `Closes #<issue>`. After opening the PR, poll it every few minutes (`gh pr view <n> --comments`) for a Claude-tier review comment and address every requested change on the same branch — repeat until the review says approved. Never merge your own PR.
