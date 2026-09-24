# EF 3.1 Single Score as an openLCA impact category

openLCA computes the EF 3.1 single score by normalizing and weighting its 16 impact categories, but the total score itself isn’t shown as an impact category. It can't be displayed in a contribution tree or compared across processes. You can create a product system, but this is cumbersome if you just want to calculate the single score for processes. 

This script builds that missing category: **"Total EF 3.1 Single Score (Pt)"**,
inside a new method **"EF 3.1 (Single Score)"**. The original "EF 3.1 Method
(adapted)" is never modified. The inspiration comes from ecoinvents “Ecological Scarcity” Method, which displays its single score the same way. 

## Import into openLCA

1. Download the latest `ef31-single-score.zip` from this repo's
   [Releases page](../../releases) (or build it yourself from source, see
   below).
2. In openLCA: `File > Import > Linked Data (JSON-LD)` → select the zip.
3. You'll now have method **"EF 3.1 (Single Score)"** with all the original EF
   3.1 categories plus **"Total EF 3.1 Single Score (Pt)"**.

## How it works

The EF 3.1 single score is the normalized, weighted sum of its 16 scored impact
categories. Since that sum is linear, it can be rewritten as one combined
characterization factor per flow, read directly from the method's own
Normalization & Weighting set — nothing hard-coded:

```
combined_CF(flow) = Σ_categories  CF_cat(flow) × weightingFactor_cat / normalisationFactor_cat
```

This reproduces openLCA's result under both regionalized and non-regionalized
calculation, and correctly handles impact direction (e.g. water returned to the
environment is subtracted, not added, matching how openLCA treats it). See
`build.py`'s docstring and comments for the exact math and edge cases handled.

## Prerequisites

- Python ≥ 3.11 (standard library only — nothing to install).
- An exported openLCA JSON-LD package containing "EF 3.1 Method (adapted)" —
  e.g. openLCA's official "LCIA Methods" package (`File > Export > Linked Data`
  from a database that has it, or download the package directly).
- openLCA 2.x with the database you want to use this in (the new category
  references existing flows/locations by ID — they must already exist there).

## Build

```bash
python3 build.py
```

Configuration (source package path, source method ID, new names, output UUIDs)
is at the top of `build.py`. The output is `dist/ef31-single-score.zip`.

UUIDs are fixed in the script so re-running and re-importing **updates** the
existing method/category rather than duplicating it — as long as you also bump
`NEW_VERSION`, or import with "Overwrite all existing data sets" (a version-only
bump with "Update data sets with newer versions" can silently no-op).

## Verify

### Offline (arithmetic)

```bash
python3 verify.py
```

Computes random inventories' single scores two independent ways — per-category
normalize+weight (openLCA's exact-key, direction-corrected model) vs. straight
from the generated combined factors — for generic, regionalized, and mixed
inventories, and asserts they match (they agree to ~1e-15). This confirms the
generated file is internally faithful; it does not exercise openLCA itself.
