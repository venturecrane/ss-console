"""pytest fixtures for the medchron runner tests. Data and helpers live in
medchron_testkit.py (see its docstring for why they are not here)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import yaml

from medchron_testkit import FIRM_CONFIG, PRICING, job_yaml


@pytest.fixture
def firm_config_path(tmp_path: Path) -> Path:
    p = tmp_path / "firm.yaml"
    p.write_text(yaml.safe_dump(FIRM_CONFIG, sort_keys=False), encoding="utf-8")
    return p


@pytest.fixture
def pricing_path(tmp_path: Path) -> Path:
    p = tmp_path / "pricing.json"
    p.write_text(json.dumps(PRICING), encoding="utf-8")
    return p


@pytest.fixture
def data_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    (root / "example-matter").mkdir(parents=True)
    return root


@pytest.fixture
def job_dir(tmp_path: Path, data_root: Path) -> Path:
    d = tmp_path / "job"
    d.mkdir()
    (d / "job.yaml").write_text(job_yaml(data_root), encoding="utf-8")
    return d


@pytest.fixture
def fake_pipeline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A pipeline directory whose scripts record their argv and env, exit per
    an optional per-script `exit_<name>` file, and write the artifacts the
    driver reads. Nothing paid ever runs."""
    pdir = tmp_path / "pipeline"
    pdir.mkdir()
    script = (
        "import json, os, sys, pathlib\n"
        "name = pathlib.Path(sys.argv[0]).name\n"
        "log = pathlib.Path(os.environ['SMD_MC_DATA']) / 'calls.jsonl'\n"
        "with log.open('a') as fh:\n"
        "    fh.write(json.dumps({'script': name, 'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "        'env': {k: os.environ.get(k) for k in ('SMD_MC_DATA','SMD_SLUG','SMD_UNIT','SMD_INCIDENT_DATE','SMD_BATCH_STAGES','SMD_MODEL_AUDIT')}}) + '\\n')\n"
        "code_file = pathlib.Path(sys.argv[0]).parent / ('exit_' + name)\n"
        "if name == 'extract.py':\n"
        "    d = pathlib.Path(os.environ['SMD_MC_DATA']) / os.environ['SMD_SLUG']\n"
        "    (d / 'extracted.jsonl').write_text(json.dumps({'id': 'f1', 'name': 'f1', 'pages': 7, 'chars': 9000}) + '\\n')\n"
        "if name == 'index_msg.py' and not any(a.startswith('--fold') for a in sys.argv[1:]):\n"
        "    d = pathlib.Path(os.environ['SMD_MC_DATA']) / os.environ['SMD_SLUG']\n"
        "    (d / 'msg_attachments.json').write_text(json.dumps({'attachments': [\n"
        "        {'sha12': 'aaaaaaaaaaaa', 'ext': '.pdf', 'status': 'NEW'},\n"
        "        {'sha12': 'bbbbbbbbbbbb', 'ext': '.rpmsg', 'status': 'NEW'},\n"
        "        {'sha12': 'cccccccccccc', 'ext': '.png', 'status': 'NEW'}]}))\n"
        "if name == 'build_doc.py':\n"
        "    rd = pathlib.Path(os.environ['SMD_MC_DATA']) / os.environ['SMD_SLUG'] / 'runs' / os.environ['SMD_UNIT']\n"
        "    rd.mkdir(parents=True, exist_ok=True)\n"
        "    (rd / 'final-chronology.md').write_text('# Chronology\\n## Medical Chronology\\n\\n## Exhibit List\\n')\n"
        "if code_file.is_file():\n"
        "    sys.exit(int(code_file.read_text().strip()))\n"
        "sys.exit(0)\n"
    )
    names = [
        "download.py",
        "extract.py",
        "index_msg.py",
        "vision_scan.py",
        "billing_extract.py",
        "build_units.py",
        "check_unit_identity.py",
        "map_run.py",
        "repair_truncated.py",
        "assemble.py",
        "merge_code.py",
        "group_providers.py",
        "filter_preincident.py",
        "build_exhibits.py",
        "condense_entries.py",
        "summarize_preincident.py",
        "build_doc.py",
        "classify_nonrecord.py",
        "classify_scanned.py",
        "strip_nonrecord.py",
        "coverage_gate.py",
        "billing_chart.py",
        "billing_docx.py",
        "audit_repair_loop.py",
        "md_to_docx_v4.py",
        "make_manifest.py",
    ]
    for n in names:
        (pdir / n).write_text(script, encoding="utf-8")
    (pdir / "fetch_icd.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setenv("MEDCHRON_PIPELINE_DIR", str(pdir))
    return pdir


os.environ.setdefault("MEDCHRON_TEST", "1")
