#!/usr/bin/env python3
"""
Build an openLCA JSON-LD import package that adds "Total EF 3.1 Single Score (Pt)"
-- the EF 3.1 single score as one browsable, contribution-tree-able impact category
-- to a new impact method, "EF 3.1 (Single Score)", derived from the openLCA
methods-package "EF 3.1 Method (adapted)". The original method is never modified.

WHY: openLCA computes the EF 3.1 single score by normalizing and weighting 16
impact categories (Normalization & Weighting tab), but that total isn't itself an
impact category -- so it can't be calculated for a whole database, put in a
contribution tree, or compared like ecoinvent's "Total - UBP" category
(Ecological Scarcity 2021) can. This script builds that missing category.

HOW: the single score is
    single_score = Σ_cat  (Σ_flows amount × CF_cat(flow)) × WF_cat / NF_cat
Re-grouping by flow key (linearity) gives one combined factor per key:
    combined_CF(key) = Σ_cat  CF_cat(key) × WF_cat / NF_cat
    key = (flow, flow property, unit, location)      <- EXACT key, no fallback
summed over the 16 EF 3.1 categories that carry a normalization + weighting
factor in the method's NwSet (the method's other 10 categories are sub-indicators
-- organics/inorganics splits, climate-change fossil/biogenic/land-use, water-use
adapted spatialization -- not part of the single score, so excluded from the sum
but still referenced in the new method for browsing).

Because every source key is preserved exactly (never merged into a generic
fallback), this reproduces openLCA's own result under BOTH calculation modes:
  - regionalization OFF -> openLCA uses each category's generic (null-location)
    factor -> the combined generic factor already sums those.
  - regionalization ON  -> openLCA uses the flow's location-specific factor when
    present -> the combined per-location factor already sums those.

IMPACT DIRECTION matters and is easy to get wrong: each source category has a
direction (INPUT/OUTPUT). "Water use" is direction=INPUT but also carries factors
on emission flows (water returned to the environment) stored as positive --
openLCA SUBTRACTS those from an INPUT category's result (net use = intake -
return). A directionless merged category would add them instead, over-counting.
So every factor is multiplied by +1 if the flow's role (Resource=INPUT,
Emission=OUTPUT) matches its source category's direction, and -1 if not.

VERIFIED (see README): matches openLCA's own single score to ~0.001% on a
non-water-dominated product system, and within ~0.4% on a water-dominated one
(residual traced to a minor difference in how openLCA nets water returns vs. this
script's flat per-flow netting -- see README "Known limitation").

Run:  python3 build.py
"""

from __future__ import annotations
import json
import os
import shutil
import zipfile
from collections import defaultdict

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

# Exported openLCA JSON-LD package that contains the source method (e.g. the
# "openLCA LCIA Methods" package, exported via File > Export > Linked Data).
SOURCE_PACKAGE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "openLCA LCIA Methods 2.8.2 2026-07-01",
)

# Source method: "EF 3.1 Method (adapted)" from the openLCA methods package.
SOURCE_METHOD_ID = "2f995579-06bd-4681-b07c-cee3b1805b0d"

# New objects. UUIDs are fixed so re-running this script and re-importing
# UPDATES the same method/category instead of creating duplicates -- as long as
# you also bump NEW_VERSION, or use openLCA's "Overwrite all existing data sets"
# import option (version-only bumps can silently no-op otherwise).
METHOD_ID = "ac11d414-1aef-47dd-9f08-0c921cec1322"
CATEGORY_ID = "c67102bf-bf7d-4a09-94ad-fa0f2aef0277"
NWSET_ID = "e46b6f4d-f4ad-4a88-a374-883988f37abb"

NEW_METHOD_NAME = "EF 3.1 (Single Score)"
NEW_CATEGORY_NAME = "Total EF 3.1 Single Score (Pt)"
NEW_CATEGORY_REFUNIT = "Pt"
NEW_CATEGORY_MB_CATEGORY = "Single Score"  # folder shown in the openLCA navigator
NEW_VERSION = "01.00.000"

# Output.
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
PACKAGE_DIR = os.path.join(OUT_DIR, "ef31-single-score")
ZIP_PATH = os.path.join(OUT_DIR, "ef31-single-score.zip")

# Drop combined factors whose absolute value is below this (pure numeric noise).
MIN_ABS_CF = 0.0

# ---------------------------------------------------------------------------


