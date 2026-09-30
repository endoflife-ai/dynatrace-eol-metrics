# endoflife.ai integration for use with Dynatrace®

Brings endoflife.ai's per-product EOL Risk Scores into a Dynatrace environment as
metrics, so end-of-life exposure can be charted, alerted on and correlated with
the hosts and services Dynatrace already monitors.

Status: **v1.0** — metrics exporter, dashboard document and alert recipe.
Runs against any Dynatrace SaaS environment with a `metrics.ingest` token.
Not yet listed on the Dynatrace Hub. Questions: partners@endoflife.ai.

Data: [endoflife.ai](https://endoflife.ai) — end-of-life dates for 500+
products, with the [EOL Risk Score](https://endoflife.ai/risk-score) (0–100)
on every version. Every date names its source: for the products endoflife.ai
verifies directly it is the vendor's own lifecycle page, for the rest it is the
open endoflife.date dataset, and the API says which.

## What it sends

| Metric | Type | Meaning |
| --- | --- | --- |
| `endoflife.risk_score` | gauge, 0–100 | EOL Risk Score; higher means more dangerous to keep running |
| `endoflife.days_until_eol` | gauge, days | Days until the product's end-of-life date (negative once it has passed) |
| `endoflife.kev_factor_score` | gauge, 0–20 | The CISA Known Exploited Vulnerabilities component of the score, in isolation |
| `endoflife.days_until_next_eol` | gauge, days | Days until the product's next upcoming cycle end of life (adds `version` and `eol_date` dimensions); the "approaching" signal |

Dimensions on every data point: `product` (endoflife.ai slug), `grade` (A–F),
`eol_label`, `kev_active` (`true`/`false`); `days_until_eol` also carries `eol_date`.

Source: `https://endoflife.ai/scores.json`, rebuilt daily from vendor-verified
lifecycle data. The exporter consumes the same public feed any customer can.

## Setup

1. In Dynatrace: **Access Tokens → Generate new token**, scope `metrics.ingest`.
2. Set two environment variables (Windows shown; use `export` elsewhere):

   ```
   setx DT_ENV_URL   "https://<environment-id>.live.dynatrace.com"
   setx DT_API_TOKEN "dt0c01...."
   ```

3. Preview the payload without sending anything:

   ```
   python dt_push.py --dry-run
   ```

4. Push a small sample first, then the full catalog:

   ```
   python dt_push.py --limit 25
   python dt_push.py
   ```

Metrics appear under **Metrics** (search `endoflife.`) within a minute or two.
Schedule the full push daily to track the source rebuild.

## Consumption

One full run is about 2,000 data points (500+ products, four metric keys). The
script sends once, reports the API's `linesOk` / `linesInvalid` counts, and exits;
it never loops or retries on its own.

## Dashboard

`assets/dashboards/eol-risk-overview.dashboard.json` is a Dynatrace dashboard
JSON in the Dashboards app's own upload/download format (version 21; the bare
content object, not the CLI's `{name,type,content}` wrapper - the UI upload
ignores the wrapper and imports an empty dashboard) with eleven tiles built on the
three metrics: products tracked, average score, past-EOL count, KEV-exposed
count, products by grade, a honeycomb of every product's score, the 25
highest-risk products, everything reaching EOL in the next 90 days, the
past-EOL-and-exploited intersection, and the score trend.

To install it: **Dashboards → New dashboard ▾ → Upload** and pick the file; the
dashboard takes its name from the filename, so rename the copy you upload if you
want a friendlier title. The
queries use `timeseries ... | arrayAvg(...)` so they read correctly over any
dashboard timeframe that contains at least one push.

## Alerting

A custom alert (Settings → Analyze and alert → Alerts → Custom alerts) named
"[endoflife.ai] Product at Critical EOL Risk": static threshold on
`timeseries score = max(endoflife.risk_score), by:{product}`, alert when the
metric is above 80 (grade F, Critical risk on the endoflife.ai scale), event name
"endoflife.ai: {product} EOL Risk Score is Critical (above 80)". Create it
once in Settings; a settings-object export will follow in a later release.

## Files

- `dt_push.py` — the exporter (standard library only, Python 3.9+)
- `assets/dashboards/eol-risk-overview.dashboard.json` — the dashboard document
- `README.md` — this file

## Changelog

- **v1.0.0 (2026-09-29)** — alert recipe threshold corrected to above 80
  (grade F, Critical risk); the earlier text said 60, which is the top of grade C
  on the endoflife.ai scale. Dashboard default timeframe of seven days so the
  daily write is always in view. Data-source wording made exact. No change to
  the metrics or the exporter's behaviour.

- **v0.1.0 (2026-09-05)** — first public release: exporter with four metric
  keys (about 1,900 data points per run across 540+ products, verified 0
  rejected on ingest), the 11-tile EOL Risk Overview dashboard, the
  critical-risk alert recipe. Previously developed inside the
  [endoflife-site](https://github.com/endoflife-ai/endoflife-site) repository.

## Trademarks

Dynatrace and any accompanying logos are either registered trademarks or
trademarks of Dynatrace LLC or its subsidiaries in the United States or in
other countries. endoflife.ai is not affiliated with or endorsed by Dynatrace LLC.

## License

MIT — see [LICENSE](LICENSE). The lifecycle data itself comes from
endoflife.ai's public feeds; underlying community dates from
[endoflife.date](https://endoflife.date) (MIT).
