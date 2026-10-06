-- The shared data product: per country and year, polymer research output next
-- to the polymer and chemical industry's releases to air. It joins the two
-- domains' products only, never their raw tables. Years and countries come
-- from the EEA release (the narrower coverage).
with emissions as (
    select * from {{ ref('chemical_sector_by_country_year') }}
),

research as (
    select * from {{ ref('works_by_country_year') }}
)

select
    e.country_code,
    e.reporting_year as year,
    coalesce(r.works, 0) as polymer_works,
    r.sdg_tagged_share,
    r.open_access_share,
    e.polymer_facilities,
    e.polymer_co2_tonnes,
    e.chemical_facilities,
    e.chemical_co2_tonnes
from emissions as e
left join research as r
    on e.country_code = r.country_code
    and e.reporting_year = r.publication_year
