"""Rewrite `knowledge.lock` after a change: `uv run python scripts/knowledge_lock.py`.

Refuses when a document's content changed but its `version` did not go up. Afterwards rebuild the
index: `uv run python manage.py rebuild_knowledge_index`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from apps.catalog import knowledge  # noqa: E402

documents = knowledge.load_documents()
stale = knowledge.unversioned_changes(documents, knowledge.read_lock())
if stale:
    sys.exit(f"Raise the version of: {', '.join(stale)}")
knowledge.LOCK.write_text(knowledge.lock_text(documents))
print(f"Locked {len(documents)} documents.")
