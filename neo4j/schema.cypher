// Neo4j Community Edition: uniqueness constraints are the schema it offers.
CREATE CONSTRAINT person_slug IF NOT EXISTS FOR (p:Person) REQUIRE p.slug IS UNIQUE;
CREATE CONSTRAINT company_slug IF NOT EXISTS FOR (c:Company) REQUIRE c.slug IS UNIQUE;
CREATE CONSTRAINT project_slug IF NOT EXISTS FOR (j:Project) REQUIRE j.slug IS UNIQUE;
