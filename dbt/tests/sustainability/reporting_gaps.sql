-- Data-quality signal, not a failure: a country with no facility at all in a
-- year inside its reporting period, usually because its latest years are not
-- in this EEA release yet. The gaps stay gaps (nothing is filled in); this
-- lists them on every build, so a new one is noticed.
{{ config(severity='warn') }}

select country_code, reporting_year
from {{ ref('reporting_coverage') }}
where status = 'missing'
