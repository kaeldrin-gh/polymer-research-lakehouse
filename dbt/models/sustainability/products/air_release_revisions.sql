-- Values a later EEA release revised or withdrew: the history the SCD Type 2
-- table keeps. Empty until a second release has been loaded.
with history as (
    select * from {{ source('sustainability', 'air_releases') }}
),

closed as (
    select * from history where valid_to_version is not null
)

select
    c.facility_id,
    cast(c.reporting_year as bigint) as reporting_year,
    c.pollutant,
    cast(c.valid_from_version as bigint) as from_version,
    cast(c.valid_to_version as bigint) as revised_in_version,
    c.releases_kg as previous_releases_kg,
    n.releases_kg as revised_releases_kg,
    case when n.facility_id is null then 'withdrawn' else 'revised' end as change_type
from closed as c
left join history as n
    on c.facility_id = n.facility_id
    and c.reporting_year = n.reporting_year
    and c.pollutant = n.pollutant
    and n.valid_from_version = c.valid_to_version
