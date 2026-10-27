# 2026-10-30

## Today's Focus: Prometheus `up` Metric vs Pod Health

## Category: Prometheus / OpenShift / Monitoring

### Background

Prometheus uses the `up` metric to determine whether a target can be successfully scraped. However, `up = 1` does not guarantee application health.

### Observations

| Value | Observation |
|---|---|
| `up = 1` | Metrics scraping succeeds, but the application may still be unhealthy |
| `up = 0` | Scraping fails, such as during a Pod failure or recovery |
| No data (`null`) | Observed after manually deleting a Pod in OCP, when its target disappears from monitoring |

In the current OCP environment:

- **Pod failure / automatic recovery:** `up = 0` was observed when Prometheus could not scrape the target.
- **Manual Pod deletion:** The target disappeared, resulting in no `up` data (`null`). This may prevent an `up == 0` alert from detecting the missing StatefulSet Pod.

### Monitoring Improvement

```promql
# Detect scrape failures
up{job="app"} == 0
```

```promql
# Detect missing metrics for a target
absent(up{job="app"})
```

For StatefulSets, also monitor expected vs available replicas using `kube-state-metrics`. Note that `absent()` detects no matching series for the selector, not necessarily one missing Pod among several.

### Lessons Learned

**`up = 1` indicates successful scraping, not application health. `up = 0` indicates scrape failure, while missing data requires separate detection.**

Combining Prometheus `up` with Kubernetes replica and readiness metrics provides more reliable monitoring.
