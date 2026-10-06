{#- SQL that differs between DuckDB (CI) and Athena (Trino). -#}

{% macro array_size(column) -%}
    {{ return(adapter.dispatch('array_size')(column)) }}
{%- endmacro %}

{% macro default__array_size(column) -%}
    coalesce(cardinality({{ column }}), 0)
{%- endmacro %}

{% macro duckdb__array_size(column) -%}
    coalesce(len({{ column }}), 0)
{%- endmacro %}

{#- One output row per array element; rows with an empty array drop out. -#}
{% macro cross_join_unnest(column, table_alias, element) -%}
    cross join unnest({{ column }}) as {{ table_alias }}({{ element }})
{%- endmacro %}

{% macro as_of_date() -%}
    {%- set as_of = var('as_of_date') -%}
    {{ "date '" ~ as_of ~ "'" if as_of else "current_date" }}
{%- endmacro %}
