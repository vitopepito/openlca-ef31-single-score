#!/usr/bin/env python3
"""
Offline self-check: prove the generated "Total EF 3.1 Single Score (Pt)" category
reproduces openLCA's single-score math by LINEARITY, for arbitrary inventories.

openLCA's single score is
    single = Σ_cat  WF_cat/NF_cat × Σ_i amount_i × CF_cat(key_i)
where key_i = (flow, flow property, unit, location) of inventory item i, and
CF_cat(key) is that category's factor for that exact key (0 if absent, direction
sign applied). The generated category stores, per key,
    combined_CF(key) = Σ_cat  WF_cat/NF_cat × sign_cat(key) × CF_cat(key)
so  Σ_i amount_i × combined_CF(key_i) == single, for ANY inventory -- this models
openLCA's exact-key application (no cross-category fallback) plus the impact-
direction sign correction.

This validates the arithmetic and that every source factor landed in the right
combined key with the right sign. It does NOT replace testing against a real
process in openLCA, but a mismatch here means the generated file is wrong.

Run:  python3 verify.py
"""

from __future__ import annotations
import os
import random
from collections import defaultdict

import build as B

PKG = os.path.abspath(B.SOURCE_PACKAGE)


def load_source():
    """cf[cat_id][key] = signed value ; weights[cat_id] = WF/NF."""
    method = B.load(PKG, "lcia_methods", f"{B.SOURCE_METHOD_ID}.json")
    weights, cf = {}, defaultdict(dict)
    for f in method["nwSets"][0]["factors"]:
        cid = f["impactCategory"]["@id"]
        weights[cid] = f["weightingFactor"] / f["normalisationFactor"]
    for cid in weights:
        cat = B.load(PKG, "lcia_categories", f"{cid}.json")
        cat_dir = cat.get("direction")
        for fa in cat["impactFactors"]:
            key = (B.flow_identity(fa), B.loc_id(fa))
            cf[cid][key] = B.directional_sign(fa, cat_dir) * fa["value"]
    return weights, cf


def load_combined():
    cat = B.load(B.PACKAGE_DIR, "lcia_categories", f"{B.CATEGORY_ID}.json")
    return {(B.flow_identity(fa), B.loc_id(fa)): fa["value"] for fa in cat["impactFactors"]}


def score_openlca(inv, weights, cf):
    total = 0.0
    for cid, w in weights.items():
        c = cf[cid]
        total += w * sum(amount * c[key] for key, amount in inv if key in c)
    return total


def score_combined(inv, table):
    return sum(amount * table[key] for key, amount in inv if key in table)


def build_inventory(weights, cf, kind, n=500, seed=0):
    rng = random.Random(seed)
    gen_keys = list({k for cid in weights for k in cf[cid] if k[1] is None})
    reg_keys = list({k for cid in weights for k in cf[cid] if k[1] is not None})
    inv = []
    if kind in ("generic", "mixed"):
        inv += [(k, rng.uniform(1e-3, 1e3)) for k in rng.sample(gen_keys, min(n, len(gen_keys)))]
    if kind in ("regionalized", "mixed"):
        inv += [(k, rng.uniform(1e-3, 1e3)) for k in rng.sample(reg_keys, min(n, len(reg_keys)))]
    return inv


def main():
    print("Loading source + generated category ...")
    weights, cf = load_source()
    table = load_combined()
    print(f"  weights: {len(weights)} cats, combined table: {len(table):,} keys")

    src_keys = {k for cid in weights for k in cf[cid]}
    missing = src_keys - set(table)
    extra = set(table) - src_keys
    dropped_zero = {k for k in missing
                    if abs(sum(weights[c] * cf[c][k] for c in weights if k in cf[c])) <= B.MIN_ABS_CF}
    print(f"  source keys: {len(src_keys):,}  missing(nonzero): "
          f"{len(missing - dropped_zero)}  extra: {len(extra)}")

    ok = (not (missing - dropped_zero)) and (not extra)
    for kind in ("generic", "regionalized", "mixed"):
        for seed in range(3):
            inv = build_inventory(weights, cf, kind, n=500, seed=seed)
            a = score_openlca(inv, weights, cf)
            b = score_combined(inv, table)
            rel = abs(a - b) / max(abs(a), abs(b), 1e-30)
            match = rel < 1e-9
            ok &= match
            print(f"[{kind:12s} seed={seed}]  openLCA={a: .6e}  combined={b: .6e}  "
                  f"rel.diff={rel:.1e}  {'OK' if match else 'MISMATCH!!'}")
    print("\nRESULT:", "ALL MATCH -- arithmetic verified." if ok else "MISMATCH FOUND!")


if __name__ == "__main__":
    main()
