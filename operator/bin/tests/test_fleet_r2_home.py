"""bin/lib/fleet/r2.py as the one Python home of the laptop/CI R2 names.

The four callers that read R2_* themselves (publish- and retire-output-class-spec,
audit-chain-watch, decommission_inventory) now go through it. Each test pins the
home to what the inline code it replaced did, argument for argument: the boto3
client the two spec scripts built, the credentials audit-chain-watch derived,
and the "vault loaded" test decommission ran.
"""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from bin.lib import decommission_inventory
from bin.lib.fleet import r2

OPERATOR = Path(__file__).resolve().parents[2]
ENV = {
    "R2_ENDPOINT_URL": "https://acct.r2.invalid",
    "R2_ACCESS_KEY_ID": "key-id",
    "R2_SECRET_ACCESS_KEY": "secret",
    "R2_BUCKET_CONFIG": "cfg-bucket",
}


def _old_inline_client_kwargs(environ: dict[str, str]) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """What publish/retire-output-class-spec.py passed to boto3.client before the home existed."""
    return (
        ("s3",),
        {
            "endpoint_url": environ["R2_ENDPOINT_URL"],
            "aws_access_key_id": environ["R2_ACCESS_KEY_ID"],
            "aws_secret_access_key": environ["R2_SECRET_ACCESS_KEY"],
            "region_name": "auto",
        },
    )


class _FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise KeyError(Key)
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str) -> None:
        self.objects[(Bucket, Key)] = Body


@pytest.fixture
def fake_boto3(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    seen: dict[str, Any] = {"calls": [], "s3": _FakeS3()}

    def client(*args: Any, **kwargs: Any) -> _FakeS3:
        seen["calls"].append((args, kwargs))
        return seen["s3"]

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(client=client))
    return seen


def test_the_home_builds_the_client_the_inline_code_built(fake_boto3: dict[str, Any]) -> None:
    r2.boto3_client(ENV)
    assert fake_boto3["calls"] == [_old_inline_client_kwargs(ENV)]


def test_a_missing_name_fails_as_it_did_with_a_key_error(fake_boto3: dict[str, Any]) -> None:
    for name in ("R2_ENDPOINT_URL", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        partial = {k: v for k, v in ENV.items() if k != name}
        with pytest.raises(KeyError, match=name):
            r2.boto3_client(partial)
    with pytest.raises(KeyError, match="R2_BUCKET_CONFIG"):
        r2.required_config_bucket({k: v for k, v in ENV.items() if k != "R2_BUCKET_CONFIG"})
    assert r2.required_config_bucket(ENV) == "cfg-bucket"


def _load_script(name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), OPERATOR / "bin" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_both_spec_scripts_reach_r2_through_the_home(
    fake_boto3: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FALSIFIER: a script that builds its own client with other arguments, or reads another bucket."""
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    body = tmp_path / "voice.md"
    body.write_text("Plain and brief.\n")
    publish = _load_script("publish-output-class-spec")
    assert publish.main(["--slug", "pilot-smokeball", "--class", "demand", "--body", str(body)]) == 0
    key = ("cfg-bucket", "vaults/pilot-smokeball/output-classes.json")
    assert key in fake_boto3["s3"].objects
    retire = _load_script("retire-output-class-spec")
    assert retire.main(["--slug", "pilot-smokeball", "--all"]) == 0
    assert json.loads(fake_boto3["s3"].objects[key])["classes"] == {}
    assert fake_boto3["calls"] == [_old_inline_client_kwargs(ENV)] * 2


def _old_inline_derivation(environ: dict[str, str], token_id: Any) -> tuple[str, str, str]:
    """audit-chain-watch.py r2_credentials() as it was, with os.environ as a parameter."""
    key_id = environ.get("R2_ACCESS_KEY_ID")
    secret = environ.get("R2_SECRET_ACCESS_KEY")
    if not key_id or not secret:
        token = environ.get("CLOUDFLARE_API_TOKEN")
        if not token:
            raise RuntimeError("no R2 credentials and no CLOUDFLARE_API_TOKEN to derive them from")
        key_id = token_id(token)
        secret = hashlib.sha256(token.encode("utf-8")).hexdigest()
    endpoint = environ.get("R2_ENDPOINT_URL")
    if not endpoint:
        account = environ.get("CLOUDFLARE_ACCOUNT_ID")
        if not account:
            raise RuntimeError("R2_ENDPOINT_URL unset and CLOUDFLARE_ACCOUNT_ID not available")
        endpoint = f"https://{account}.r2.cloudflarestorage.com"
    return key_id, secret, endpoint


@pytest.mark.parametrize(
    "environ",
    [
        ENV,
        {"CLOUDFLARE_API_TOKEN": "tok", "CLOUDFLARE_ACCOUNT_ID": "acct"},
        {"R2_ACCESS_KEY_ID": "only-half", "CLOUDFLARE_API_TOKEN": "tok", "R2_ENDPOINT_URL": "https://e.invalid"},
        {"R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s", "CLOUDFLARE_ACCOUNT_ID": "acct"},
    ],
)
def test_the_derivation_is_the_one_audit_chain_watch_made(environ: dict[str, str]) -> None:
    def token_id(token: str) -> str:
        return f"id-of-{token}"

    assert r2.derived_credentials(environ, token_id=token_id) == _old_inline_derivation(environ, token_id)


@pytest.mark.parametrize(
    "environ",
    [{}, {"CLOUDFLARE_API_TOKEN": "tok"}, {"R2_ACCESS_KEY_ID": "k", "R2_SECRET_ACCESS_KEY": "s"}],
)
def test_the_derivation_refuses_where_it_refused(environ: dict[str, str]) -> None:
    def token_id(token: str) -> str:
        return "id"

    with pytest.raises(RuntimeError) as new:
        r2.derived_credentials(environ, token_id=token_id)
    with pytest.raises(RuntimeError) as old:
        _old_inline_derivation(environ, token_id)
    assert str(new.value) == str(old.value)


@pytest.mark.parametrize(
    "environ",
    [{}, {"R2_ACCESS_KEY_ID": ""}, {"CF_ACCOUNT_ID": "a"}, {"CLOUDFLARE_ACCOUNT_ID": "a"}, {"OTHER": "x"}],
)
def test_decommission_reads_the_vault_as_loaded_exactly_when_it_did(environ: dict[str, str]) -> None:
    old = any(k in environ for k in ("R2_ACCESS_KEY_ID", "CF_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID"))
    assert decommission_inventory.infisical_names("acme", environ)["enumerated"] is old
