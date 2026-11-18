# careers-watch

Daily check of Dutch ticketing careers pages. `watch.py` runs on GitHub Actions each weekday morning and writes `data/new.json` (only what changed) and `data/current.json` (all current postings). Public job postings only.

## Which companies are watched

The list lives in the "Watched companies" tab of the Job Search Pipeline Google Sheet, published as CSV (URL in `sheet_sources.py`). Each run reads it:

- Add a row (Company, Watch = Ticketing or Tech, Source URL) and the next run reads that company. Its first run lists its current openings once as new (flagged `first_read`) and names it in `baseline_sources`.
- The reader is picked from the Source URL: Greenhouse, Ashby, Recruitee, Homerun, Lever, Workable and Personio feeds (worldwide boards limited to NL and remote Europe), HiBob job-list text, or for any other site the job links on the page, falling back to a page-change alert.
- Companies with a custom reader in `watch.py` / `tech_sources.py` keep it; changing their URL in the tab has no effect.
- Delete a row to stop watching a company.
- If the tab can't be read, the built-in list is used and `errors` in `new.json` says so.

`test-tab.yml` dry-runs both watches after a change to the reader and saves only `data/test-run.json`.
