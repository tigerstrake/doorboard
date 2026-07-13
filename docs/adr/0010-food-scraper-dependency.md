# ADR-0010: Admit `beautifulsoup4` for the Stanford dining scraper

**Status:** Accepted · **Date:** 2026-07-14

## Context

T-605 shipped the `FoodRecommendationProvider` interface plus a mock, and
deferred "the real personal food system" to a future brief against that
interface. T-607 implements that real provider: it scrapes the Stanford R&DE
dining-hall menu site (a classic ASP.NET WebForms page — `__VIEWSTATE` state and
CSS-class-tagged menu rows), scores menus against dietary preferences, and
optionally asks an LLM to refine the pick.

Robustly extracting hidden form fields and menu items from that HTML needs a
real parser. ADR-0003 keeps the stack "boring on purpose" and requires an
escalation before adding any dependency beyond a brief's list. The upstream
prototype used `requests`, `beautifulsoup4`, `lxml`, `openai`, `pytz`, and
`pyyaml` — none of which are in ADR-0003. This ADR resolves escalation #86 by
deciding the minimal set to admit.

## Decision

- **Admit `beautifulsoup4`** (pure-Python, using the stdlib `html.parser`
  backend — no `lxml`) as a dependency of `integrations/food-recommendation`
  only. It is a stable, widely-used HTML parser and the smallest addition that
  makes the scraper maintainable.
- **Do not add** `lxml`, `openai`, `requests`, `pytz`, or `pyyaml`:
  - HTTP uses **`httpx`**, already in the workspace (ADR-0003's implied client).
  - The OpenAI call is a plain `httpx` POST to the Chat Completions endpoint — no
    vendor SDK.
  - Timezones use the stdlib **`zoneinfo`**; dietary preferences ship as bundled
    Python data (JSON-overridable) instead of a YAML file.
- The dependency is confined to the `stanford` provider, which runs **only** in
  the wallboard-worker (NUC) daily job, never on the door critical path, and only
  when `FEATURE_FOOD=true` and `FOOD_PROVIDER=stanford`.

## Consequences

`beautifulsoup4` (+ its `soupsieve` dependency) enters `uv.lock`. The stack stays
otherwise unchanged. Any further scraping/LLM dependency (e.g. `lxml` for
performance, a vendor SDK) requires a superseding ADR. This scope keeps the
novelty budget on the product, per ADR-0003.
