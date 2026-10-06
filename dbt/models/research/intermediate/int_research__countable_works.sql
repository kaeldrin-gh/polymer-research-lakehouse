-- The works every research product counts: not retracted, with a plausible
-- publication year.
select
    work_id,
    publication_year,
    topic_id,
    topic_name,
    is_open_access,
    has_sdg,
    cited_by_count,
    fwci,
    country_codes
from {{ ref('stg_research__works') }}
where not is_retracted
  and not is_beyond_current_year
  and publication_year is not null
