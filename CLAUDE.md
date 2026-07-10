# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->


## Build & Test

_Add your build and test commands here_

```bash
# Example:
# npm install
# npm test
```

## Architecture Overview

_Add a brief overview of your project architecture_

## Conventions & Patterns

### DB session resilience

The bot currently uses one process-global scoped session (`DB.s`). A DB error on any code path can poison it for every later operation, so failures MUST be recovered at the boundary where they surface.

**Recovery convention (effective now):**

- Every entry point that can see a DB exception — a task-loop error handler, an event-handler `except` block, `on_application_command_error` — MUST call `recover_session()` from `db/session_guard.py` before continuing.
- New task loops MUST use `recover_loop()` from `bot/utils/loop_recovery.py` instead of a hand-rolled error handler.
- NEVER put `DB.s.commit()` in a `finally` block.

**Unit-of-work target (adopted incrementally):**

Today's `DB.s` is itself a scoped session; the target is moving AWAY from process-global state to a session scoped per logical operation.

- One logical operation (a command invocation, one event, one loop iteration) = one session and one transaction. The entry point opens `with DB.Session() as session:` (sqla-wrapper's sessionmaker, which coexists with `DB.s`), passes `session` into `db/helpers` functions as a parameter, and commits once at the end on success. The context manager guarantees close; an error rolls back that session without touching anyone else's.
- Helpers migrated to this style take `session` as their first argument and NEVER call `commit()` themselves — transaction boundaries belong to the entry point.
- When researching new work, consider refactoring the DB surfaces being touched to this unit-of-work pattern as part of that work. Unmigrated code keeps using `DB.s` + `recover_session()` in the meantime.
