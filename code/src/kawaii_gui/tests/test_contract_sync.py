"""
Things kept in sync by hand, checked so they can't drift apart: the copy of
web/src/types.ts in CONTRACT.md, and the pump flow estimate in hardware.py and
web/src/flow.ts.
"""

import re
from pathlib import Path

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
