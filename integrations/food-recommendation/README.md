# integrations/food-recommendation — daily food pick

Tasks: T-605 (interface + mock, Gemini), T-607 (real Stanford provider, Claude). Feature flag: `FEATURE_FOOD`.

The package defines the boundary between the wallboard tile and whatever picks
the food, so the UI and worker never know which provider is behind it:

```
FoodRecommendationProvider.get_daily_recommendation() -> Recommendation
  Recommendation: {date, title, detail?, provider}
```

## Providers

- **`mock`** (default) — deterministic per-date fixture rotation for dev, CI, and demos.
- **`stanford`** — the real personal system (T-607). Lives in `food_recommendation/stanford/`:
  1. **scraper** — GETs the Stanford R&DE menu page, extracts the ASP.NET form
     state, and POSTs per dining hall (via `httpx`, bounded/streamed reads, no
     redirects; BeautifulSoup with the stdlib `html.parser` backend).
  2. **scorer** — keyword rules from bundled `preferences` rank halls by whole
     foods, protein quality, vegetables, and legumes while penalising hard-avoids.
  3. **recommender** — optionally sends the scored menus to OpenAI (via `httpx`,
     no `openai` SDK) for a natural pick; any failure falls back to the
     deterministic score, so the LLM is a strict enhancement.

  The rich internal result is mapped down to the public `Recommendation`
  (`title` = "<best hall> — <meal>", `detail` = plate + confidence + backup,
  `provider` = `stanford` or `stanford-ai`).

Select the provider with `FOOD_PROVIDER=mock|stanford`. The real provider only
runs when `FEATURE_FOOD=true` **and** `FOOD_PROVIDER=stanford`; see `.env.example`
for all keys (`FOOD_USE_AI`, `OPENAI_API_KEY`, `FOOD_HALL_IDS`, …).

## Behaviour

- One recommendation per day, cached. All network + LLM work runs in the
  wallboard-worker (NUC) daily job — never on the door critical path.
- On any provider failure the job emits **yesterday's** cached pick, whose older
  `occurred_at` renders as a staleness marker on the tile.
- Dependencies (ADR-0003 / ADR-0010): `pydantic`, `httpx` (already in the
  workspace), and `beautifulsoup4`. No `lxml`, `openai`, `requests`, `pytz`, or
  `pyyaml` — HTML parsing uses the stdlib backend, timezones use `zoneinfo`, and
  preferences ship as bundled Python data.
