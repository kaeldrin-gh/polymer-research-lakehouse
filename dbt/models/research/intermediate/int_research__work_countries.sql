-- One row per countable work and institution country. A work with authors in
-- two countries counts once for each.
select
    w.work_id,
    c.country_code,
    w.publication_year,
    w.is_open_access,
    w.has_sdg,
    w.cited_by_count,
    w.fwci
from {{ ref('int_research__countable_works') }} as w
{{ cross_join_unnest('w.country_codes', 'c', 'country_code') }}
