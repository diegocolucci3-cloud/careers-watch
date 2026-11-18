#!/usr/bin/env python3
"""Careers watch for Dutch ticketing companies (and, with `python watch.py tech`, tech companies).

Which companies are read is set by the "Watched companies" tab of the Job Search Pipeline sheet
(see sheet_sources.py); the readers below are the custom ones for companies already set up.

Reads every source, compares with the previous run (data/current.json) and writes:
  data/current.json  - every posting currently listed, per source
  data/new.json      - only what changed since the last run (new / closed / errors)
The Claude task reads data/new.json and judges fit. No job filtering happens here,
except Fever, which is limited to Dutch and remote-Europe locations (it has ~560 jobs worldwide).
"""
import datetime
import hashlib
import json
import os
import pathlib
import re
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

DATA = pathlib.Path(__file__).parent / "data"
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

NL_PLACES = [
    "netherlands", "nederland", "amsterdam", "rotterdam", "the hague", "den haag", "utrecht",
    "haarlem", "leiden", "delft", "almere", "amersfoort", "hilversum", "zoetermeer", "dordrecht",
    "hoofddorp", "schiphol", "amstelveen", "eindhoven", "breda", "groningen", "tilburg", "arnhem",
    "nijmegen", "den bosch", "zwolle", "enschede", "maastricht",
]
REMOTE_EU = re.compile(r"remote.*(europe|emea|\beu\b|netherlands)|(europe|emea).*remote", re.I)


# ---------- helpers ----------
def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read().decode("utf-8", "replace")


def item(id_, title, url, location=""):
    return {"id": str(id_), "title": " ".join(str(title).split()), "url": url,
            "location": " ".join(str(location or "").split())}


# ---------- source readers ----------
def recruitee_api(sub):
    data = json.loads(http_get(f"https://{sub}.recruitee.com/api/offers/"))
    out = {}
    for o in data.get("offers", []):
        loc = o.get("location") or ", ".join(x for x in [o.get("city"), o.get("country")] if x)
        if o.get("remote"):
            loc = f"{loc} (remote possible)".strip()
        it = item(o.get("id") or o.get("slug"), o.get("title"), o.get("careers_url"), loc)
        out[it["id"]] = it
    return out


def greenhouse_fever():
    data = json.loads(http_get("https://boards-api.greenhouse.io/v1/boards/feverup/jobs"))
    out = {}
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        low = loc.lower()
        if any(p in low for p in NL_PLACES) or REMOTE_EU.search(loc):
            it = item(j["id"], j.get("title"), j.get("absolute_url"), loc)
            out[it["id"]] = it
    return out


def homerun_sitemap(base):
    xml = http_get(base.rstrip("/") + "/sitemap.xml")
    root = ET.fromstring(xml)
    out = {}
    for loc in root.iter():
        if not loc.tag.endswith("loc") or not loc.text:
            continue
        parts = [p for p in urlparse(loc.text.strip()).path.split("/") if p]
        if not parts:
            continue
        slug = parts[0]
        out[slug] = item(slug, slug.replace("-", " ").title(), f"{base.rstrip('/')}/{slug}")
    return out


