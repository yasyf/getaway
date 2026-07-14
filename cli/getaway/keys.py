import os
import re
import subprocess

from getaway import prefs

_OP_PREFIX = "op://"
_KEY_RE = re.compile(r"[!-~]+")


class AuthError(Exception):
    """No usable API credential could be resolved."""


def _op_read(ref: str) -> str:
    result = subprocess.run(["op", "read", ref], capture_output=True, text=True)
    if result.returncode != 0:
        raise AuthError("failed to resolve the API key from the configured 1Password reference")
    return result.stdout.strip()


def _validate_key(key: str, service: str) -> str:
    if not _KEY_RE.fullmatch(key):
        raise AuthError(f"resolved {service} API key must be printable ASCII without whitespace")
    return key


def resolve(env_var: str, prefs_key: str, service: str) -> str:
    """Resolve the API key for ``service`` from ``env_var`` or a preferences 1Password ref."""
    key = os.environ.get(env_var)
    if key:
        return _validate_key(key, service)
    # load_or_empty tolerates a missing file / absent ref (both -> None, env
    # fallback) and rejects a pre-v2 shape loudly.
    ref = prefs.load_or_empty().get(prefs_key)
    if not ref:
        raise AuthError(f"no {service} API key: set {env_var} or a preferences {prefs_key}")
    if not ref.startswith(_OP_PREFIX):
        raise AuthError(f"preferences {prefs_key} must be a 1Password op:// reference")
    return _validate_key(_op_read(ref), service)
