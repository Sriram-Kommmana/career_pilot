"""Pydantic request and response contracts.

These are the API's public shape. ORM models are never returned directly, so an
internal column rename is not a breaking API change, and a field cannot leak into a
response merely by existing on a table.
"""
