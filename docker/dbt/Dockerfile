# dbt on Athena, run as an ECS Fargate task (infra/stacks/products_build.py).
# Build context: the repository root (see the root .dockerignore).
FROM python:3.12-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DBT_PROFILES_DIR=/app/dbt \
    DBT_SEND_ANONYMOUS_USAGE_STATS=false

WORKDIR /app

# Versions pinned from uv.lock, so the image runs what CI tested.
COPY docker/dbt/requirements.txt /tmp/requirements.txt
RUN pip install --no-compile -r /tmp/requirements.txt

COPY dbt/ dbt/

# A non-root user; dbt writes target/ and logs/ inside the project.
RUN useradd --create-home dbt && chown -R dbt /app
USER dbt

# The task's environment provides AWS_ACCOUNT_ID and its role's credentials.
# Unit tests use DuckDB fixtures and run in CI, not here.
ENTRYPOINT ["dbt"]
CMD ["build", "--project-dir", "dbt", "--target", "athena", "--exclude", "test_type:unit"]
