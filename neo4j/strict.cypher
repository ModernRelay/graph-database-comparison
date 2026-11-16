// Stricter mode: property type constraints (documented as Enterprise Edition only).
CREATE CONSTRAINT person_age_int IF NOT EXISTS FOR (p:Person) REQUIRE p.age IS :: INTEGER;
CREATE CONSTRAINT person_name_str IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS :: STRING;
