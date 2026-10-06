"""Find and download releases from the EEA's public WebDAV share."""

from __future__ import annotations

import base64
import shutil
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from sustainability import scope

DAV = "{DAV:}"
PROPFIND_BODY = (
    b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
    b"</d:prop></d:propfind>"
)
USER_AGENT = (
    "polymer-research-lakehouse (https://github.com/kaeldrin-gh/polymer-research-lakehouse)"
)


@dataclass(frozen=True)
class Release:
    version: int
    folder: str
    first_year: int
    last_year: int

    @property
    def zip_url(self) -> str:
        return scope.DAV_ROOT + urllib.parse.quote(f"{self.folder}/{scope.ZIP_NAME}")


def _auth_header() -> str:
    # The share token is the user name; the share has no password.
    return "Basic " + base64.b64encode(f"{scope.SHARE_TOKEN}:".encode()).decode()


def _request(url: str, method: str = "GET", body: bytes | None = None) -> urllib.request.Request:
    headers = {"Authorization": _auth_header(), "User-Agent": USER_AGENT}
    if method == "PROPFIND":
        headers.update({"Depth": "1", "Content-Type": "application/xml"})
    return urllib.request.Request(url, data=body, method=method, headers=headers)


def list_folder_names(xml_text: str | bytes) -> list[str]:
    """Names of the direct children in a WebDAV PROPFIND (Depth 1) answer."""
    names = []
    for response in ET.fromstring(xml_text).iter(f"{DAV}response"):
        href = response.findtext(f"{DAV}href") or ""
        name = urllib.parse.unquote(href.rstrip("/").rsplit("/", 1)[-1])
        if name:
            names.append(name)
    return names


def latest_release(names: list[str]) -> Release | None:
    releases = []
    for name in names:
        match = scope.RELEASE_FOLDER.match(name)
        if match:
            first, last, version, _revision = (int(g) for g in match.groups())
            releases.append(Release(version, name, first, last))
    return max(releases, key=lambda r: r.version, default=None)


def fetch_listing() -> bytes:
    with urllib.request.urlopen(
        _request(scope.DAV_ROOT, "PROPFIND", PROPFIND_BODY), timeout=120
    ) as response:
        return response.read()


def download(url: str, path: str) -> None:
    with urllib.request.urlopen(_request(url), timeout=600) as response, open(path, "wb") as out:
        shutil.copyfileobj(response, out, length=1024 * 1024)
