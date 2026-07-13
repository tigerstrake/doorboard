# T-607: Real Stanford dining food provider

**Agent:** claude · **Milestone:** M6 · **Depends on:** T-605

## Context

T-605 built the `FoodRecommendationProvider` interface + mock and deferred "the
real personal food system (future brief against this interface)". This is that
brief. The real system is the owner's Stanford dining recommender: it scrapes the
Stanford R&DE menu site, scores each hall's menu against dietary preferences, and
(optionally) asks an LLM for a natural pick.

Security/privacy-critical (external scraping, an API secret, a public wallboard
tile) so it is `agent:claude`. Spec: [integrations/food-recommendation/README.md](../../integrations/food-recommendation/README.md).
Adding `beautifulsoup4` is an ADR-0003 escalation, resolved by [ADR-0010](../adr/0010-food-scraper-dependency.md).

## Deliverables

- `StanfordDiningProvider` behind the existing `FoodRecommendationProvider`
  interface (no interface or contract change): scrape → score → optional LLM →
  map to the public `Recommendation{date, title, detail, provider}`.
- Pipeline modules under `food_recommendation/stanford/` (models, preferences,
  meal detection, scraper, scorer, recommender, provider). `httpx` for all HTTP
  (menus + OpenAI); BeautifulSoup `html.parser` backend; stdlib `zoneinfo`;
  bundled preferences (no `lxml`/`openai`/`pytz`/`pyyaml`/`requests`).
- Worker wiring: `FOOD_PROVIDER=mock|stanford` selection in the
  `food-recommendation` CLI command, new `Settings` fields, `.env.example` keys.
- `beautifulsoup4` added to the integration's `pyproject.toml` + `uv.lock`; ADR-0010.
- Offline tests (mocked `httpx`, saved HTML fixture) for parsing, scoring,
  recommendation, LLM path + fallback, provider mapping, and CLI selection.

## Out of scope

The `ambient.food_recommendation` contract and the daily-job/cache/fallback flow
(already shipped in T-605 — reused unchanged). New tile layouts. Discord/stdout
notification (the wallboard tile replaces it). Scheduling the job (existing infra).

## Acceptance criteria

- With `FEATURE_FOOD=true` + `FOOD_PROVIDER=stanford` the provider returns a
  mapped `Recommendation`; default config keeps the mock provider.
- Real provider failure raises so the worker emits yesterday's cached pick.
- LLM is opt-in (`FOOD_USE_AI` + `OPENAI_API_KEY`); any LLM failure falls back to
  deterministic scoring. The API key is never logged.
- No door-critical-path work added; CI green (lint, typecheck, tests) with no
  network and no hardware.
