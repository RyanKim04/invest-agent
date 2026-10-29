{% test tickers_in_universe(model, column_name) %}

select {{ column_name }}
from {{ model }}
where exists (
    select 1
    from unnest({{ column_name }}) as t(ticker)
    where t.ticker not in (select ticker from {{ source('config', 'universe') }})
)

{% endtest %}
