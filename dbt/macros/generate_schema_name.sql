{#- Use each folder's schema as given (a Glue database on Athena), not
    "<target schema>_<custom schema>". -#}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {{ custom_schema_name if custom_schema_name is not none else target.schema }}
{%- endmacro %}
