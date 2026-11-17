MATCH (c:Company {slug: $company})<-[:WORKS_AT]-(p:Person)-[:CONTRIBUTES_TO]->(j:Project)
RETURN DISTINCT j.name AS project
ORDER BY project;
