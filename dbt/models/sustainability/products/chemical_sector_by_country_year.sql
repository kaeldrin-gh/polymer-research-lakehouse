-- Chemical industry releases to air per country and reporting year, with the
-- polymer production plants (E-PRTR 4(a)(viii)) as their own columns.
with chemical as (
    select * from {{ ref('stg_sustainability__air_releases') }}
    where is_chemical_industry
)

select
    country_code,
    cast(reporting_year as bigint) as reporting_year,
    cast(count(distinct facility_id) as bigint) as chemical_facilities,
    cast(
        count(distinct case when is_polymer_production then facility_id end) as bigint
    ) as polymer_facilities,
    cast(
        sum(case when pollutant = 'Carbon dioxide (CO2)' then releases_tonnes end) as double
    ) as chemical_co2_tonnes,
    cast(
        sum(
            case when is_polymer_production and pollutant = 'Carbon dioxide (CO2)'
                then releases_tonnes end
        ) as double
    ) as polymer_co2_tonnes,
    cast(
        sum(
            case when is_polymer_production
                and pollutant = 'Non-methane volatile organic compounds (NMVOC)'
                then releases_tonnes end
        ) as double
    ) as polymer_nmvoc_tonnes,
    cast(count(case when releases_kg is null then 1 end) as bigint) as confidential_values
from chemical
group by country_code, reporting_year
