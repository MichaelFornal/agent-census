-- kind: series
-- Harnesses with at least one artifact in each use case (parents include their subtypes).
SELECT use_case_id, count(DISTINCT repo) AS harnesses
FROM v_uc_repos
GROUP BY use_case_id
ORDER BY use_case_id
