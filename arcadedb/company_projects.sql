MATCH {type: Company, as: c, where: (slug = :company)}.in('WorksAt'){as: p}.out('ContributesTo'){as: j}
RETURN DISTINCT j.name AS project
ORDER BY project
