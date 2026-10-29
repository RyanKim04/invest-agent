# invest-agent

Design: [docs/01-system-overview.md](docs/01-system-overview.md) · Plan: [docs/02-implementation-plan.md](docs/02-implementation-plan.md)

## Laptop

```sh
uv sync
cp .env.example .env          # fill in AIRFLOW_JWT_SECRET and AIRFLOW_ADMIN_PASSWORD
docker compose --profile local up -d --build
uv run --env-file .env invest db migrate
uv run --env-file .env pytest
uv run --env-file .env invest db load-fixtures tests/fixtures/db
uv run --env-file .env dbt build --project-dir dbt --profiles-dir dbt
```

Airflow UI: http://localhost:8080

## Neon

1. Create a project, on Postgres 18 if offered. Use the **direct** connection string, not the pooled one.
2. Run `invest db migrate` with `INVEST_MIGRATE_DATABASE_URL` set to the owner role's URL.
3. In the Neon console, create two login roles, then grant the group roles:
   ```sql
   GRANT invest_app TO <app login>;
   GRANT invest_dbt TO <dbt login>;
   ```
4. Record the versions and pin `pgvector/pgvector:<version>-pg<major>` in `docker-compose.yml` and CI to match:
   ```sql
   SELECT version(), extversion FROM pg_extension WHERE extname = 'vector';
   ```
