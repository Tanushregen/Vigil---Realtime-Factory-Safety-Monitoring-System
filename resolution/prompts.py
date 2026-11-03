from sqlalchemy.orm import Session
from schemas.requests import RuleInput
from store.database import SafetyPrompt, CameraGroup

_TYPE_TO_SCOPE = {"universal": "global", "group": "room", "individual": "camera"}


def rule_input_from_prompt(db: Session, prompt: SafetyPrompt) -> RuleInput:
    try:
        scope = _TYPE_TO_SCOPE[prompt.prompt_type]
    except KeyError:
        raise ValueError(f"unknown prompt_type {prompt.prompt_type!r} on prompt {prompt.id}")

    if scope == "global":
        value = None
    elif scope == "room":
        group = db.get(CameraGroup, prompt.group_id) if prompt.group_id else None
        if group is None:
            raise ValueError(f"prompt {prompt.id} is type 'group' but has no valid group_id")
        value = group.group_code
    else:
        if not prompt.camera_id:
            raise ValueError(f"prompt {prompt.id} is type 'individual' but has no camera_id")
        value = str(prompt.camera_id)
    return RuleInput(rule_text=prompt.text, scope=scope, scope_value=value)