def load(*parts: str) -> dict:
    with open(os.path.join(*parts), "r", encoding="utf-8") as fh:
        return json.load(fh)


def flow_identity(factor: dict) -> tuple:
    """Grouping identity excluding location: (flow, flow property, unit)."""
    fp = factor.get("flowProperty") or {}
    un = factor.get("unit") or {}
    return (factor["flow"]["@id"], fp.get("@id"), un.get("@id"))


def loc_id(factor: dict):
    loc = factor.get("location")
    return loc["@id"] if loc else None


def flow_io(factor: dict):
    """INPUT for resource flows, OUTPUT for emission flows, else None."""
    comp = factor["flow"].get("category", "") or ""
    if comp.startswith("Elementary flows/Resource"):
        return "INPUT"
    if comp.startswith("Elementary flows/Emission"):
        return "OUTPUT"
    return None


def directional_sign(factor: dict, cat_direction) -> float:
    """openLCA's impact-direction convention: a factor counts positively when the
    flow's input/output role matches the impact category's direction, and
    negatively when it doesn't. Needed for Water use (direction=INPUT), which
    carries factors on EMISSION flows (water returned to nature) that must be
    subtracted from the water consumed, not added."""
    io = flow_io(factor)
    if io is None or cat_direction is None:
        return 1.0
    return 1.0 if io == cat_direction else -1.0


def _factor(refs: dict, value: float, location) -> dict:
    f = {"value": value, "flow": refs["flow"]}
    if refs.get("unit"):
        f["unit"] = refs["unit"]
    if refs.get("flowProperty"):
        f["flowProperty"] = refs["flowProperty"]
    if location is not None:
        f["location"] = location
    return f


