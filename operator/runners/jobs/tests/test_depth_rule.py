"""One depth rule: the writer and the reviewer carry the same text.

The writer used to be told to keep routine visits to two or three sentences
while the reviewer was told to report anything a page records that the entry
leaves out; on two real updates omitted content was 56 and 80 percent of the
must-fix findings. These tests fail if the two ever disagree again.
"""

from __future__ import annotations

import re

import pytest

from jobs.medchron import prompts

# Wording that sets how much an entry carries. Outside ``depth-rule.md`` it is
# a second rule, which is the defect.
OWN_DEPTH = re.compile(
    r"depth mirrors|routine repeat visits|one-sentence subsections|near-full fidelity|two to three",
    re.IGNORECASE,
)


def _cfg(firm_config_path):
    from jobs.medchron import config as config_mod

    return config_mod.load(str(firm_config_path))


@pytest.mark.parametrize("name", prompts.DEPTH_PROMPTS)
def test_the_prompt_carries_the_rule_whole(name, firm_config_path):
    assert prompts.raw(name).count(prompts.DEPTH_TOKEN) == 1
    loaded = prompts.load(name, _cfg(firm_config_path))
    assert prompts.DEPTH_TOKEN not in loaded
    assert prompts.depth_rule() in loaded


@pytest.mark.parametrize("name", prompts.DEPTH_PROMPTS)
def test_the_prompt_has_no_depth_wording_of_its_own(name):
    assert OWN_DEPTH.findall(prompts.raw(name)) == []


def test_the_check_can_fail():
    # The instrument: the line this build removed from the writer prompt.
    old = "Routine repeat visits (serial PT) get two to three one-sentence subsections."
    assert OWN_DEPTH.findall(old)
