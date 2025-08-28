# streamlit_app.py
import streamlit as st
import pandas as pd
import re
from io import BytesIO
import io

DEFAULT_UNCLEAR_TERMS = ["item", "sample", "unknown", "misc", "product", "variety", "generic"]

st.set_page_config(page_title="UPC Validator & Product Recommender", layout="wide")
st.title("🔍 UPC Validator & Product Recommender")

# === Role selection ===========================================================
role = st.radio(
    "Who are you?",
    ["IC (Product File)", "QA (Campaign File)"],
    horizontal=True,
    help="Pick IC for product lists; QA for campaign download files."
)

# === Uploaders ================================================================
if "IC" in role:
    uploader_label = "Upload product file (CSV or Excel). We’ll map to: barcode, brand, description."
else:
    uploader_label = "Upload QA campaign file (CSV or Excel) containing UPC, Description, RequirementName."

uploaded_file = st.file_uploader(uploader_label, type=["csv", "xlsx", "xls"])

# === Helpers =================================================================
ALIAS_BARCODE = {
    "barcode", "bar_code", "upc", "upc12", "upc-12", "upc_a", "upc-a",
    "gtin", "gtin12", "gtin-12", "ean", "ean13", "barcode_num", "barcode number"
}
ALIAS_BRAND = {"brand", "brand_name", "brand name", "mfrbrand", "mfr_brand"}
ALIAS_DESCRIPTION = {
    "description", "desc", "product", "product_name", "product name",
    "product_description", "product description", "item_description", "item description", "item name", "title"
}

def _read_any(file):
    """Read CSV or Excel to DataFrame (as strings when possible)."""
    name = file.name.lower()
    if name.endswith(".csv"):
        # Keep strings as strings; avoid dtype inference eating leading zeros
        return pd.read_csv(file, dtype=str, keep_default_na=False)
    else:
        data = file.read()
        # Try openpyxl first, then fallback
        try:
            return pd.read_excel(io.BytesIO(data), dtype=str, engine="openpyxl")
        except Exception:
            return pd.read_excel(io.BytesIO(data), dtype=str)

def _normalize_cols(df: pd.DataFrame):
    """Lowercase/strip columns; return normalized df + map normalized->original (for UI)."""
    original_cols = list(df.columns)
    df2 = df.copy()
    df2.columns = [str(c).strip() for c in df2.columns]
    normed = [c.lower().strip() for c in df2.columns]
    df2.columns = normed
    colmap = dict(zip(normed, original_cols))
    return df2, colmap

def _auto_pick_column(norm_cols, alias_set):
    for c in norm_cols:
        if c in alias_set:
            return c
    return None

def _make_manual_mapping_ui(norm_cols, picked_barcode, picked_brand, picked_description):
    st.info("We couldn’t confidently match all columns. Please map them below.")
    col1, col2, col3 = st.columns(3)
    with col1:
        bc_sel = st.selectbox("Barcode column", ["-- choose --"] + norm_cols,
                              index=(norm_cols.index(picked_barcode)+1 if picked_barcode in norm_cols else 0))
    with col2:
        br_sel = st.selectbox("Brand column (optional)", ["-- choose --"] + norm_cols,
                              index=(norm_cols.index(picked_brand)+1 if picked_brand in norm_cols else 0))
    with col3:
        ds_sel = st.selectbox("Description column", ["-- choose --"] + norm_cols,
                              index=(norm_cols.index(picked_description)+1 if picked_description in norm_cols else 0))
    bc_sel = None if bc_sel == "-- choose --" else bc_sel
    br_sel = None if br_sel == "-- choose --" else br_sel
    ds_sel = None if ds_sel == "-- choose --" else ds_sel
    return bc_sel, br_sel, ds_sel

def _clean_barcode_series(s: pd.Series) -> pd.Series:
    """Keep digits only; support EAN/GTIN up to 14 by preserving rightmost digits."""
    return (
        s.astype(str)
         .str.extract(r"(\d+)")[0]
         .str[-14:]
         .fillna("")
    )

