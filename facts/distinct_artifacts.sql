-- kind: scalar
-- Distinct artifacts after dedup (exact, normalized and MinHash tiers).
SELECT count(DISTINCT cluster_id) FROM v_membership
