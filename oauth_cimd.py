"""Client ID Metadata Document fetcher for The Cauldron's OAuth server (#89).

django-oauth-toolkit maps a CIMD document to exactly one grant type and refuses
any document that lists another one besides ``refresh_token``. Claude's own
document (``https://claude.ai/oauth/mcp-oauth-client-metadata``) also lists
``urn:ietf:params:oauth:grant-type:jwt-bearer``, so the authorize request failed
with ``invalid_request``. We only ever issue authorization codes and refresh
tokens, so grant types we don't support are dropped before DOT validates the
document; nothing else in it changes. Wired in arcano's settings as
``OAUTH2_PROVIDER["CIMD_METADATA_FETCHER"]``.
"""

from oauth2_provider.cimd import SafeMetadataFetcher

SUPPORTED_GRANT_TYPES = ("authorization_code", "refresh_token")


class CauldronMetadataFetcher(SafeMetadataFetcher):
    """The SSRF-hardened default fetcher, minus grant types we never issue."""

    def fetch(self, client_id):
        metadata, max_age = super().fetch(client_id)
        grants = metadata.get("grant_types")
        if isinstance(grants, list):
            metadata = {**metadata, "grant_types": [g for g in grants if g in SUPPORTED_GRANT_TYPES]}
        return metadata, max_age
