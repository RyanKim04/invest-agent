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

Neon settings go in `.env.neon` (gitignored), not `.env`, so laptop commands never touch Neon.

1. Create a Postgres 18 project. Use the **direct** connection string, not the pooled one.
2. `uv run --env-file .env.neon invest db migrate`, with `INVEST_MIGRATE_DATABASE_URL` set to the owner (`neondb_owner`) URL.
3. Create the login roles with SQL, as the owner. Roles made in the Neon console get `neon_superuser`.
   Passwords need at least 60 bits of entropy.
   ```sql
   CREATE ROLE app_login LOGIN PASSWORD '...';
   CREATE ROLE dbt_login LOGIN PASSWORD '...';
   GRANT invest_app TO app_login;
   GRANT invest_dbt TO dbt_login;
   ```
   `INVEST_DATABASE_URL` uses `app_login`. `DBT_PG_*` use `dbt_login`.
4. Keep `pgvector/pgvector:<version>-pg<major>` in `docker-compose.yml` and CI equal to Neon's:
   ```sql
   SELECT version(), extversion FROM pg_extension WHERE extname = 'vector';
   ```
