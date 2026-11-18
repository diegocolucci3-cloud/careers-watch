"""Company list from the "Watched companies" tab of the Job Search Pipeline sheet.

The tab is published as CSV (columns Company, Watch, Source, ...). Each run:
  - a company in the tab that already has a custom reader in watch.py / tech_sources.py keeps it;
  - a new company gets a reader picked from its Source URL (job-board feed if recognised,
    otherwise generic job links, otherwise a page-change alert);
  - a company removed from the tab is no longer read.
If the tab can't be read (or has no rows for this watch), the built-in list is used and an error is reported.
"""
import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from watch import http_get, item, recruitee_api, homerun_sitemap, page_text_hash
import tech_sources as ts

TAB_CSV = ("https://docs.google.com/spreadsheets/d/e/2PACX-1vQob3E1dhIRNDLdMVgj3bV3crjaHM4-N21EOEKn_jIh0gYIGL__81uxxYudMqh7Tx2l6o2q43A6NKoV"
           "/pub?gid=3&single=true&output=csv")
TAB_ERROR_KEY = "Watched companies tab"

# tab name -> name used in the code and in data/*.json (keeps history when the tab label differs)
ALIASES = {"eventim / paylogic": "Eventim"}


def read_tab(text=None):
    """Return [(company, watch, source_url)] from the published tab."""
    text = text if text is not None else http_get(TAB_CSV)
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        r = {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
        name, watch, src = r.get("company", ""), r.get("watch", "").lower(), r.get("source", "")
        if name and watch:
            rows.append((ALIASES.get(name.lower(), name), watch, src))
    return rows


# ---------- extra job-board feeds ----------
def lever(board, eu=False):
    host = "api.eu.lever.co" if eu else "api.lever.co"
    out = {}
    for j in json.loads(http_get(f"https://{host}/v0/postings/{board}?mode=json")):
        cat = j.get("categories") or {}
        loc = " / ".join([cat.get("location") or ""] + list(cat.get("allLocations") or []))
        if (j.get("workplaceType") or "").lower() == "remote":
            loc += " (remote)"
        if ts.in_nl(loc):
            it = item(j["id"], j.get("text"), j.get("hostedUrl"), loc)
            out[it["id"]] = it
    return out


def workable(account):
    data = json.loads(http_get(f"https://apply.workable.com/api/v1/widget/accounts/{account}"))
    out = {}
    for j in data.get("jobs", []):
        locs = [", ".join(x for x in [l.get("city"), l.get("country")] if x) for l in j.get("locations") or []]
        loc = " / ".join(locs) or ", ".join(x for x in [j.get("city"), j.get("country")] if x)
        if j.get("telecommuting"):
            loc += " (remote)"
        if ts.in_nl(loc):
            it = item(j.get("shortcode") or j.get("url"), j.get("title"), j.get("url") or j.get("application_url"), loc)
            out[it["id"]] = it
    return out


def personio(host):
    root = ET.fromstring(http_get(f"https://{host}/xml"))
    out = {}
    for p in root.iter("position"):
        pid, title = p.findtext("id"), p.findtext("name")
        offices = [p.findtext("office") or ""] + [o.text or "" for o in p.iter("additionalOffice")]
        loc = " / ".join(o for o in offices if o)
        if ts.in_nl(loc):
            it = item(pid, title, f"https://{host}/job/{pid}", loc)
            out[it["id"]] = it
    return out


def generic_page(page, name, url):
    """Unknown careers site: collect job links; if none, fall back to a page-change alert."""
    try:
        return ts.jobs_on_page(page, name, url, min_expected=1)
    except RuntimeError:
        return page_text_hash(page, url)


def reader_for(name, url):
    """Pick a reader from the Source URL. Returns ("api", fn) or ("browser", fn)."""
    u = urlparse(url if "://" in url else "https://" + url)
    host, parts = u.netloc.lower(), [p for p in u.path.split("/") if p]
    full = (u.scheme or "https") + "://" + u.netloc + u.path
    if "greenhouse.io" in host:
        if "boards" in parts and parts.index("boards") + 1 < len(parts):
            board = parts[parts.index("boards") + 1]       # boards-api.greenhouse.io/v1/boards/<board>/jobs
        else:
            board = parts[0] if parts else ""               # (job-)boards.greenhouse.io/<board>
        if not board:
            raise ValueError(f"Greenhouse URL without a board name: {url}")
        return "api", lambda b=board: ts.greenhouse(b)
    if host == "api.ashbyhq.com" and len(parts) > 2:
        return "api", lambda b=parts[2]: ts.ashby(b)
    if host == "jobs.ashbyhq.com" and parts:
        return "api", lambda b=parts[0]: ts.ashby(b)
    if host.endswith(".recruitee.com"):
        return "api", lambda s=host.split(".")[0]: recruitee_api(s)
    if host.endswith(".homerun.co"):
        return "api", lambda b="https://" + host: homerun_sitemap(b)
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return "api", lambda b=parts[0], eu=".eu." in host: lever(b, eu)
    if host == "apply.workable.com" and parts:
        return "api", lambda a=parts[0]: workable(a)
    if re.search(r"\.jobs\.personio\.(de|com)$", host):
        return "api", lambda h=host: personio(h)
    if host.endswith(".careers.hibob.com"):
        return "browser", lambda p, x=full: ts.hibob(p, x)
    return "browser", lambda p, n=name, x=url: generic_page(p, n, x)


def apply_tab(watch_set, api_sources, browser_sources):
    """Return (api_sources, browser_sources, errors) for this watch set, driven by the tab."""
    try:
        rows = [r for r in read_tab() if r[1] == watch_set]
    except Exception as e:
        return api_sources, browser_sources, {TAB_ERROR_KEY: f"tab unreadable, used built-in list: {type(e).__name__}: {e}"[:300]}
    if not rows:
        return api_sources, browser_sources, {TAB_ERROR_KEY: f"tab has no '{watch_set}' rows, used built-in list"}
    errors = {}
    built_api = {k.lower(): (k, v) for k, v in api_sources.items()}
    built_browser = {k.lower(): (k, v) for k, v in browser_sources.items()}
    api, browser = {}, {}
    for name, _, url in rows:
        key = name.lower()
        if key in built_api:
            api[built_api[key][0]] = built_api[key][1]
        elif key in built_browser:
            browser[built_browser[key][0]] = built_browser[key][1]
        elif url:
            try:
                kind, fn = reader_for(name, url)
            except Exception as e:
                errors[name] = f"Source URL not usable: {e}"[:300]
                continue
            (api if kind == "api" else browser)[name] = fn
        else:
            errors[name] = "added in the tab without a Source URL: not read"
    return api, browser, errors
