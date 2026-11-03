from app import storage
from app.db import SessionLocal, Camera

client = storage.get_client()
bucket = storage._bucket()

# Every top-level folder in the bucket, including ones that only have hidden versions
folders = set()
paginator = client.get_paginator("list_object_versions")
for page in paginator.paginate(Bucket=bucket, Delimiter="/"):
    for p in page.get("CommonPrefixes", []):
        folders.add(p["Prefix"].rstrip("/"))

# Folders used by cameras that still exist
db = SessionLocal()
in_use = set()
for c in db.query(Camera).all():
    if c.storage_folder:
        in_use.add(c.storage_folder.lower())
    in_use.add(storage.clean_folder_name(c.name or "").lower())
db.close()

orphans = sorted(f for f in folders if f.lower() not in in_use)

print("In use (kept):", sorted(f for f in folders if f.lower() in in_use))
print("To delete   :", orphans)

if orphans and input("Type DELETE to permanently remove these folders: ") == "DELETE":
    for f in orphans:
        n = storage.delete_prefix(f"{f}/")
        print(f"{f}/ -> {n} object version(s) deleted")
else:
    print("Nothing deleted.")