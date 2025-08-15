import streamlit as st
import pandas as pd
import requests
from io import BytesIO
import re

DEFAULT_UNCLEAR_TERMS = ["item", "sample", "unknown", "misc", "product", "variety", "generic"]

st.set_page_config(page_title="UPC Validator & Product Recommender", layout="wide")
st.title("🔍 UPC Validator & Product Recommender")

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
