"""Who may call itself Claude on The Cauldron's OAuth consent screen (#89).

Client names are self-declared: anyone can register a client (DCR) or host a
metadata document (CIMD) named "Claude" and phish users into approving it.
The name — or anything imitating it — is therefore reserved for Anthropic's own
clients, which identify themselves with a Client ID Metadata Document served
from an Anthropic host: for CIMD the ``client_id`` *is* that document's URL, so
its host can't be faked. Everyone else gets refused at registration and, as a
backstop, on the consent screen.
"""

import re
import unicodedata
from urllib.parse import urlsplit

# Hosts that serve Anthropic's client metadata documents (and their subdomains).
TRUSTED_CLAUDE_HOSTS = ("claude.ai", "claude.com", "anthropic.com")

# Brand names nobody but Anthropic's clients may use.
RESERVED = ("claude", "anthropic")

# Look-alikes squeezed out before comparing: "C1aude", "Cl4ude", "Сlaude" (Cyrillic С)…
_LOOKALIKES = str.maketrans({
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "|": "l", "!": "l",
    "а": "a", "е": "e", "о": "o", "с": "c", "і": "i", "ӏ": "l", "ԁ": "d", "υ": "u", "ս": "u",
    "α": "a", "ε": "e", "ο": "o", "ϲ": "c", "ι": "i",
})


def _skeleton(name):
    """Lower-case letters only, with compatibility forms (full-width, ligatures,
    accents) and common look-alike characters folded to plain ASCII."""
    text = unicodedata.normalize("NFKD", name or "").casefold().translate(_LOOKALIKES)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z]", "", text)


def claims_claude(name):
    """True when a client name says (or imitates) Claude / Anthropic."""
    skeleton = _skeleton(name)
    return any(word in skeleton for word in RESERVED)


def is_anthropic_client(client_id):
    """True for a CIMD client whose metadata document lives on an Anthropic host."""
    if not (client_id or "").lower().startswith("https://"):
        return False
    host = (urlsplit(client_id).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in TRUSTED_CLAUDE_HOSTS)


def name_allowed(client_id, name):
    return not claims_claude(name) or is_anthropic_client(client_id)
