# -*- coding: utf-8 -*-
"""
CarboRevit: Automated LEED v5 Embodied Carbon (MRp2/MRc2) & Solar PV (EAp1/EAc1) Calculator
Target Environment: Autodesk Revit 2024 / 2025 / 2026 via pyRevit (IronPython / CPython)
 also supports standalone CLI mode for CSV testing outside Revit.
"""

import csv
import os
import sys

FT3_TO_M3 = 0.0283168466
FT2_TO_M2 = 0.09290304

DEFAULT_BASELINE_FACTORS = {
    "OST_Floors": {"name": "Floor Slabs", "unit": "m3", "gwp": 320.0, "rebar_ratio": 95.0, "is_concrete": True},
    "OST_StructuralFraming": {"name": "Structural Beams", "unit": "m3", "gwp": 350.0, "rebar_ratio": 130.0, "is_concrete": True},
    "OST_StructuralColumns": {"name": "Structural Columns", "unit": "m3", "gwp": 365.0, "rebar_ratio": 150.0, "is_concrete": True},
    "OST_CurtainWall": {"name": "Curtain Wall & Windows", "unit": "m2", "gwp": 210.0, "rebar_ratio": 0.0, "is_concrete": False},
    "OST_Walls": {"name": "Exterior Walls", "unit": "m3", "gwp": 315.0, "rebar_ratio": 85.0, "is_concrete": True},
    "OST_Roofs": {"name": "Roof Deck Assembly", "unit": "m2", "gwp": 115.0, "rebar_ratio": 0.0, "is_concrete": False},
}

BASELINE_REBAR_GWP = 1.99  # kgCO2e / kg virgin BF rebar
EAF_REBAR_GWP = 0.95       # kgCO2e / kg recycled EAF rebar


def validate_schedule_rows(rows):
    """Validate imported BIM schedule rows and return (valid_rows, errors)."""
    required_cols = {"BuiltInCategory", "Unit", "Quantity", "MaterialGWP", "RebarRatio_kg_m3"}
    if not rows:
        return [], ["CSV file is empty or contains no data rows."]

    header_cols = set(rows[0].keys())
    missing = required_cols - header_cols
    if missing:
        return [], [f"Missing required column(s): {', '.join(sorted(missing))}"]

    valid_rows = []
    errors = []
    for idx, row in enumerate(rows, start=2):
        cat = (row.get("BuiltInCategory") or "").strip()
        if cat not in DEFAULT_BASELINE_FACTORS:
            errors.append(f"Row {idx}: Unrecognized BuiltInCategory '{cat}'.")
            continue
        try:
            qty = float(row.get("Quantity", 0))
            gwp = float(row.get("MaterialGWP", 0))
            rebar = float(row.get("RebarRatio_kg_m3", 0))
        except ValueError:
            errors.append(f"Row {idx}: Non-numeric Quantity, MaterialGWP, or RebarRatio_kg_m3.")
            continue

        if qty <= 0:
            errors.append(f"Row {idx}: Quantity must be > 0 (got {qty}).")
            continue
        if gwp < 0 or rebar < 0:
            errors.append(f"Row {idx}: Negative GWP or RebarRatio is invalid.")
            continue

        valid_rows.append({
            "BuiltInCategory": cat,
            "CategoryName": row.get("CategoryName", DEFAULT_BASELINE_FACTORS[cat]["name"]),
            "Unit": row.get("Unit", DEFAULT_BASELINE_FACTORS[cat]["unit"]),
            "Quantity": qty,
            "MaterialGWP": gwp,
            "RebarRatio_kg_m3": rebar
        })

    return valid_rows, errors


def compute_leed_v5_summary(valid_rows, scm_pct=15.0, eaf_pct=30.0, rebar_density_pct=100.0, facade_cut_pct=14.0):
    """Compute baseline vs proposed embodied carbon and LEED v5 MRc2 points."""
    total_base_t = 0.0
    total_prop_t = 0.0
    rebar_unit_gwp = BASELINE_REBAR_GWP * (1.0 - eaf_pct / 100.0) + EAF_REBAR_GWP * (eaf_pct / 100.0)
    scm_factor = 1.0 - (scm_pct * 0.0085)

    results = []
    for item in valid_rows:
        cat = item["BuiltInCategory"]
        qty = item["Quantity"]
        gwp = item["MaterialGWP"]
        rebar = item["RebarRatio_kg_m3"]
        is_conc = DEFAULT_BASELINE_FACTORS[cat]["is_concrete"]

        base_kg = qty * (gwp + rebar * BASELINE_REBAR_GWP) if is_conc else qty * gwp
        if is_conc:
            prop_mat_gwp = gwp * scm_factor
            prop_rebar_kg = rebar * (rebar_density_pct / 100.0) * rebar_unit_gwp
            prop_kg = qty * (prop_mat_gwp + prop_rebar_kg)
        elif cat == "OST_CurtainWall":
            prop_kg = qty * gwp * (1.0 - facade_cut_pct / 100.0)
        else:
            prop_kg = base_kg

        base_t = base_kg / 1000.0
        prop_t = prop_kg / 1000.0
        red_pct = ((base_t - prop_t) / base_t * 100.0) if base_t > 0 else 0.0

        total_base_t += base_t
        total_prop_t += prop_t
        results.append({
            "category": cat,
            "name": item["CategoryName"],
            "baseline_tCO2e": round(base_t, 2),
            "proposed_tCO2e": round(prop_t, 2),
            "reduction_pct": round(red_pct, 1)
        })

    overall_red_pct = ((total_base_t - total_prop_t) / total_base_t * 100.0) if total_base_t > 0 else 0.0
    if overall_red_pct >= 40.0:
        pts = 4
    elif overall_red_pct >= 30.0:
        pts = 3
    elif overall_red_pct >= 20.0:
        pts = 2
    elif overall_red_pct >= 10.0:
        pts = 1
    else:
        pts = 0

    return {
        "baseline_total_tCO2e": round(total_base_t, 2),
        "proposed_total_tCO2e": round(total_prop_t, 2),
        "reduction_pct": round(overall_red_pct, 2),
        "leed_v5_mrc2_points": pts,
        "categories": results
    }


if __name__ == "__main__":
    sample_csv = os.path.join(os.path.dirname(__file__), "..", "data", "sample_revit_schedule_8story_rc.csv")
    if len(sys.argv) > 1:
        sample_csv = sys.argv[1]

    with open(sample_csv, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    valid, errs = validate_schedule_rows(rows)
    if errs:
        print("[VALIDATION ERRORS]:")
        for e in errs:
            print("  -", e)
        sys.exit(1)

    summary = compute_leed_v5_summary(valid)
    print("=== CarboRevit LEED v5 CLI Verification ===")
    print(f"Baseline Total: {summary['baseline_total_tCO2e']} tCO2e")
    print(f"Proposed Total: {summary['proposed_total_tCO2e']} tCO2e")
    print(f"Reduction:      -{summary['reduction_pct']}% -> {summary['leed_v5_mrc2_points']} / 4 LEED v5 MRc2 Points")
