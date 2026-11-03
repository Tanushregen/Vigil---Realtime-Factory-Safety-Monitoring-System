import json

import pytest

from llm.parser import ParseError, parse_llm_output
from tests.spec_examples import EXAMPLES

GOOD = json.dumps(EXAMPLES["ex1_ppe_wearable"])


def test_plain_json():
    assert parse_llm_output(GOOD).requires_zone is True


def test_fenced_json():
    assert parse_llm_output(f"```json\n{GOOD}\n```").checks[0].zone == "press_area"


def test_chatter_around_json_is_tolerated():
    assert parse_llm_output(f"Sure! Here you go:\n{GOOD}\nHope that helps.").duration_sec == 3


@pytest.mark.parametrize("text,msg", [
    ("I cannot help with that", "no JSON object"),
    ('{"requires_zone": true,', "invalid JSON"),
    ("[1, 2, 3]", "no JSON object"),
])
def test_unparseable_replies(text, msg):
    with pytest.raises(ParseError, match=msg):
        parse_llm_output(text)


def test_schema_failure_message_names_the_field():
    bad = dict(EXAMPLES["ex1_ppe_wearable"], requires_zone=False)
    with pytest.raises(ParseError, match="schema validation failed.*requires_zone"):
        parse_llm_output(json.dumps(bad))


def test_llm_chosen_tool_is_rejected():
    bad = json.loads(GOOD)
    bad["checks"][1]["tool"] = "sam"
    with pytest.raises(ParseError):
        parse_llm_output(json.dumps(bad))


def test_duplicate_checks_are_dropped():
    d = json.loads(GOOD)
    d["checks"].append(d["checks"][0])
    assert len(parse_llm_output(json.dumps(d)).checks) == 2


def test_duration_is_raised_to_cover_posture_hold():
    d = json.loads(json.dumps(EXAMPLES["ex3_posture_duration"]))
    d["duration_sec"] = 2
    assert parse_llm_output(json.dumps(d)).duration_sec == 5
