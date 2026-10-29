FROM apache/airflow:3.3.2-python3.12

COPY --from=ghcr.io/astral-sh/uv:0.12.2 /uv /usr/local/bin/uv

USER root
RUN mkdir -p /opt/invest && chown airflow:0 /opt/invest
USER airflow

# The app gets its own virtualenv so its dependencies (dbt, psycopg, later torch) can't
# clash with Airflow's. Airflow tasks call /opt/invest/.venv/bin/invest.
WORKDIR /opt/invest
ENV UV_PROJECT_ENVIRONMENT=/opt/invest/.venv \
    UV_PYTHON=/usr/local/bin/python \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    INVEST_CONFIG_DIR=/opt/invest/config

COPY --chown=airflow:0 pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY --chown=airflow:0 src ./src
COPY --chown=airflow:0 config ./config
COPY --chown=airflow:0 dbt ./dbt
RUN uv sync --frozen --no-dev --no-editable

WORKDIR /opt/airflow
