"""What the research domain loads, and where."""

from __future__ import annotations

# OpenAlex subfield "Polymers and Plastics" (field: Materials Science).
SUBFIELD_ID = "https://openalex.org/subfields/2507"
SUBFIELD_FILTER = "2507"

OPENALEX_BUCKET = "openalex"
MANIFEST_KEY = "data/parquet/manifest.json"
DELETED_IDS_KEY = "data/parquet/works/deleted_ids.csv.gz"

# Glue tables (see infra/stacks/data_lake.py).
SOURCE_WORKS = "openalex_source.works"
SOURCE_API_WORKS = "openalex_source.api_works"
SOURCE_DELETED_WORKS = "openalex_source.deleted_works"
WORKS = "research.works"

# Prefixes in the research bucket.
ICEBERG_PREFIX = "iceberg/works/"
STAGING_PREFIX = "staging/"
API_LANDING_PREFIX = "landing/openalex_api/"
DELETED_IDS_PREFIX = "reference/openalex_deleted/"

# Before the first snapshot load: every partition is newer than this.
INITIAL_WATERMARK = "2000-01-01"

# The daily feed re-reads works published in this window, so a work that
# OpenAlex indexes a few weeks late still arrives before the next snapshot.
API_WINDOW_DAYS = 30
