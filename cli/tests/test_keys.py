import json
import traceback
import types
from pathlib import Path

import pytest

from getaway import keys, paths, prefs
from getaway.keys import AuthError

ENV_VAR = "EXAMPLE_API_KEY"
PREFS_KEY = "example_op_ref"
SERVICE = "example"
OP_REF = "op://Vault/example/credential"


def _resolve() -> str:
    return keys.resolve(ENV_VAR, PREFS_KEY, SERVICE)


def _write_prefs_ref(ref: str) -> None:
    # example_op_ref is not a template key, so set_patch would reject it; write it
    # into an initialized doc directly instead.
    prefs.init()
    doc = json.loads(paths.prefs_path().read_text())
    doc[PREFS_KEY] = ref
    paths.prefs_path().write_text(json.dumps(doc))


def _forbidden(*_args: object, **_kwargs: object) -> object:
    raise AssertionError("neither prefs nor op may be touched")


def _rendered(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


def test_env_key_wins_without_touching_prefs_or_op(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, "env-example-key")
    monkeypatch.setattr(keys.prefs, "load_or_empty", _forbidden)
    monkeypatch.setattr(keys.subprocess, "run", _forbidden)
    assert _resolve() == "env-example-key"


def test_missing_env_and_prefs_names_both_sources(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    with pytest.raises(AuthError) as excinfo:
        _resolve()
    message = str(excinfo.value)
    assert ENV_VAR in message
    assert PREFS_KEY in message


def test_non_op_ref_rejected_without_echoing(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    _write_prefs_ref("Vault/example/credential")
    monkeypatch.setattr(keys.subprocess, "run", _forbidden)
    with pytest.raises(AuthError) as excinfo:
        _resolve()
    assert "Vault/example/credential" not in str(excinfo.value)


def test_op_invoked_with_exact_argv(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    _write_prefs_ref(OP_REF)
    captured: dict[str, object] = {}

    def _fake_run(argv: list[str], **kwargs: object) -> object:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return types.SimpleNamespace(returncode=0, stdout="example_resolved_secret\n", stderr="")

    monkeypatch.setattr(keys.subprocess, "run", _fake_run)
    assert _resolve() == "example_resolved_secret"
    assert captured["argv"] == ["op", "read", OP_REF]
    assert captured["kwargs"] == {"capture_output": True, "text": True, "errors": "replace"}


def test_op_failure_does_not_leak_reference(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    _write_prefs_ref(OP_REF)

    def _fail(*_args: object, **_kwargs: object) -> object:
        return types.SimpleNamespace(returncode=1, stdout="", stderr=f"{OP_REF} not found")

    monkeypatch.setattr(keys.subprocess, "run", _fail)
    with pytest.raises(AuthError) as excinfo:
        _resolve()
    assert OP_REF not in _rendered(excinfo.value)


@pytest.mark.parametrize(
    "bad_key",
    ["example\ufffdsecret", "example\x01secret"],
    ids=["undecodable-replacement", "control-byte"],
)
def test_malformed_op_output_rejected_without_leaking(
    getaway_home: Path, monkeypatch: pytest.MonkeyPatch, bad_key: str
) -> None:
    monkeypatch.delenv(ENV_VAR, raising=False)
    _write_prefs_ref(OP_REF)
    monkeypatch.setattr(
        keys.subprocess,
        "run",
        lambda *_a, **_k: types.SimpleNamespace(returncode=0, stdout=bad_key + "\n", stderr=""),
    )
    with pytest.raises(AuthError) as excinfo:
        _resolve()
    rendered = _rendered(excinfo.value)
    assert bad_key not in rendered
    assert OP_REF not in rendered


@pytest.mark.parametrize("key", ["example_valid_key-123", "!~"], ids=["typical", "ascii-bounds"])
def test_validate_passes_printable_ascii(key: str) -> None:
    assert keys.validate(key, SERVICE) == key


@pytest.mark.parametrize(
    "bad_key",
    ["bad\r\nkey", "bad key", "bad\x00key"],
    ids=["crlf", "whitespace", "non-printable"],
)
def test_validate_rejects_without_leaking(bad_key: str) -> None:
    with pytest.raises(AuthError) as excinfo:
        keys.validate(bad_key, SERVICE)
    rendered = _rendered(excinfo.value)
    assert bad_key not in str(excinfo.value)
    assert bad_key not in rendered
