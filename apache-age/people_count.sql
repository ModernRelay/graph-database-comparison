LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT * FROM cypher('cmp', $$
  MATCH (p:Person)
  RETURN count(p) AS people
$$) AS (people agtype);
