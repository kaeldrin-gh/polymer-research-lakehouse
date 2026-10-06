"""What the sustainability domain loads, and where."""

from __future__ import annotations

import re

# Every EEA release sits in one public Nextcloud share, readable over WebDAV
# with the share token as user name and no password (see docs/design.md).
SHARE_TOKEN = "sptXqwkQr5g7Bp5"
DAV_ROOT = f"https://sdi.eea.europa.eu/datashare/public.php/dav/files/{SHARE_TOKEN}/"
# Tabular releases of the industrial reporting database; the newest is the
# highest version.
RELEASE_FOLDER = re.compile(r"^eea_t_ied-eprtr_p_(\d{4})-(\d{4})_v(\d{2,3})_r(\d{2})$")
ZIP_NAME = "User friendly .csv files.zip"
AIR_RELEASES_MEMBER = "F1_4_Air_Releases_Facilities.csv"

# Prefixes in the sustainability bucket.
LANDING_PREFIX = "landing/eea/"
ICEBERG_PREFIX = "iceberg/air_releases/"

AIR_RELEASES = "sustainability.air_releases"

# Before the first load every release is newer.
INITIAL_VERSION = 0
