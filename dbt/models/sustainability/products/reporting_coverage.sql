-- Which countries are in which reporting years of the current EEA release,
-- across all sectors: one row per country and year, including the years a
-- country has no facility at all. A gap is flagged, never filled.
with releases as (
    select * from {{ ref('stg_sustainability__air_releases') }}
    where country_code is not null
),

reported as (
    select
        country_code,
        reporting_year,
        count(distinct facility_id) as facilities
    from releases
    group by country_code, reporting_year
),

years as (
    select distinct reporting_year from releases
),

countries as (
    select country_code, min(reporting_year) as first_year
    from reported
    group by country_code
),

exits as (
    select * from {{ ref('eea_reporting_exits') }}
)

select
    c.country_code,
    cast(y.reporting_year as bigint) as reporting_year,
    cast(coalesce(r.facilities, 0) as bigint) as facilities,
    case
        when r.facilities is not null then 'reported'
        when y.reporting_year < c.first_year then 'not yet reporting'
        when y.reporting_year > e.last_reporting_year then 'left'
        else 'missing'
    end as status
from countries as c
cross join years as y
left join reported as r
    on c.country_code = r.country_code and y.reporting_year = r.reporting_year
left join exits as e on c.country_code = e.country_code
