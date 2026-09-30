-- kind: series
-- For each use case, how many of its harnesses use each technique (the matrix, PRD §3 surface 4).
SELECT u.use_case_id, f.technique_id, count(DISTINCT f.repo) AS harnesses
FROM v_uc_repos u JOIN v_features f USING (repo)
GROUP BY u.use_case_id, f.technique_id
ORDER BY u.use_case_id, f.technique_id
