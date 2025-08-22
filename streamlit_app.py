import streamlit as st
import pandas as pd
import requests
from io import BytesIO
import re

DEFAULT_UNCLEAR_TERMS = ["item", "sample", "unknown", "misc", "product", "variety", "generic"]

st.set_page_config(page_title="UPC Validator & Product Recommender", layout="wide")
st.title("🔍 UPC Validator & Product Recommender")

# === Role selection ===========================================================
role = st.selectbox(
    "Who are you?",
    ["IC (Product File)", "QA (Campaign File)"],
    help="Pick IC for product lists; QA for campaign download files."
)

# Role-specific helper text
if "IC" in role:
    uploader_label = "Upload Excel product file (expected columns like: barcode, brand, description)"
else:
    uploader_label = "Upload Excel campaign download file (columns may be named UPC, Product Description, etc.)"

uploaded_file = st.file_uploader(uploader_label, type=["xlsx"])

# --- Column alias maps (case-insensitive) ------------------------------------
ALIAS_BARCODE = {"barcode", "bar_code", "upc", "upc12", "upc-12", "upc_a", "upc-a", "gtin", "gtin12", "barcode_num", "barcode number"}
ALIAS_BRAND = {"brand", "brand_name", "brand name", "mfrbrand", "mfr_brand"}
ALIAS_DESCRIPTION = {"description", "desc", "product", "product_name", "product name", "product_description", "product description", "item_description", "item description", "item name"}

def _normalize_cols(df):
    """Return lower-cased columns and a map from normalized->original for UI selection."""
    norm_map = {c: c for c in df.columns}
    df2 = df.copy()
    df2.columns = [str(c).strip() for c in df2.columns]
    normed = [c.lower().strip() for c in df2.columns]
    df2.columns = normed
    colmap = dict(zip(normed, norm_map.keys()))  # normalized -> original label
    return df2, colmap

def _auto_pick_column(norm_cols, alias_set):
    """Pick the first column whose normalized name matches any alias."""
    for c in norm_cols:
        if c in alias_set:
            return c
    return None

def _make_manual_mapping_ui(norm_cols, picked_barcode, picked_brand, picked_description):
    st.info("We couldn’t confidently match all columns. Please map them below.")
    col1, col2, col3 = st.columns(3)
    with col1:
        bc_sel = st.selectbox("Barcode column", ["-- choose --"] + norm_cols, index= (norm_cols.index(picked_barcode)+1 if picked_barcode in norm_cols else 0))
    with col2:
        br_sel = st.selectbox("Brand column", ["-- choose --"] + norm_cols, index= (norm_cols.index(picked_brand)+1 if picked_brand in norm_cols else 0))
    with col3:
        ds_sel = st.selectbox("Description column", ["-- choose --"] + norm_cols, index= (norm_cols.index(picked_description)+1 if picked_description in norm_cols else 0))
    # Return None for unchosen
    bc_sel = None if bc_sel == "-- choose --" else bc_sel
    br_sel = None if br_sel == "-- choose --" else br_sel
    ds_sel = None if ds_sel == "-- choose --" else ds_sel
    return bc_sel, br_sel, ds_sel

products_df = None