def page_links(page, url, href_pattern, exclude=None):
    """Render the page in a real browser and collect job links matching href_pattern."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(4000)  # let JS-built lists finish
    links = page.eval_on_selector_all(
        "a[href]", "els => els.map(e => [e.href, e.innerText])")
    pat, exc = re.compile(href_pattern), re.compile(exclude) if exclude else None
    out = {}
    for href, text in links:
        href = urljoin(url, href).split("#")[0].split("?")[0].rstrip("/")
        if not pat.search(href) or (exc and exc.search(href)):
            continue
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        title = lines[0] if lines else href.rsplit("/", 1)[-1]
        prev = out.get(href)
        if prev and len(prev["title"]) >= len(title):
            continue
        out[href] = item(href, title, href, " | ".join(lines[1:4]))
    return out


def page_text_hash(page, url):
    """For pages with no postings: report a change if the visible text changes."""
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(2000)
    text = " ".join(page.inner_text("body").split())
    h = hashlib.sha256(text.encode()).hexdigest()[:12]
    return {f"page-{h}": item(f"page-{h}", "Careers page content changed - check it", url)}


# ---------- source list ----------
def browser_sources():
    return {
        "Celebratix": lambda p: page_text_hash(p, "https://www.celebratix.io/en/careers"),
        "WeTicket": lambda p: page_text_hash(p, "https://www.weticket.com/jobs"),
        "Bleep": lambda p: page_text_hash(p, "https://www.justbleep.it/jobs"),
        "Fastlane": lambda p: page_links(p, "https://www.fastlane.events/vacatures", r"/vacatures/[^/]+$"),
        "Eventim": lambda p: page_links(p, "https://careers.eventim.nl/openings?jobs-705be1b5%5Btab%5D=NL", r"/o/[^/]+$"),
        "Tixly": lambda p: tixly(p),
    }


def tixly(page):
    url = "https://tixly.careers.hibob.com/jobs"
    found = page_links(page, url, r"hibob\.com/jobs/[^/]+", exclude=r"/jobs$")
    if not found:
        # HiBob job cards are not links: watch the rendered job-list text instead
        text = " ".join(page.inner_text("body").split())
        m = re.search(r"Total:\s*(\d+)", text)
        if not m:
            raise RuntimeError("job list not found on rendered page")
        snippet = text[m.start():m.start() + 1500]
        h = hashlib.sha256(snippet.encode()).hexdigest()[:12]
        it = item(f"list-{h}", f"Job list changed ({m.group(0)} openings) - read snippet", url)
        it["snippet"] = snippet
        return {it["id"]: it}
    return found


API_SOURCES = {
    "Weeztix": lambda: recruitee_api("weeztix"),
    "CM.com": lambda: recruitee_api("cmcom"),
    "Fever": greenhouse_fever,
    "Stager": lambda: homerun_sitemap("https://stager.homerun.co"),
}


# ---------- title pre-filter (tech set only: big employers post dozens of irrelevant roles) ----------
KEEP = re.compile(r"operation|\bops\b|implementation|integration|onboarding|delivery|partner|"
                  r"product|technical account|\btam\b|solution|customer success|marketplace|"
                  r"trust|safety|excellence|analyst|enablement|program|project", re.I)
DROP = re.compile(r"engineer|developer|intern|stage\b|stagiair|werkstudent|working student|"
                  r"designer|scientist|recruit|counsel|legal|accountant|account executive", re.I)


ALWAYS = re.compile(r"(implementation|integration|solutions?|onboarding|partner) (engineer|consultant|specialist|manager)", re.I)


def keep_title(t):
    if ALWAYS.search(t or ""):
        return True
    return bool(KEEP.search(t or "")) and not DROP.search(t or "")


# ---------- main ----------
def main(watch_set="ticketing"):
    global API_SOURCES, browser_sources, DATA
    prefilter = False
    if watch_set == "tech":
        import tech_sources
        API_SOURCES, browser_sources = tech_sources.API_SOURCES, tech_sources.browser_sources
        DATA, prefilter = DATA / "tech", True
    DATA.mkdir(parents=True, exist_ok=True)
    cur_path = DATA / "current.json"
    prev = json.loads(cur_path.read_text()) if cur_path.exists() else {}
    results, errors = {}, {}

    # company list comes from the "Watched companies" tab; built-in list if the tab can't be read
    import sheet_sources
    api_sources, browsers, tab_errors = sheet_sources.apply_tab(watch_set, API_SOURCES, browser_sources())
    errors.update(tab_errors)
    API_SOURCES, browser_sources = api_sources, (lambda: browsers)

    for name, fn in API_SOURCES.items():
        try:
            results[name] = fn()
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"[:300]

    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            ctx = browser.new_context(user_agent=UA, locale="en-GB")
            for name, fn in browser_sources().items():
                page = ctx.new_page()
                try:
                    results[name] = fn(page)
                except Exception as e:
                    errors[name] = f"{type(e).__name__}: {e}"[:300]
                finally:
                    page.close()
            browser.close()
    except Exception as e:
        for name in browser_sources():
            errors.setdefault(name, f"browser failed: {e}"[:300])

    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    current, new, closed, baseline = {}, [], [], []
    for name in list(API_SOURCES) + list(browser_sources()):
        old = prev.get(name, {}).get("items", {})
        if name not in results:          # failed: keep last known list, report nothing as closed
            if name in prev:
                current[name] = prev[name]
            continue
        items = results[name]
        if prefilter and os.environ.get("TECH_DEBUG"):   # test runs: keep the unfiltered list for tuning
            (DATA / "debug").mkdir(exist_ok=True)
            (DATA / "debug" / f"raw_{name}.json").write_text(json.dumps(items, indent=1, ensure_ascii=False))
        if prefilter:
            items = {k: v for k, v in items.items() if k.startswith(("page-", "list-")) or keep_title(v["title"])}
        current[name] = {"items": items, "checked_at": now}
        if name not in prev:
            # first read of a company (e.g. just added in the tab): report its current openings
            # once as new, flagged first_read, so they get screened too
            baseline.append(name)
            new += [dict(v, company=name, first_read=True) for v in items.values()]
            continue
        new += [dict(v, company=name) for k, v in items.items() if k not in old]
        closed += [dict(v, company=name) for k, v in old.items() if k not in items]

    cur_path.write_text(json.dumps(current, indent=1, ensure_ascii=False, sort_keys=True))
    (DATA / "new.json").write_text(json.dumps({
        "run_at": now,
        "new": new,
        "closed": closed,
        "errors": errors,
        "baseline_sources": baseline,
        "counts": {k: len(v.get("items", {})) for k, v in current.items()},
    }, indent=1, ensure_ascii=False))
    print(f"new={len(new)} closed={len(closed)} errors={list(errors)} baseline={baseline}")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "ticketing")
