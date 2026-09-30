-- kind: series
-- Harnesses using each tier-1 technique, and their share of all harnesses.
SELECT technique_id, count(DISTINCT repo) AS harnesses,
       count(DISTINCT repo)::DOUBLE / (SELECT count(*) FROM v_repos) AS share
FROM v_features
GROUP BY technique_id
ORDER BY technique_id
