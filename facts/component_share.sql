-- kind: series
-- For each harness component, how many harnesses have it and what share of all harnesses that is.
SELECT kind, count(DISTINCT repo) AS harnesses,
       count(DISTINCT repo)::DOUBLE / (SELECT count(*) FROM v_repos) AS share
FROM v_artifacts
GROUP BY kind
ORDER BY kind
