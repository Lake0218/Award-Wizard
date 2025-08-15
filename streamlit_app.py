import streamlit as st
import pandas as pd
import requests
from io import BytesIO

# Replace this with your actual API endpoint to Pinot
PINOT_API_ENDPOINT = "https://your-pinot-api-url.com/openai/fetch_gpt_api/sql/query"

DEFAULT_UNCLEAR_TERMS = ["item", "sample", "unknown", "misc", "product", "variety", "generic"]

st.set_page_config(page_title="UPC Validator & Product Recommender", layout="wide")
st.title("🔍 UPC Validator & Product Recommender")

# --- Brand Selector ---
st.subheader("🏷️ Brand Selector")
st.write("Start typing to choose a brand to validate.")

# Pull distinct brands from Pinot
brand_query = "SELECT DISTINCT brand FROM catalog_lookup_gpt ORDER BY brand LIMIT 10000"
brand_response = requests.post(PINOT_API_ENDPOINT, json={"query": brand_query}, headers={"Content-Type": "application/json"})

brand_options = []
if brand_response.status_code == 200:
    brand_data = brand_response.json()
    brand_options = sorted([row[0] for row in brand_data['resultTable']['rows'] if row[0]])
else:
    st.warning("Could not load brand options.")

selected_brand = st.selectbox("Select brand", brand_options, index=0 if brand_options else None)

# --- Custom Description Keywords ---
st.subheader("🧠 Description Validator")
description_input = st.text_input("Enter product keywords expected in descriptions (comma-separated)", value="")

custom_keywords = [kw.strip().lower() for kw in description_input.split(",") if kw.strip()] if description_input else []
UNCLEAR_TERMS = DEFAULT_UNCLEAR_TERMS + custom_keywords

uploaded_file = st.file_uploader("Upload an Excel file with a column named 'barcode'", type="xlsx")

if uploaded_file:
    input_df = pd.read_excel(uploaded_file, dtype={'barcode': str})

    if 'barcode' not in input_df.columns:
        st.error("Excel file must contain a 'barcode' column.")
    else:
        st.success(f"Loaded {len(input_df)} UPCs")

        upc_list = input_df['barcode'].dropna().astype(str).unique().tolist()
        upc_clause = ", ".join([f"'{upc}'" for upc in upc_list])
        ilike_clauses = " OR ".join([f"LOWER(description) LIKE '%{term.lower()}%'" for term in UNCLEAR_TERMS])

        sql_query = f"""
        SELECT
            barcode,
            brand,
            manufacturer,
            category_1_search_key,
            category_2_search_key,
            category_3_search_key,
            category_4_search_key,
            description,
            LENGTH(description) AS desc_length,
            CASE
                WHEN LENGTH(description) < 10 THEN 'Too short'
                WHEN {ilike_clauses} THEN 'Unclear or Generic'
                ELSE NULL
            END AS description_flag
        FROM catalog_lookup_gpt
        WHERE barcode IN ({upc_clause})
        AND brand = '{selected_brand}'
        """

        headers = {"Content-Type": "application/json"}
        response = requests.post(PINOT_API_ENDPOINT, json={"query": sql_query}, headers=headers)

        if response.status_code != 200:
            st.error("Error querying Pinot API.")
        else:
            data = response.json()
            result_df = pd.DataFrame(data['resultTable']['rows'], columns=[col['columnName'] for col in data['resultTable']['dataSchema']['columnDataTypes']])
            result_df['barcode'] = result_df['barcode'].astype(str).str.zfill(12)

            st.subheader("🧪 Validation Results")
            st.dataframe(result_df)

            flagged_df = result_df[result_df['description_flag'].notna()]
            if not flagged_df.empty:
                st.warning("🚩 Some UPCs have unclear or problematic descriptions:")
                st.dataframe(flagged_df)

                excel_buffer = BytesIO()
                with pd.ExcelWriter(excel_buffer, engine='xlsxwriter') as writer:
                    flagged_df.to_excel(writer, index=False, sheet_name='Flagged')
                st.download_button("Download flagged UPCs as Excel", data=excel_buffer.getvalue(), file_name="flagged_upcs.xlsx")

            st.subheader("💡 Suggested Additional UPCs")
            category_filter = st.text_input("Enter category_1_search_key (optional)")
            keyword_filter = st.text_input("Enter keyword to filter descriptions (optional)")

            if selected_brand:
                keyword_clause = f"AND description ILIKE '%{keyword_filter}%'" if keyword_filter else ""
                category_clause = f"AND category_1_search_key = '{category_filter}'" if category_filter else ""

                recommender_query = f"""
                SELECT DISTINCT barcode, brand, manufacturer, description,
                    category_1_search_key, category_2_search_key, category_3_search_key
                FROM catalog_lookup_gpt
                WHERE brand = '{selected_brand}'
                {category_clause}
                {keyword_clause}
                AND barcode NOT IN ({upc_clause})
                ORDER BY description
                LIMIT 200
                """

                recommender_response = requests.post(PINOT_API_ENDPOINT, json={"query": recommender_query}, headers=headers)

                if recommender_response.status_code == 200:
                    rec_data = recommender_response.json()
                    rec_df = pd.DataFrame(rec_data['resultTable']['rows'], columns=[col['columnName'] for col in rec_data['resultTable']['dataSchema']['columnDataTypes']])
                    rec_df['barcode'] = rec_df['barcode'].astype(str).str.zfill(12)
                    st.dataframe(rec_df)

                    rec_buffer = BytesIO()
                    with pd.ExcelWriter(rec_buffer, engine='xlsxwriter') as writer:
                        rec_df.to_excel(writer, index=False, sheet_name='Suggestions')

                    st.download_button("Download suggested UPCs as Excel", data=rec_buffer.getvalue(), file_name="suggested_upcs.xlsx")
                else:
                    st.error("Failed to fetch recommendations.")
