"""The seven worked examples from spec Section 9, as LLM-output fixtures.

Only deviation from the spec text: each object_overlap check carries `entity_category`
(see Phase 1 notes) so the Phase 3 binder can tell wearable from static without guessing.
"""

EXAMPLES: dict[str, dict] = {
    "ex1_ppe_wearable": {
        "interpretation_note": "Anyone in the press area must be wearing a helmet.",
        "requires_zone": True,
        "checks": [
            {"primitive": "zone_containment", "zone": "press_area"},
            {"primitive": "object_overlap", "object": "helmet", "region": "head",
             "negate": True, "entity_category": "wearable_item"},
        ],
        "fallback_question": None,
        "duration_sec": 3,
    },
    "ex2_static_hazard_contact": {
        "interpretation_note": "No one may physically touch the conveyor belt.",
        "requires_zone": False,
        "checks": [
            {"primitive": "object_overlap", "object": "conveyor_belt", "region": "hand",
             "negate": False, "entity_category": "static_object"},
        ],
        "fallback_question": None,
        "duration_sec": 1,
    },
    "ex3_posture_duration": {
        "interpretation_note": "No one may sit or lie on the conveyor belt for over 5 seconds.",
        "requires_zone": True,
        "checks": [
            {"primitive": "zone_containment", "zone": "conveyor_belt"},
            {"primitive": "posture", "value": "horizontal", "sustained_sec": 5},
        ],
        "fallback_question": None,
        "duration_sec": 5,
    },
    "ex4_object_state": {
        "interpretation_note": "The fire exit must never be blocked or closed.",
        "requires_zone": False,
        "checks": [
            {"primitive": "state", "object": "fire_exit", "expected": "unchanged"},
        ],
        "fallback_question": None,
        "duration_sec": 60,
    },
    "ex5_time_window_zone": {
        "interpretation_note": "No one may enter the warehouse between 18:00 and 06:00.",
        "requires_zone": True,
        "checks": [
            {"primitive": "time_window", "start": "18:00", "end": "06:00"},
            {"primitive": "zone_containment", "zone": "warehouse"},
        ],
        "fallback_question": None,
        "duration_sec": 1,
    },
    "ex6_pure_behavioral_fallback": {
        "interpretation_note": "No one may eat in this area; needs behavioral judgment.",
        "requires_zone": True,
        "checks": [],
        "fallback_question": "Is this person eating or drinking?",
        "duration_sec": 3,
    },
    "ex7_mixed_primitive_and_fallback": {
        "interpretation_note": "In the loading dock, no phone in hand while operating the forklift.",
        "requires_zone": True,
        "checks": [
            {"primitive": "zone_containment", "zone": "loading_dock"},
            {"primitive": "object_overlap", "object": "phone", "region": "hand",
             "negate": False, "entity_category": "wearable_item"},
        ],
        "fallback_question": "Is this person actively operating the forklift (not just standing near it)?",
        "duration_sec": 3,
    },
}
