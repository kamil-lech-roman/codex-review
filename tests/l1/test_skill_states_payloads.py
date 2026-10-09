"""Every field `record-apply` validates must be described in SKILL.md's apply step.

The skill showed the apply payload as `"results": [ … ]` and never named the field
each result carries. A session that wrote `"status": "applied"` was refused with
`bad-result-value`, "unrecognised result" — a message that names neither the key nor
the accepted values — and had to read `apply.py` to recover (quant-factory,
2026-10-09).

A validated format that is never stated is a contract the host cannot satisfy.
"""
from pathlib import Path

from codex_review import apply

SKILL = Path(__file__).resolve().parents[2] / "SKILL.md"


def _apply_step():
    text = SKILL.read_text()
    start = text.index("### 4. Apply")
    return text[start:text.index("### 5.", start)]


def should_name_the_result_field_and_every_value_record_apply_accepts():
    """
    given the apply step of SKILL.md
    when it is read
    then it names the `result` key and every value in apply.RESULTS
    """
    step = _apply_step()

    assert '"result"' in step, step
    for value in apply.RESULTS:
        assert f"`{value}`" in step, value


def should_name_the_fields_a_denied_result_requires():
    """
    given the apply step of SKILL.md
    when it is read
    then it names the fields record-apply requires on a denied result
    """
    step = _apply_step()

    assert "denial_kind" in step
    assert "asserted_by" in step
