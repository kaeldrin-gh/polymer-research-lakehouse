-- Current values only (the latest release), with ISO country codes and the
-- activity groups the products use.
with releases as (
    select * from {{ source('sustainability', 'air_releases') }}
    where valid_to_version is null
),

countries as (
    select * from {{ ref('eea_country_codes') }}
)

select
    r.facility_id,
    r.reporting_year,
    r.pollutant,
    c.country_code,
    r.country_name,
    r.sector_code,
    r.annex_activity,
    r.releases_kg,
    r.releases_kg / 1000.0 as releases_tonnes,
    r.confidentiality_reason,
    coalesce(r.sector_code = '4', false) as is_chemical_industry,
    -- E-PRTR Annex I 4(a)(viii): plastic materials (polymers, synthetic
    -- fibres and cellulose-based fibres).
    coalesce(r.annex_activity = '4(a)(viii)', false) as is_polymer_production,
    r.valid_from_version
from releases as r
left join countries as c on r.country_name = c.country_name
