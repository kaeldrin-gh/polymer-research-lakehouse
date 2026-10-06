-- The works every research product counts: not retracted, not in OpenAlex's
-- xpac expansion set, with a plausible publication year. Leaving xpac works out
-- matches OpenAlex's own default, so the counts agree with openalex.org.
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
  and not is_xpac
  and not is_beyond_current_year
  and publication_year is not null
