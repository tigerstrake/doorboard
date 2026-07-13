# tools/project-stats

Computes the "About this project" fun facts shown on the wallboard **About
Doorboard** tile and the `/admin` About panel (T-608).

```sh
python tools/project-stats/collect.py           # writes apps/door-ui/src/aboutStats.json
python tools/project-stats/collect.py --print    # also print to stdout
python tools/project-stats/collect.py --out /tmp/stats.json
```

What it measures (git-tracked files only, so `node_modules`/`.venv`/build output
never leak in):

- **Lines of code** by language (Python, TypeScript, JavaScript, C/C++, Shell).
  Generated schemas/types and lock/binary files are excluded from the count.
- **Structural counts**: services, shared packages, integrations, ADRs, task
  briefs, and contract event types.

The output is a static, build-time snapshot baked into the UI. Re-run after
significant changes to refresh the numbers, then commit the updated
`aboutStats.json`. Requires Python 3.12+ (matches the repo).
