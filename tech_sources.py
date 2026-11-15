"""Tech-company source set for the careers watch (run: python watch.py tech).

Feeds are used where a public job API exists; other sites are rendered in a browser.
Results go to data/tech/. Titles are pre-filtered in watch.py (keep_title).
Feeds with worldwide jobs are limited to Dutch and remote-Europe locations.
"""
import json
import pathlib
import re
from urllib.parse import urljoin

from watch import http_get, item, recruitee_api, page_text_hash, NL_PLACES, REMOTE_EU

DEBUG = pathlib.Path(__file__).parent / "data" / "tech" / "debug"


def in_nl(loc):
    low = (loc or "").lower()
    return any(p in low for p in NL_PLACES) or bool(REMOTE_EU.search(loc or ""))


# ---------- feeds ----------
def greenhouse(board):
    data = json.loads(http_get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs"))
    out = {}
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        if in_nl(loc):
            it = item(j["id"], j.get("title"), j.get("absolute_url"), loc)
            out[it["id"]] = it
    return out


def ashby(board):
    data = json.loads(http_get(f"https://api.ashbyhq.com/posting-api/job-board/{board}"))
    out = {}
    for j in data.get("jobs", []):
        locs = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        loc = " / ".join(l for l in locs if l)
        if j.get("isRemote") or j.get("workplaceType") == "Remote":
            loc += " (remote)"
        if in_nl(loc) or (("remote" in loc.lower()) and re.search(r"europe|emea", loc, re.I)):
            it = item(j["id"], j.get("title"), j.get("jobUrl"), loc)
            out[it["id"]] = it
    return out


def booking():
    out = {}
    for page in range(1, 30):
        data = json.loads(http_get(f"https://jobs.booking.com/api/jobs?location=Amsterdam&page={page}"))
        jobs = data.get("jobs", [])
        if not jobs:
            break
        for j in jobs:
            d = j.get("data", j)
            slug = d.get("slug") or d.get("req_id")
            loc = d.get("full_location") or d.get("location_name") or d.get("city") or ""
            it = item(slug, d.get("title"), f"https://jobs.booking.com/booking/jobs/{slug}", loc)
            out[it["id"]] = it
    return out


def netflix():
    out, start = {}, 0
    while start < 500:
        data = json.loads(http_get(
            "https://explore.jobs.netflix.net/api/apply/v2/jobs?domain=netflix.com"
            f"&location=Amsterdam%2C%20NH%2C%20Netherlands&num=50&start={start}"))
        pos = data.get("positions", [])
        if not pos:
            break
        for p in pos:
            it = item(p["id"], p.get("name"), p.get("canonicalPositionUrl"), p.get("location", ""))
            out[it["id"]] = it
        start += len(pos)
    return out


API_SOURCES = {
    "Adyen": lambda: greenhouse("adyen"),
    "Bol": lambda: greenhouse("bolcom"),
    "Miro": lambda: greenhouse("realtimeboardglobal"),
    "Mollie": lambda: ashby("mollie"),
    "Tebi": lambda: ashby("tebi"),
    "Channable": lambda: recruitee_api("channable"),
    "Booking": booking,
    "Netflix": netflix,
}


# ---------- browser pages ----------
JOBLIKE = re.compile(
    r"greenhouse\.io/.+/jobs/\d+|lever\.co/[^/]+/[0-9a-f-]{36}|ashbyhq\.com/[^/]+/[0-9a-f-]{36}"
    r"|recruitee\.com/o/|teamtailor\.com/jobs/\d+|workable\.com/.+/j/|personio\.(de|com)/job/\d+"
    r"|smartrecruiters\.com/[^/]+/\d+|homerun\.co/[^/]+$|/careers/list/\d+"
    r"|/jobs?/[^/]+|/vacanc(y|ies)/[^/]+|/positions?/[^/]+|/openings?/[^/]+|/vacatures?/[^/]+", re.I)


def render(page, url, load_more=True):
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:
        pass
    page.wait_for_timeout(4000)
    for txt in ["Accept all", "Accept", "Allow all", "Agree", "I agree"]:   # cookie banners
        try:
            page.get_by_role("button", name=txt, exact=True).first.click(timeout=1500)
            break
        except Exception:
            pass
    if load_more:
        for _ in range(10):
            try:
                page.get_by_role("button", name=re.compile(r"load more|show more|more jobs", re.I)).first.click(timeout=2000)
                page.wait_for_timeout(2500)
            except Exception:
                break


def jobs_on_page(page, name, url, pattern=JOBLIKE, exclude=None, min_expected=1):
    render(page, url)
    links = page.eval_on_selector_all("a[href]", "els => els.map(e => [e.href, e.innerText])")
    exc = re.compile(exclude) if exclude else None
    base = url.split("?")[0].rstrip("/")
    out = {}
    for href, text in links:
        href = urljoin(url, href).split("#")[0].split("?")[0].rstrip("/")
        if href == base or not pattern.search(href) or (exc and exc.search(href)):
            continue
        lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
        title = lines[0] if lines else href.rsplit("/", 1)[-1].replace("-", " ")
        prev = out.get(href)
        if prev and len(prev["title"]) >= len(title):
            continue
        out[href] = item(href, title, href, " | ".join(lines[1:4]))
    if len(out) < min_expected:
        # save what the page offered so the reader can be fixed after one run
        DEBUG.mkdir(parents=True, exist_ok=True)
        sample = [[h, " ".join((t or "").split())[:80]] for h, t in links][:150]
        (DEBUG / f"{name}.json").write_text(json.dumps({"url": url, "final_url": page.url, "links": sample}, indent=1, ensure_ascii=False))
        if not out:
            raise RuntimeError(f"no job links found ({len(links)} links on page) - see data/tech/debug/{name}.json")
    return out


def browser_sources():
    return {
        "Zenchef": lambda p: page_text_hash(p, "https://www.zenchef.com/join-us"),
        "Bolt": lambda p: jobs_on_page(p, "Bolt", "https://bolt.eu/en/careers/positions/?location%5B0%5D=Netherlands",
                                       re.compile(r"/careers/positions/[0-9a-f-]{36}$")),
        "Vinted": lambda p: jobs_on_page(p, "Vinted", "https://careers.vinted.com/jobs?page=1&city%5B0%5D=Amsterdam&q=", min_expected=5),
        "Catawiki": lambda p: jobs_on_page(p, "Catawiki", "https://catawiki.careers/vacancies?o=0&n=100&of=47810&of=47823&f=92", min_expected=3),
        "Uber": lambda p: jobs_on_page(p, "Uber", "https://jobs.uber.com/en/jobs/?location=Amsterdam&radius=100", min_expected=5),
        "Just Eat Takeaway": lambda p: jobs_on_page(p, "JET", "https://careers.justeattakeaway.com/global/en/the-netherlands", min_expected=5),
        "Tiqets": lambda p: jobs_on_page(p, "Tiqets", "https://www.tiqets.com/careers/", min_expected=3),
        "Backbase": lambda p: jobs_on_page(p, "Backbase", "https://www.backbase.com/careers/jobs", min_expected=5),
        "Lightspeed": lambda p: jobs_on_page(p, "Lightspeed", "https://www.lightspeedhq.com/careers/openings/", min_expected=5),
        "Bird": lambda p: jobs_on_page(p, "Bird", "https://bird.com/careers", min_expected=2),
        "bunq": lambda p: jobs_on_page(p, "bunq", "https://www.bunq.com/careers", min_expected=3),
        "Picnic": lambda p: jobs_on_page(p, "Picnic", "https://jobs.picnic.app/en/vacancies", min_expected=5),
        "Otrium": lambda p: jobs_on_page(p, "Otrium", "https://careers.otrium.com/", min_expected=2),
        "Swapfiets": lambda p: jobs_on_page(p, "Swapfiets", "https://swapfiets.com/careers", min_expected=2),
        "Sendcloud": lambda p: jobs_on_page(p, "Sendcloud", "https://www.sendcloud.com/careers/", min_expected=2),
    }
