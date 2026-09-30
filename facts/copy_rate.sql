-- kind: scalar
-- Share of artifacts that are copies of another artifact in the edition.
SELECT 1 - count(DISTINCT cluster_id)::DOUBLE / nullif(count(*), 0) FROM v_membership
