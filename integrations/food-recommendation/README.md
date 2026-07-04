# integrations/food-recommendation — daily food pick

Task: T-605 (Gemini). Feature flag: `FEATURE_FOOD`.

The owner has an existing personal food-preference system; its interface is undocumented, so this package defines the boundary and ships a mock first:

```
FoodRecommendationProvider.get_daily_recommendation() -> Recommendation
  Recommendation: {date, title, detail?, provider}
```

- `mock` provider: rotating fixture list, deterministic per date (dev/CI/demo).
- `personal` provider: added later behind this exact interface once the real system's access is defined — the UI and worker must not know the difference.
- One recommendation per day, cached; failure = tile shows yesterday's with staleness marker.
