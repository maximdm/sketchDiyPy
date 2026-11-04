"""Multi-session document store — foundation for review.md §6.2 H2.

sketchDiy is local-first and single-document today: one ``Document`` is the
source of truth (``server.doc``). Every document access is rooted here so that
per-session documents — keyed by an ``X-Session-Id`` header / cookie — can be
added later **without editing every endpoint**.

Right now there is exactly one default document and ``doc`` aliases it, so the
original behaviour (and the existing tests that reassign ``server.doc``) is
preserved. To activate sessions:

  1. Add a FastAPI dependency that reads ``X-Session-Id`` and calls
     ``store.get(session_id)``.
  2. Have each endpoint receive ``doc = Depends(current_doc)`` instead of using
     the module global, and update the test harness to ``store.set_default(...)``
     instead of ``server.doc = ...``.
"""

from __future__ import annotations

from core.document import Document


class DocStore:
    """Holds documents keyed by a session id; creates them on first access."""

    def __init__(self) -> None:
        self._docs: dict[str, Document] = {}
        self.default_id = "default"

    def get(self, session_id: str | None = None) -> Document:
        key = session_id or self.default_id
        doc = self._docs.get(key)
        if doc is None:
            doc = Document()
            self._docs[key] = doc
        return doc

    def set_default(self, doc: Document) -> None:
        """Pin the default document (startup / test harness)."""
        self._docs[self.default_id] = doc


store = DocStore()
# The live default document. `server.py` aliases this as its module-global `doc`
# so the rest of the app is unchanged during the transition.
doc = store.get()
