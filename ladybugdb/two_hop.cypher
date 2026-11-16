MATCH (c:Company {slug: 'acme'})<-[:WorksAt]-(p:Person)-[:ContributesTo]->(j:Project)
RETURN DISTINCT j.name AS project
ORDER BY project;
