"""
Things kept in sync by hand, checked so they can't drift apart: the copy of
web/src/types.ts in CONTRACT.md, the pump flow estimate in hardware.py and
web/src/flow.ts, and the flush constants in flush.py and web/src/flow.ts.
"""

import re
from pathlib import Path

import pytest

import flush
import hardware

ROOT = Path(__file__).resolve().parent.parent


def test_contract_has_types_ts_verbatim():
    contract = (ROOT / "CONTRACT.md").read_text(encoding="utf-8")
    m = re.search(r"^## src/types\.ts \(verbatim\)\n```ts\n(.*?)^```", contract, re.S | re.M)
    assert m, "CONTRACT.md has no '## src/types.ts (verbatim)' ```ts block"
    types_ts = (ROOT / "web" / "src" / "types.ts").read_text(encoding="utf-8")
    assert m.group(1).strip() == types_ts.strip(), (
        "CONTRACT.md's copy of src/types.ts differs from web/src/types.ts: paste the file in again")


def test_flow_estimate_matches_flow_ts():
    flow_ts = (ROOT / "web" / "src" / "flow.ts").read_text(encoding="utf-8")
    m = re.search(r"mlPerS:\s*([\d.]+)", flow_ts)
    assert m, "web/src/flow.ts has no mlPerS"
    assert float(m.group(1)) == hardware.FLOW_ML_PER_S, (
        "hardware.FLOW_ML_PER_S and flow.mlPerS in web/src/flow.ts must be the same")


@pytest.mark.parametrize("name", ["TUBE_INNER_DIAMETER_MM", "FLUSH_EXCESS_ML", "CLEAN_ROUNDS", "MAX_TUBE_LENGTH_MM"])
def test_flush_constants_match_flow_ts(name):
    flow_ts = (ROOT / "web" / "src" / "flow.ts").read_text(encoding="utf-8")
    m = re.search(rf"export const {name} = ([\d.]+);", flow_ts)
    assert m, f"web/src/flow.ts has no {name}"
    assert float(m.group(1)) == getattr(flush, name), f"flush.{name} and {name} in web/src/flow.ts must be the same"
