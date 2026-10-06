-- SCD Type 2 invariant: each facility, year and pollutant has at most one
-- current row, and its versions never overlap.
with history as (
    select * from {{ source('sustainability', 'air_releases') }}
),

current_dupes as (
    select facility_id, reporting_year, pollutant, 'two current rows' as problem
    from history
    where valid_to_version is null
    group by facility_id, reporting_year, pollutant
    having count(*) > 1
),

bad_ranges as (
    select facility_id, reporting_year, pollutant, 'empty or reversed range' as problem
    from history
    where valid_to_version is not null and valid_to_version <= valid_from_version
)

select * from current_dupes
union all
select * from bad_ranges
