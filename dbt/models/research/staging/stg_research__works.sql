-- One row per work, renamed and flagged. Nothing is dropped here; the
-- intermediate layer decides what counts.
with works as (
    select * from {{ source('research', 'works') }}
)

select
    id as work_id,
    doi,
    title,
    publication_date,
    publication_year,
    type as work_type,
    language,
    coalesce(is_retracted, false) as is_retracted,
    coalesce(is_xpac, false) as is_xpac,
    topic_id,
    topic_name,
    topic_score,
    coalesce(is_oa, false) as is_open_access,
    oa_status,
    coalesce(cited_by_count, 0) as cited_by_count,
    fwci,
    sdg_ids,
    {{ array_size('sdg_ids') }} > 0 as has_sdg,
    country_codes,
    institution_ids,
    -- Some works carry dates years ahead (for example dissertations dated by
    -- their embargo end). A date later in the current year is plausible; a
    -- later year is not, and such works are left out of per-year products.
    coalesce(publication_date > {{ as_of_date() }}, false) as is_future_dated,
    coalesce(publication_year > year({{ as_of_date() }}), false) as is_beyond_current_year,
    updated_date,
    source
from works
