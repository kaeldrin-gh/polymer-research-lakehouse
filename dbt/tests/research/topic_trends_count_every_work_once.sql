-- Topic counts must add up to the countable works of each year (works without
-- a topic excluded), so no work is lost or counted twice.
with expected as (
    select cast(publication_year as bigint) as publication_year, count(*) as works
    from {{ ref('int_research__countable_works') }}
    where topic_id is not null
    group by publication_year
),

actual as (
    select publication_year, sum(works) as works
    from {{ ref('topic_trends') }}
    group by publication_year
)

select e.publication_year, e.works as expected, a.works as actual
from expected as e
full outer join actual as a on e.publication_year = a.publication_year
where e.works is null or a.works is null or e.works <> a.works
