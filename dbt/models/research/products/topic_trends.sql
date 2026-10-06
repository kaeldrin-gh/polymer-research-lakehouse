with by_topic as (
    select
        topic_id,
        max(topic_name) as topic_name,
        cast(publication_year as bigint) as publication_year,
        cast(count(*) as bigint) as works
    from {{ ref('int_research__countable_works') }}
    where topic_id is not null
    group by topic_id, publication_year
),

by_year as (
    select publication_year, sum(works) as year_works
    from by_topic
    group by publication_year
)

select
    t.topic_id,
    t.topic_name,
    t.publication_year,
    t.works,
    cast(round(cast(t.works as double) / y.year_works, 4) as double) as share_of_year
from by_topic as t
inner join by_year as y on t.publication_year = y.publication_year
