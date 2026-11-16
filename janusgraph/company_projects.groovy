g.V().has('Company', 'slug', company).in('WorksAt').out('ContributesTo').values('name').dedup().order()
