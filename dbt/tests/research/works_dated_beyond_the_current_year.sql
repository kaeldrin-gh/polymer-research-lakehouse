-- Data-quality signal, not a failure: works OpenAlex dates in a later year
-- than today (for example dissertations dated by their embargo end). They are
-- left out of per-year products; this lists them so the count stays visible.
{{ config(severity='warn') }}

select work_id, publication_date, work_type
from {{ ref('stg_research__works') }}
where is_beyond_current_year
