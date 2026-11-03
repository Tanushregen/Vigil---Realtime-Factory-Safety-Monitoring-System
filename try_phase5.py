from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from schemas.compiled import LLMCompilerOutput, CompiledRule
from schemas.requests import RuleInput
from store.database import Base, Camera, CameraGroup, CameraGroupMember
import store.models  # noqa: F401
from resolution import captures as cap
from resolution import service as svc
from resolution.plan_builder import get_plan
from tests.spec_examples import EXAMPLES

# throwaway database with 3 cameras
engine = create_engine("sqlite://")
Base.metadata.create_all(engine)
db = sessionmaker(bind=engine, autoflush=False)()
press = CameraGroup(group_code="GRP-PRESS", name="Press Area")
db.add_all([press,
            Camera(name="Press Cam 1", zone="Press Area"),
            Camera(name="Press Cam 2"),
            Camera(name="Canteen Cam", zone="Canteen")])
db.flush()
db.add(CameraGroupMember(group_id=press.id, camera_id=2))
db.commit()

# compile one rule (from the spec example) and activate it for the press area
ri = RuleInput(rule_text="No one in the press area without a helmet",
               scope="room", scope_value="press_area")
out = LLMCompilerOutput.model_validate(EXAMPLES["ex1_ppe_wearable"])
rule = CompiledRule.from_llm_output(out, rule_id="r1", rule_input=ri, prompt_version="demo")
svc.activate_rule(db, rule)

print("=== Camera 1 plan (before zone capture) ===")
print(get_plan(db, 1).model_dump_json(indent=2))

# pretend the capture worker finished drawing the zone
svc.complete_capture(db, 1, cap.ZONE, "press_area", {"poly": [[0, 0], [1, 0], [1, 1]]})

plan = get_plan(db, 1)
print("\n=== Camera 1 after capture ===")
print("version:", plan.version, "| rule state:", plan.active_rules[0].state,
      "| zone captured:", plan.zones_needed[0].captured)

print("\n=== Camera 3 (canteen, not in the press area) ===")
print("active rules:", get_plan(db, 3).active_rules)