def main() -> None:
    pkg = os.path.abspath(SOURCE_PACKAGE)
    method = load(pkg, "lcia_methods", f"{SOURCE_METHOD_ID}.json")
    print(f"Source method : {method['name']}  ({SOURCE_METHOD_ID})")

    nwsets = method.get("nwSets", [])
    if len(nwsets) != 1:
        print(f"  NOTE: method has {len(nwsets)} N&W set(s); using the first.")
    nwset = nwsets[0]
    print(f"N&W set       : {nwset['name']}  ({nwset['@id']})")

    # --- read NF / WF per scored category -------------------------------
    weights = {}  # cat_id -> WF/NF
    wf_sum = 0.0
    for f in nwset["factors"]:
        cid = f["impactCategory"]["@id"]
        nf, wf = f.get("normalisationFactor"), f.get("weightingFactor")
        if not nf or wf is None:
            print(f"  SKIP (missing NF/WF): {f['impactCategory'].get('name')}")
            continue
        weights[cid] = wf / nf
        wf_sum += wf
    print(f"Scored cats   : {len(weights)}   (Σ weighting factors = {wf_sum:.5f} "
          f"-> {'FRACTIONS' if abs(wf_sum - 1.0) < 0.05 else 'PERCENT? check scaling'})")

    # --- read factors and combine, EXACT KEY, direction-corrected --------
    # combined[(flow_identity, loc_id)] += (WF/NF) * sign * CF, for every source
    # factor. This is a pure linear combination: keys mirror the originals
    # exactly (no cross-category fallback), so the result equals the sum of the
    # 16 originals under whatever aggregation openLCA applies -- regionalized or
    # not -- by linearity.
    combined = defaultdict(float)
    ref_of = {}
    total_factors = 0
    total_dupes = 0
    for cid, w in weights.items():
        cat = load(pkg, "lcia_categories", f"{cid}.json")
        cat_dir = cat.get("direction")
        facs = cat.get("impactFactors", [])
        total_factors += len(facs)
        # Dedupe within the category: some categories (Water use) list the same
        # (flow, property, unit, location) key several times with an IDENTICAL
        # value. openLCA keeps one such factor per key on import, so summing the
        # repeats would over-count. Across categories we DO sum.
        per_cat = {}
        for fa in facs:
            key = (flow_identity(fa), loc_id(fa))
            if key in per_cat:
                total_dupes += 1
            per_cat[key] = directional_sign(fa, cat_dir) * fa["value"]
            if key not in ref_of:
                ref_of[key] = {
                    "flow": fa["flow"], "unit": fa.get("unit"),
                    "flowProperty": fa.get("flowProperty"), "location": fa.get("location"),
                }
        for key, val in per_cat.items():
            combined[key] += w * val
    print(f"Read {total_factors:,} source factors "
          f"({total_dupes:,} intra-category duplicate keys deduped).")

    out_factors = []
    n_gen = n_reg = 0
    for key, val in combined.items():
        if abs(val) <= MIN_ABS_CF:
            continue
        r = ref_of[key]
        out_factors.append(_factor(r, val, r["location"]))
        n_gen += key[1] is None
        n_reg += key[1] is not None
    print(f"New category  : {len(out_factors):,} combined factors "
          f"({n_gen:,} generic + {n_reg:,} regionalized keys).")

    # --- assemble JSON-LD objects ---------------------------------------
    new_category = {
        "@type": "ImpactCategory", "@id": CATEGORY_ID, "name": NEW_CATEGORY_NAME,
        "category": NEW_CATEGORY_MB_CATEGORY,
        "description": (
            "The EF 3.1 single score as one contribution-tree-able impact category. "
            f"combined_CF(key) = Σ over the {len(weights)} normalized+weighted EF 3.1 "
            "categories of CF(key) × weightingFactor / normalisationFactor, key = "
            "(flow, flow property, unit, location). Derived from "
            f"'{method['name']}' / N&W set '{nwset['name']}'. Auto-generated by "
            "build.py -- do not edit by hand."
        ),
        "version": NEW_VERSION,
        "refUnit": NEW_CATEGORY_REFUNIT,
        "impactFactors": out_factors,
    }
    new_cat_ref = {
        "@type": "ImpactCategory", "@id": CATEGORY_ID, "name": NEW_CATEGORY_NAME,
        "category": NEW_CATEGORY_MB_CATEGORY, "refUnit": NEW_CATEGORY_REFUNIT,
    }
    new_method = {
        "@type": "ImpactMethod", "@id": METHOD_ID, "name": NEW_METHOD_NAME,
        "category": NEW_CATEGORY_MB_CATEGORY,
        "description": (
            f"Copy of '{method['name']}' plus '{NEW_CATEGORY_NAME}', a single "
            "category that reproduces the EF 3.1 single score. The original "
            "method is untouched. See the 'openlca-ef31-single-score' GitHub repo."
        ),
        "version": NEW_VERSION,
        "impactCategories": list(method.get("impactCategories", [])) + [new_cat_ref],
        "nwSets": [{"@type": "NwSet", "@id": NWSET_ID, "name": nwset["name"],
                    "factors": nwset["factors"]}],
    }

    # --- write package ---------------------------------------------------
    if os.path.isdir(PACKAGE_DIR):
        shutil.rmtree(PACKAGE_DIR)
    os.makedirs(os.path.join(PACKAGE_DIR, "lcia_categories"))
    os.makedirs(os.path.join(PACKAGE_DIR, "lcia_methods"))
    with open(os.path.join(PACKAGE_DIR, "openlca.json"), "w", encoding="utf-8") as fh:
        json.dump({"schemaVersion": 5}, fh)
    with open(os.path.join(PACKAGE_DIR, "lcia_categories", f"{CATEGORY_ID}.json"),
              "w", encoding="utf-8") as fh:
        json.dump(new_category, fh, ensure_ascii=False)
    with open(os.path.join(PACKAGE_DIR, "lcia_methods", f"{METHOD_ID}.json"),
              "w", encoding="utf-8") as fh:
        json.dump(new_method, fh, ensure_ascii=False)

    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _dirs, files in os.walk(PACKAGE_DIR):
            for name in files:
                full = os.path.join(root, name)
                zf.write(full, os.path.relpath(full, PACKAGE_DIR))

    print("\n===================== SUMMARY =====================")
    print(f"Source method    : {method['name']}  ({SOURCE_METHOD_ID})")
    print(f"N&W set          : {nwset['name']}  ({nwset['@id']})")
    print(f"Categories used  : {len(weights)} scored (of "
          f"{len(method.get('impactCategories', []))} in the method)")
    print(f"Unique flow keys : {len(combined):,}")
    print(f"Combined factors : {len(out_factors):,}")
    print(f"New method       : {NEW_METHOD_NAME}  ({METHOD_ID})")
    print(f"New category     : {NEW_CATEGORY_NAME}  ({CATEGORY_ID})")
    print(f"Import zip       : {ZIP_PATH}")
    print("Import in openLCA: File > Import > Linked Data (JSON-LD), pick the zip.")
    print("==================================================")


if __name__ == "__main__":
    main()
