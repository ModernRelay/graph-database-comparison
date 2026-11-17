-- AGE has no property schema. Its docs set up a graph and its labels;
-- each label becomes a PostgreSQL table with an agtype properties column.
LOAD 'age';
SET search_path = ag_catalog, "$user", public;
SELECT create_graph('cmp');
SELECT create_vlabel('cmp', 'Person');
SELECT create_vlabel('cmp', 'Company');
SELECT create_vlabel('cmp', 'Project');
SELECT create_elabel('cmp', 'WorksAt');
SELECT create_elabel('cmp', 'ContributesTo');
