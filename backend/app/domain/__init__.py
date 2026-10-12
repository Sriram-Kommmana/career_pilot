"""Pure domain logic.

Nothing in this package may import SQLAlchemy, FastAPI, an HTTP client, an LLM
client, or read the system clock. Functions here operate on plain dataclasses and
enums and take ``now`` as a parameter when they need it.

That constraint is what makes the safety-critical parts — the application state
machine, approval binding, and the submission gate — testable with no database, no
network, and no running services.
"""
