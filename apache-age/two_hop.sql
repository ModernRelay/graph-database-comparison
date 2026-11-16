LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT * FROM cypher('cmp', $$
  MATCH (c:Company {slug: 'acme'})<-[:WorksAt]-(p:Person)-[:ContributesTo]->(j:Project)
  RETURN DISTINCT j.name AS project
  ORDER BY project
$$) AS (project agtype);
