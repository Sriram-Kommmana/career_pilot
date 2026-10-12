"""Job discovery connectors.

A connector fetches postings from one external source and normalises them into
``app.domain.normalize.NormalizedJob``. It never writes to the database itself —
that is ``app.services.discovery``'s job — and it never does anything a connector
isn't allowed to do: no authentication bypass, no scraping a page that requires
login, no defeating a bot check. See ``docs/SAFETY.md`` §10.

Connectors prefer documented, public APIs over HTML scraping wherever one exists.
Greenhouse is first because its public job-board JSON API
(``https://boards-api.greenhouse.io``) is exactly that: a stable, documented,
unauthenticated endpoint meant for programmatic consumption, not a page to scrape.
"""