if uploaded_file is not None:
    raw_df = pd.read_excel(uploaded_file, dtype=str)  # keep strings as strings (preserves leading zeros)
    if raw_df.empty:
        st.error("The uploaded Excel file appears to be empty.")
    else:
        df_norm, norm_to_orig = _normalize_cols(raw_df)

        # If QA, we expect campaign files to use alternate names more often, so we lean on aliasing.
        # If IC, the file is more likely to be “clean” but we still alias for safety.
        norm_cols = list(df_norm.columns)

        # Auto-picks
        picked_barcode = _auto_pick_column(norm_cols, ALIAS_BARCODE)
        picked_brand = _auto_pick_column(norm_cols, ALIAS_BRAND)
        picked_description = _auto_pick_column(norm_cols, ALIAS_DESCRIPTION)

        # If any of the key picks are missing, present manual mapping UI
        if not picked_barcode or not picked_description:
            picked_barcode, picked_brand, picked_description = _make_manual_mapping_ui(
                norm_cols, picked_barcode, picked_brand, picked_description
            )

        # Validate mappings
        missing = []
        if not picked_barcode: missing.append("barcode")
        if not picked_description: missing.append("description")
        # brand is helpful but optional in some IC flows
        if missing:
            st.error(f"Please map the following required column(s): {', '.join(missing)}.")
        else:
            # Build a unified dataframe
            products_df = pd.DataFrame({
                "barcode": df_norm[picked_barcode].astype(str).str.replace(r"\D", "", regex=True).str.zfill(12),
                "description": df_norm[picked_description].astype(str).str.strip()
            })
            if picked_brand and picked_brand in df_norm.columns:
                products_df["brand"] = df_norm[picked_brand].astype(str).str.strip()
            else:
                products_df["brand"] = ""

            # Role-specific quick hints
            if "IC" in role:
                st.caption("IC mode: expecting product master files. We normalized columns and barcodes to 12 digits.")
            else:
                st.caption("QA mode: expecting campaign download files. We auto-mapped typical headers like UPC / Product Description.")

            st.success(f"Ingested {len(products_df):,} rows.")
            st.dataframe(products_df.head(25))

            # If you already have downstream validators (description, size, keywords, etc.),
            # they can now consume `products_df` which has guaranteed columns:
            #   - barcode (12-digit string)
            #   - brand   (string, possibly empty)
            #   - description (string)
            #
            # Example: ensure brand selector derives from uploaded data (non-empty):
            brand_options = sorted([b for b in products_df["brand"].dropna().unique() if b and b.strip()])
            if brand_options:
                selected_brand = st.selectbox("Filter by brand (optional)", ["(All brands)"] + brand_options)
                if selected_brand != "(All brands)":
                    products_df = products_df[products_df["brand"] == selected_brand]
                    st.caption(f"Filtered to {len(products_df):,} rows for brand '{selected_brand}'.")
            else:
                selected_brand = "(All brands)"

# --- Custom Description Keywords ---
st.subheader("🧠 Description Validator")
description_input = st.text_input("Enter product keywords expected in descriptions (comma-separated)", value="")
custom_keywords = [kw.strip().lower() for kw in description_input.split(",") if kw.strip()] if description_input else []
UNCLEAR_TERMS = DEFAULT_UNCLEAR_TERMS + custom_keywords

# --- Upload UPC Excel File ---
st.subheader("📥 Upload Campaign UPC File")
uploaded_file = st.file_uploader("Upload an Excel file with columns 'barcode', 'brand', and 'description'", type="xlsx")

if uploaded_file:
    try:
        input_df = pd.read_excel(uploaded_file, dtype={'barcode': str})
    except Exception as e:
        st.error(f"Error reading Excel file: {e}")
        st.stop()

    if not {'barcode', 'brand', 'description'}.issubset(input_df.columns):
        st.error("Excel file must contain 'barcode', 'brand', and 'description' columns.")
        st.stop()

    # --- Brand Filter Dropdown ---
    brand_options = sorted(input_df['brand'].dropna().unique().tolist())
    selected_brand = st.selectbox("Select a brand to validate", brand_options)

    filtered_df = input_df[input_df['brand'] == selected_brand].copy()
    filtered_df['barcode'] = filtered_df['barcode'].astype(str).str.zfill(12)

    ilike_clauses = [kw.lower() for kw in UNCLEAR_TERMS]

    def flag_description(desc):
        if pd.isna(desc) or len(desc) < 10:
            return 'Too short'
        for term in ilike_clauses:
            if term in desc.lower():
                return 'Unclear or Generic'
        return None

    def extract_size_ml(desc):
        if not isinstance(desc, str):
            return None
        match_ml = re.search(r'(\d+(\.\d+)?)\s?m[lL]', desc)
        match_l = re.search(r'(\d+(\.\d+)?)\s?[lL]', desc)

        if match_ml:
            return float(match_ml.group(1))
        elif match_l:
            return float(match_l.group(1)) * 1000
        return None

    def flag_size(size_ml):
        if size_ml is None:
            return 'No size found'
        elif size_ml < 750:
            return 'Too small'
        return None

    filtered_df['description_flag'] = filtered_df['description'].apply(flag_description)
    filtered_df['parsed_size_ml'] = filtered_df['description'].apply(extract_size_ml)
    filtered_df['size_flag'] = filtered_df['parsed_size_ml'].apply(flag_size)

    st.subheader("🧪 Validation Results")
    st.dataframe(filtered_df)

    flagged_df = filtered_df[(filtered_df['description_flag'].notna()) | (filtered_df['size_flag'].notna())]
    if not flagged_df.empty:
        st.warning("🚩 Some UPCs have issues with description or size:")
        st.dataframe(flagged_df)

        excel_buffer = BytesIO()
        with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
            flagged_df.to_excel(writer, index=False, sheet_name='Flagged')
        st.download_button("Download flagged UPCs as Excel", data=excel_buffer.getvalue(), file_name="flagged_upcs.xlsx")
