---
name: diagnose-metric-shift
description: Investigate a shift in an operations metric using controlled SQL queries, comparable time windows, segment counts, and evidence-backed explanations. Use when a user asks why a metric changed.
metadata:
  version: "1.0"
---

# Diagnose a metric shift

1. Read the metric definition, date range, timezone, inclusion and exclusion rules.
2. Calculate the metric for the target and comparable baseline windows.
3. Check sample counts, nulls, outliers, and source freshness.
4. Segment by route, zone, hour and other plausible dimensions; keep denominators visible.
5. Test whether the aggregate shift is due to within-segment changes or composition.
6. Report observations separately from hypotheses. Include executed SQL or trace references.
7. State remaining uncertainty and a useful follow-up measurement.
