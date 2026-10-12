# Scheduling the sweep

`python -m app.cli sweep` discovers new postings (re-running every enabled job
source) and then notifies users whose confirmed profile clears their own
`notify_score_threshold` for an open, non-demo job. It is meant to run unattended,
on a timer, from `backend/` with the project's virtual environment active — not
interactively, and not as a long-running daemon. Nothing here needs Docker, Redis,
or a worker process; see `docs/PLAN.md`'s Phase 4 amendment for why.

It is safe to schedule aggressively: every write it makes is independently
idempotent (re-ingesting a posting upserts it; re-scoring a job upserts the stored
assessment; a notification is suppressed if its dedupe key already exists), and a
Postgres advisory lock makes an overlapping trigger a safe no-op rather than
duplicate work. A 30–60 minute interval is a reasonable default for a personal,
locally-run instance.

## Windows — Task Scheduler

GUI, one-time setup:

1. Open **Task Scheduler** → **Create Task...** (not "Create Basic Task" — this
   exposes the "Start in" working directory field and the security/conditions
   options below, which the basic wizard omits).
2. **General** tab: name it `CareerPilot sweep`. Under "Security options", select
   **Run whether user is logged on or not** if you want it to keep running after
   you sign out (requires a saved password once, when prompted).
3. **Triggers** tab → **New...** → **Daily**, recurring, then **Repeat task
   every** `30 minutes` for a duration of `1 day` (this is how Task Scheduler
   expresses "every 30 minutes, indefinitely").
4. **Actions** tab → **New...** → **Start a program**:
   - Program/script: the full path to the project's venv Python, e.g.
     `C:\web dev projects\career_pilot\backend\.venv\Scripts\python.exe`
   - Add arguments: `-m app.cli sweep`
   - **Start in**: `C:\web dev projects\career_pilot\backend` — recommended for
     clarity and consistency with the rest of this guide, but not strictly
     required: the CLI resolves every settings, storage, and database path from
     its own file location, not from the process's working directory, so it
     runs correctly regardless of what "Start in" is set to.
5. **Conditions** tab: uncheck "Start the task only if the computer is on AC
   power" on a laptop, if you want it to run on battery too.
6. Save. Right-click the new task → **Run** once to confirm it exits `0` (check
   **Last Run Result** = `0x0`).

Command-line equivalent (run once, from an elevated or ordinary prompt — adjust
the path to match your checkout):

```powershell
schtasks /create /tn "CareerPilot sweep" /tr "'C:\web dev projects\career_pilot\backend\.venv\Scripts\python.exe' -m app.cli sweep" /sc minute /mo 30 /sd 01/01/2026 /st 00:00
```

`schtasks` has no "Start in" option at all, which is fine here — the command
above works regardless of working directory, for the same reason noted above.
If you'd rather set an explicit working directory anyway (e.g. for consistency
with other scheduled tasks, or to use a relative log path), wrap the call in a
`.bat` file that `cd`s into `backend\` first:

```bat
@echo off
cd /d "C:\web dev projects\career_pilot\backend"
".venv\Scripts\python.exe" -m app.cli sweep
```

and point the scheduled task's action at that `.bat` file instead.

## Linux / macOS — cron

```cron
*/30 * * * * cd /path/to/career_pilot/backend && .venv/bin/python -m app.cli sweep >> /path/to/career_pilot/var/sweep.log 2>&1
```

`crontab -e` to add it. The `cd` isn't needed for the CLI itself (it resolves its
own settings, storage, and database paths independent of the working directory)
— it's there so the shell can find the relative `.venv/bin/python` path. Use an
absolute path to the interpreter instead and the `cd` becomes optional too.

## Checking it worked

- `python -m app.cli sweep` prints a one-line summary per stage (sources checked,
  notifications created) and exits `0` on success — including when some sources
  failed or the lock was already held (that is reported, not treated as an error);
  it exits `1` only on a genuinely unexpected failure.
- The in-app **Notifications** page shows anything the sweep created.
- `GET /api/v1/jobs` reflects newly discovered or updated postings.
- The sweep's own audit rows (`AgentRun`, kind `discover`/`notify_sweep`) have no
  single owning user (`user_id` is `NULL` for a scheduled run), so they are not
  visible through the per-user `GET /api/v1/agent-runs` endpoint or the Activity
  page — check the structured logs (or query `agent_runs` directly) if you need to
  audit a scheduled run specifically, rather than looking for it in the UI.
