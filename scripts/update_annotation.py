"""Remove FP entities from annotation that are no longer extracted by the new pipeline."""
import json
import sqlite3
import shutil
from pathlib import Path

ANN = Path("annotations/806cda6bd3e8ae4f0dac0729af001beedb59b9e6adf67248b0311f9f36360ce0.json")

with open(ANN) as f:
    ann = json.load(f)

conn = sqlite3.connect("traces.db")
db_set = {
    (r[0], r[1].lower())
    for r in conn.execute(
        "SELECT e.entity_type, e.canonical_name FROM entities e"
    ).fetchall()
}

before = len(ann["expected_entities"])
kept = [
    e for e in ann["expected_entities"]
    if (e["entity_type"], e["canonical_name"].lower()) in db_set
]
removed = before - len(kept)

shutil.copy(ANN, ANN.with_suffix(".json.bak"))
ann["expected_entities"] = kept

with open(ANN, "w") as f:
    json.dump(ann, f, indent=2)

print(f"Removed {removed} FP entities (backup at {ANN}.bak)")
print(f"Annotation now has {len(kept)} expected entities")
