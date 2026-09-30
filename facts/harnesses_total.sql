-- kind: scalar
-- Harnesses in the edition: discovered repos that S2 harvested. A repo that was deleted before harvest
-- (repos.error = 'not_found') or that GitHub could not serve in five attempts ('unreachable:...') is not counted.
SELECT count(*) FROM v_repos
