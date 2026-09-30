#!/usr/bin/env python3
"""
endoflife.ai -> Dynatrace metrics exporter
==========================================
Pushes the endoflife.ai EOL Risk Score catalog into a Dynatrace environment
through the Metrics API v2 ingest endpoint (line protocol), so end-of-life
risk can be charted, alerted on and joined to host data inside Dynatrace.

Metrics emitted (one data point per product per run):
    endoflife.risk_score        0-100 EOL Risk Score (higher = more dangerous)
    endoflife.days_until_eol    days until (negative = since) the product's EOL date
    endoflife.kev_factor_score  the CISA KEV component of the score in isolation (0-20)
    endoflife.days_until_next_eol  days until the product's NEXT cycle end of life (upcoming
                                deadlines; dimensions add version + eol_date)
Dimensions on every line: product, grade, eol_label, kev_active (+ eol_date on
days_until_eol). Sources: https://endoflife.ai/scores.json (scores, the EOL date
that drives them) and https://endoflife.ai/scanner-db.json (every cycle's dates),
both rebuilt daily.

Setup (NFR / sandbox environment):
    setx DT_ENV_URL   "https://abc12345.live.dynatrace.com"   # no trailing slash
    setx DT_API_TOKEN "dt0c01.XXXX..."                         # scope: metrics.ingest
    (Access tokens: Dynatrace > Access Tokens > Generate new token > metrics.ingest)

Usage:
    python dt_push.py --dry-run            # print the line-protocol payload, send nothing
    python dt_push.py --limit 25           # push a small sample (sandbox-friendly)
    python dt_push.py                      # push the full catalog (~540 products x 3 metrics)
    python dt_push.py --products rhel,windows-server,postgresql

Consumption note: one run is ~1,600 data points. Run it on a schedule you
choose (daily matches the source rebuild); nothing here loops or retries
aggressively - a failed request is reported and the script exits nonzero.
"""

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
import urllib.error

SCORES_URL = "https://endoflife.ai/scores.json"
SCANNER_URL = "https://endoflife.ai/scanner-db.json"  # per-cycle EOL dates -> next upcoming EOL per product
USER_AGENT = "endoflife-ai-dynatrace-exporter/1.0"
MAX_BODY_BYTES = 900_000  # API limit is 1 MB per request; stay well under it


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch_scores():
    return fetch_json(SCORES_URL)


def next_eol_by_product(scanner_db, today):
    """slug -> (cycle, eol_iso, days) for the nearest cycle EOL on or after today."""
    out = {}
    products = scanner_db.get("products", scanner_db) if isinstance(scanner_db, dict) else {}
    for slug, rec in products.items():
        best = None
        for c in (rec or {}).get("cycles") or []:
            eol = c.get("eol")
            if not isinstance(eol, str):
                continue
            d = days_until(eol, today)
            if d is None or d < 0:
                continue
            if best is None or d < best[2]:
                best = (str(c.get("c", "")), eol, d)
        if best:
            out[slug] = best
    return out


def esc(v):
    """Quote a dimension value per the line protocol (escape backslash and quote)."""
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def dim_key_ok(k):
    return all(c.islower() or c.isdigit() or c in "-._:" for c in k)


def days_until(eol_iso, today):
    try:
        y, m, d = (int(x) for x in eol_iso.split("-"))
        return (dt.date(y, m, d) - today).days
    except Exception:
        return None


def build_lines(scores, today, products=None, limit=None, next_eol=None):
    lines = []
    n = 0
    next_eol = next_eol or {}
    for slug in sorted(scores):
        if products and slug not in products:
            continue
        p = scores[slug]
        if not isinstance(p, dict) or "score" not in p:
            continue
        grade = p.get("grade", "")
        label = p.get("label", "")
        kev = (p.get("factors") or {}).get("kev", 0) or 0
        base = f'product={esc(slug)},grade={esc(grade)},eol_label={esc(label)},kev_active={esc("true" if kev > 0 else "false")}'
        lines.append(f"endoflife.risk_score,{base} {int(p['score'])}")
        lines.append(f"endoflife.kev_factor_score,{base} {int(kev)}")
        eol = p.get("eolDate")
        if eol:
            du = days_until(eol, today)
            if du is not None:
                lines.append(f"endoflife.days_until_eol,{base},eol_date={esc(eol)} {du}")
        nxt = next_eol.get(slug)
        if nxt:
            cycle, neol, nd = nxt
            lines.append(f"endoflife.days_until_next_eol,{base},version={esc(cycle)},eol_date={esc(neol)} {nd}")
        n += 1
        if limit and n >= limit:
            break
    return lines, n


def chunks(lines):
    buf, size = [], 0
    for ln in lines:
        b = len(ln.encode("utf-8")) + 1
        if buf and size + b > MAX_BODY_BYTES:
            yield buf
            buf, size = [], 0
        buf.append(ln)
        size += b
    if buf:
        yield buf


def post(env_url, token, body):
    req = urllib.request.Request(
        env_url.rstrip("/") + "/api/v2/metrics/ingest",
        data=body.encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Api-Token {token}",
            "Content-Type": "text/plain; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode("utf-8", "replace")[:500]}


def main():
    ap = argparse.ArgumentParser(description="Push endoflife.ai EOL Risk Scores to Dynatrace")
    ap.add_argument("--dry-run", action="store_true", help="print the payload and exit")
    ap.add_argument("--limit", type=int, help="only the first N products (alphabetical)")
    ap.add_argument("--products", help="comma-separated product slugs to include")
    args = ap.parse_args()

    products = set(args.products.split(",")) if args.products else None
    scores = fetch_scores()
    today = dt.date.today()
    try:
        next_eol = next_eol_by_product(fetch_json(SCANNER_URL), today)
    except Exception as e:  # the three core metrics still ship if the scanner feed is unreachable
        print(f"warning: scanner feed unavailable ({e}); skipping days_until_next_eol", file=sys.stderr)
        next_eol = {}
    lines, n = build_lines(scores, today, products, args.limit, next_eol)
    if not lines:
        print("no products matched", file=sys.stderr)
        return 2

    if args.dry_run:
        print("\n".join(lines))
        print(f"\n# {n} products, {len(lines)} data points, dry run - nothing sent", file=sys.stderr)
        return 0

    env_url = os.environ.get("DT_ENV_URL")
    token = os.environ.get("DT_API_TOKEN")
    if not env_url or not token:
        print("DT_ENV_URL and DT_API_TOKEN must be set (see the header of this file)", file=sys.stderr)
        return 2

    ok = invalid = 0
    for body_lines in chunks(lines):
        status, resp = post(env_url, token, "\n".join(body_lines))
        if status != 202:
            print(f"ingest failed: HTTP {status} {resp}", file=sys.stderr)
            return 1
        ok += resp.get("linesOk", 0)
        invalid += resp.get("linesInvalid", 0)
        if resp.get("linesInvalid"):
            print(f"warning: {resp.get('linesInvalid')} invalid lines: {json.dumps(resp.get('error'))[:400]}", file=sys.stderr)
    print(f"pushed {n} products: {ok} data points accepted, {invalid} rejected")
    return 0 if invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
