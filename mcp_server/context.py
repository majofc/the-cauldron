"""Who is calling, from which assistant, in which language and timezone."""

from dataclasses import dataclass
from types import SimpleNamespace


@dataclass
class McpContext:
    user: object
    application: object
    scopes: frozenset
    connection: object  # McpConnection
    request: object  # the Django request (absolute URLs)
    language: str  # "es" / "en"
    tz: object  # the user's ZoneInfo

    @property
    def client(self):
        return self.connection.client if self.connection else "other"

    @property
    def is_es(self):
        return self.language.startswith("es")

    @property
    def serializer_request(self):
        """What the nutrition serializers need from a request: just ``user``."""
        return SimpleNamespace(user=self.user)

    def absolute(self, path):
        return self.request.build_absolute_uri(path)
