"""No firm, client, staff or vendor name in the Case File Manager's product code.

This repository is public. The Case File Manager was ported from a client's
own file-review toolset, whose contract and prompts name the firm, its staff,
its clients and its vendors. Those are firm INPUTS, staged on the seat from the
private engagements repository (``casefile/firm.py``); none may reach the
generic stage code. This test refuses any word in ``medchron/casefile/**`` (and
the lane module) whose sha256 is on a denylist computed from the names in that
toolset's ``.md`` files. Only the hashes are here, so the test itself publishes
nothing. Add a hash, never a name.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "medchron"
FILES = [*sorted((PKG / "casefile").rglob("*.py")), PKG / "casefile_lane.py"]
DENY = frozenset(
    {
        "af7c880c6178c7da849c55217816baf4cdf37d8f6fd5c32e693ab272c7196ef9",
        "deae46ac01968516adb228c7289e1f69a3ffe798989814200a31f7e1a6e1c83a",
        "ceaa9749b0471a8bcd157a4b16ad02dbc319a0422d4900245d3ae368bb72e75e",
        "f855c34a93006564538c25729584fe56bdbb6198540956c95813733900bfa132",
        "127278762a70fea92147a0c32c8baec2bd8f0cf8b6597613ba5c17c6263e886f",
        "c157a0b0d40f9d9506c72fae584069d1692b816da16cd76c648d5761c588057a",
        "4ea7ea4917057a1fcbb3bffdb673602d9b961ff14b239cc7a8d96933b8a18b51",
        "af6036a34fea1e725e1d92ac62f9b7b83689bdf666267f3847707c9b17204495",
        "21d3368e5269d5f2d53cc5a376693eb0041283bb337045ec01378eb6055bdc4e",
        "b9ee491a6030fd7d24e2efbf5de7029ddeb291eabe8f520843be43156c6e278e",
        "2b0de6a1cbbf4e4b1aabcb55f1d84f002f3efa172e76d467419c2c586b375a42",
        "dcddf1ef3dad105ca96ea9d0c9116469981e62982f3d35995d87fd9db9a3071c",
        "00fdbf7ec72f48e682cb0bb5bcd26b00fb92a83810d791b4530b1cb4b2b4126c",
        "888b1c0d1c234a2cb70c908ad0b50f738206fbb5fc012aab0ce1ad2ebdd48ec3",
        "ff52604fbbf6f8ec04f21efd2a47da45be119c3de87714bbbb8bdb2ff652fc78",
    }
)


def _hits(text: str) -> list[str]:
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", text)}
    return sorted(w for w in words if hashlib.sha256(w.encode()).hexdigest() in DENY)


def test_the_package_is_found() -> None:
    assert len(FILES) >= 10


def test_no_denylisted_word_is_in_the_case_file_code() -> None:
    found = {str(p.relative_to(PKG)): _hits(p.read_text(encoding="utf-8")) for p in FILES}
    assert {k: v for k, v in found.items() if v} == {}


def test_the_check_can_fail() -> None:
    planted = "".join(chr(c) for c in (105, 114, 109, 97))  # a denylisted word, spelled so this file carries none
    assert _hits(f"x = '{planted.title()} wrote this'") == [planted]
