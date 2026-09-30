-- kind: scalar
-- The same count with forks and template repos left out (PRD §4 S4).
SELECT count(*) FROM v_repos WHERE NOT coalesce(is_fork, false) AND NOT coalesce(is_template, false)
