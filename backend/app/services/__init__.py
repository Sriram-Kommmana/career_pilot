"""Service layer: orchestration between the API and the database.

Services may touch the database. They must not contain the rules that decide whether
something is permitted — those live in ``app.domain`` as pure functions, so they can
be tested without infrastructure and reasoned about in one place.
"""
