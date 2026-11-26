-- An earlier build's derived partition beside this build's ledger (ADR-0108).
-- Applied to a store of this build, it leaves what an upgrade finds: a
-- derived table under another name, a derived table with another column, an
-- index this build does not make, a derived trigger on a ledger table, and a
-- ledger record that names that earlier derived partition. Synthetic: no row
-- of any library is in it.
ALTER TABLE library_visit RENAME TO library_visit_previous;
ALTER TABLE browse_fact_identity ADD COLUMN previous_note TEXT;
CREATE INDEX review_cases_previous_order ON review_cases(review_case_id);
CREATE TRIGGER human_decisions_previous_attention AFTER INSERT ON human_decisions
BEGIN DELETE FROM library_visit_previous WHERE singleton=0; END;
UPDATE ledger_meta SET derived_version=1,
derived_schema_digest='0000000000000000000000000000000000000000000000000000000000000000';
