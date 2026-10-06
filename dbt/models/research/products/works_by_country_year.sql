with counts as (
    select
        country_code,
        cast(publication_year as bigint) as publication_year,
        cast(count(*) as bigint) as works,
        cast(sum(case when is_open_access then 1 else 0 end) as bigint) as open_access_works,
        cast(sum(case when has_sdg then 1 else 0 end) as bigint) as sdg_tagged_works,
        cast(sum(cited_by_count) as bigint) as citations,
        cast(avg(fwci) as double) as mean_fwci
    from {{ ref('int_research__work_countries') }}
    group by country_code, publication_year
)

select
    country_code,
    publication_year,
    works,
    open_access_works,
    cast(round(cast(open_access_works as double) / works, 4) as double) as open_access_share,
    sdg_tagged_works,
    cast(round(cast(sdg_tagged_works as double) / works, 4) as double) as sdg_tagged_share,
    citations,
    cast(round(mean_fwci, 4) as double) as mean_fwci
from counts
