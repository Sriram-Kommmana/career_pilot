"""Browser-assisted application automation (Phase 5).

Pure decision logic (``policy.py``, ``mapping.py``) is kept separate from
anything that touches a real browser (``session.py``, ``adapters/``), the same
layering discipline as ``app.domain`` versus ``app.services`` elsewhere in this
project: no database, no Playwright import, no clock in the pure modules.
"""

from __future__ import annotations
