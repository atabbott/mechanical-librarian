"""Both kinds of cover selection, read as one table for tests (#2038).

The owner's cover choices and Automatic's are kept apart (ADR-0108): each
kind has its own selection table and its own per-work pointer. A test that
counts, lists or inspects every selection reads these subqueries instead, so
its assertions keep their meaning across both halves. Each selection carries
its kind, as the documents do.
"""

from __future__ import annotations

COVER_SELECTION_COLUMNS = (
    "selection_id,connection_name,library_name,item_id,kind,decision_id,"
    "consideration_id,supersedes_selection_id,occurred_at,document"
)
# Every cover selection with its kind, owner selections first.
ALL_COVER_SELECTIONS = (
    "(SELECT selection_id,connection_name,library_name,item_id,'owner' AS kind,"
    "decision_id,consideration_id,supersedes_selection_id,occurred_at,document "
    "FROM owner_cover_selections UNION ALL SELECT selection_id,connection_name,"
    "library_name,item_id,'automatic',decision_id,consideration_id,"
    "supersedes_selection_id,occurred_at,document FROM automatic_cover_selections)"
)
# Every work's current cover selection: the owner's pointer or Automatic's.
ALL_COVER_POINTERS = (
    "(SELECT connection_name,library_name,item_id,selection_id "
    "FROM owner_cover_pointers UNION ALL SELECT connection_name,library_name,"
    "item_id,selection_id FROM automatic_cover_pointers)"
)