# === IC normalization =========================================================
def normalize_ic(df_raw: pd.DataFrame) -> pd.DataFrame:
    df_norm, _ = _normalize_cols(df_raw)
    norm_cols = list(df_norm.columns)
    picked_barcode = _auto_pick_column(norm_cols, ALIAS_BARCODE)
    picked_brand = _auto_pick_column(norm_cols, ALIAS_BRAND)
    picked_description = _auto_pick_column(norm_cols, ALIAS_DESCRIPTION)

    # If missing key picks, ask user to map
    if not picked_barcode or not picked_description:
        picked_barcode, picked_brand, picked_description = _make_manual_mapping_ui(
            norm_cols, picked_barcode, picked_brand, picked_description
        )

    missing = []
    if not picked_barcode: missing.append("barcode")
    if not picked_description: missing.append("description")
    if missing:
        st.error(f"Please map the following required column(s): {', '.join(missing)}.")
        return pd.DataFrame(columns=["barcode", "brand", "description"])

    out = pd.DataFrame()
    out["barcode"] = _clean_barcode_series(df_norm[picked_barcode])
    out["description"] = df_norm[picked_description].astype(str).str.strip()
    if picked_brand and picked_brand in df_norm.columns:
        out["brand"] = df_norm[picked_brand].astype(str).str.strip()
    else:
        out["brand"] = ""
    # Right-pad to common UPC length for display; keep digits only for validation use
    # (You can remove zfill if you prefer raw GTIN/EAN lengths.)
    out["barcode"] = out["barcode"].str.zfill(12).str[-14:]
    return out[["barcode", "brand", "description"]]

# === QA normalization & split (Awarding vs Audience) =========================
def normalize_qa_and_split(df_raw: pd.DataFrame, append_size_to_desc: bool = True):
    """
    Expect columns (case-insensitive): UPC, Description, RequirementName
    'Unlabeled Requirement' => awarding; others => audience.
    Returns awarding_df (canonical), audience_df (canonical), and a small summary dict.
    """
    df, _ = _normalize_cols(df_raw)
    required = ["upc", "description", "requirementname"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        st.error(f"QA file is missing required column(s): {missing}. Make sure your campaign export includes UPC, Description, RequirementName.")
        return pd.DataFrame(columns=["barcode", "brand", "description"]), pd.DataFrame(columns=["barcode", "brand", "description"]), {}

    # Build base
    work = pd.DataFrame(index=df.index)
    work["barcode"] = _clean_barcode_series(df["upc"])
    # description (+ optional Size)
    desc = df["description"].astype(str).str.strip()
    if append_size_to_desc and "size" in df.columns:
        size = df["size"].astype(str).str.strip()
        work["description"] = [
            d if (not s or s.lower() in d.lower()) else f"{d} - {s}"
            for d, s in zip(desc, size)
        ]
    else:
        work["description"] = desc
    # Brand not present in QA; leave blank (can be backfilled from catalog later)
    work["brand"] = ""

    # Tag awarding vs audience
    rn = df["requirementname"].astype(str).fillna("").str.strip()
    awarding_mask = rn.str.casefold().eq("unlabeled requirement")

    awarding = work[awarding_mask].copy().reset_index(drop=True)
    audience = work[~awarding_mask].copy().reset_index(drop=True)

    # Basic hygiene warning
    bad = ~work["barcode"].str.match(r"^\d{8,14}$")
    if bad.any():
        st.warning(f"{int(bad.sum())} row(s) have non-8/12/13/14-digit barcodes after cleaning.")

    summary = {
        "rows_total": len(work),
        "rows_awarding": len(awarding),
        "rows_audience": len(audience),
    }
    # Canonical column order
    awarding = awarding[["barcode", "brand", "description"]]
    audience = audience[["barcode", "brand", "description"]]
    return awarding, audience, summary

# === Description/Size Validation =============================================
def build_unclear_terms():
    st.subheader("🧠 Description Validator")
    description_input = st.text_input(
        "Enter product keywords expected in descriptions (comma-separated)",
        value=""
    )
    custom_keywords = [kw.strip().lower() for kw in description_input.split(",") if kw.strip()] if description_input else []
    return DEFAULT_UNCLEAR_TERMS + custom_keywords

def extract_size_ml(desc):
    if not isinstance(desc, str):
        return None
    # Look for patterns like "750 ml", "1.5 L"
    match_ml = re.search(r'(\d+(\.\d+)?)\s?m[lL]\b', desc)
    match_l = re.search(r'(\d+(\.\d+)?)\s?[lL]\b', desc)
    if match_ml:
        try:
            return float(match_ml.group(1))
        except Exception:
            return None
    if match_l:
        try:
            return float(match_l.group(1)) * 1000
        except Exception:
            return None
    return None

def flag_description(desc, ilike_clauses):
    if pd.isna(desc) or len(str(desc)) < 10:
        return 'Too short'
    for term in ilike_clauses:
        if term in str(desc).lower():
            return 'Unclear or Generic'
    return None

def flag_size(size_ml):
    if size_ml is None:
        return 'No size found'
    elif size_ml < 750:
        return 'Too small'
    return None

# === Main flow ===============================================================
canonical_df = pd.DataFrame(columns=["barcode", "brand", "description"])
audience_df = pd.DataFrame(columns=["barcode", "brand", "description"])  # only for QA view

if uploaded_file is not None:
    df_raw = _read_any(uploaded_file)
    if df_raw.empty:
        st.error("The uploaded file appears to be empty.")
    else:
        if "IC" in role:
            st.caption("IC mode: expecting product master files. We’ll normalize aliases and barcodes.")
            canonical_df = normalize_ic(df_raw)
            if not canonical_df.empty:
                st.success(f"Ingested {len(canonical_df):,} rows ✓")
                st.dataframe(canonical_df.head(25), use_container_width=True)
        else:
            st.caption("QA mode: campaign files with UPC, Description, RequirementName. We’ll split awarding vs audience and validate awarding.")
            append_size = st.checkbox("Append Size to description (if present)", value=True)
            awarding_df, audience_df, summary = normalize_qa_and_split(df_raw, append_size_to_desc=append_size)
            if summary:
                st.info(f"Rows: {summary['rows_total']:,} • Awarding: {summary['rows_awarding']:,} • Audience: {summary['rows_audience']:,}")
            if not awarding_df.empty:
                with st.expander("Preview: Awarding UPCs (canonical)"):
                    st.dataframe(awarding_df.head(50), use_container_width=True)
                with st.expander("Preview: Audience UPCs (FYI)"):
                    st.dataframe(audience_df.head(50), use_container_width=True)
                canonical_df = awarding_df  # Only awarding moves to validation

                # Optional downloads
                st.download_button(
                    "Download awarding UPCs (canonical CSV)",
                    awarding_df.to_csv(index=False).encode("utf-8"),
                    file_name="awarding_upcs_canonical.csv",
                    mime="text/csv",
                )
                st.download_button(
                    "Download audience UPCs (CSV)",
                    audience_df.to_csv(index=False).encode("utf-8"),
                    file_name="audience_upcs.csv",
                    mime="text/csv",
                )

# === Validation section (runs on canonical_df) ===============================
UNCLEAR_TERMS = build_unclear_terms()

if not canonical_df.empty:
    st.subheader("🧪 Validation Results")

    # Brand filter (only if there are non-empty brands)
    brand_options = sorted([b for b in canonical_df["brand"].dropna().unique().tolist() if str(b).strip()])
    if brand_options:
        selected_brand = st.selectbox("Filter by brand (optional)", ["(All brands)"] + brand_options)
        if selected_brand != "(All brands)":
            validate_df = canonical_df[canonical_df["brand"] == selected_brand].copy()
            st.caption(f"Filtered to {len(validate_df):,} rows for brand '{selected_brand}'.")
        else:
            validate_df = canonical_df.copy()
    else:
        validate_df = canonical_df.copy()

    # Normalize barcode display to 12–14 digits (keep whatever length is present)
    validate_df["barcode"] = validate_df["barcode"].astype(str).str.replace(r"\D", "", regex=True).str[-14:]

    # Apply flags
    validate_df["description_flag"] = validate_df["description"].apply(lambda d: flag_description(d, UNCLEAR_TERMS))
    validate_df["parsed_size_ml"] = validate_df["description"].apply(extract_size_ml)
    validate_df["size_flag"] = validate_df["parsed_size_ml"].apply(flag_size)

    st.dataframe(validate_df, use_container_width=True)

    flagged_df = validate_df[(validate_df['description_flag'].notna()) | (validate_df['size_flag'].notna())]
    if not flagged_df.empty:
        st.warning("🚩 Some UPCs have issues with description or size:")
        st.dataframe(flagged_df, use_container_width=True)

        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
            flagged_df.to_excel(writer, index=False, sheet_name='Flagged')
        st.download_button("Download flagged UPCs as Excel", data=excel_buffer.getvalue(), file_name="flagged_upcs.xlsx")

else:
    st.info("Upload a file above to begin.")
