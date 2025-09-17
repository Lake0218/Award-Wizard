
import os
import io
import json
import time
import textwrap
from typing import List, Tuple, Dict, Optional

import pandas as pd
import requests
import streamlit as st

# -----------------------------
# Page & Sidebar
# -----------------------------
st.set_page_config(
    page_title="Award Wizard • UPC Validator & Recommender",
    page_icon="🪄",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("🪄 Award Wizard — UPC Validator & Recommender")
st.caption("Validate barcodes, flag vague descriptions, and suggest related products.")

with st.sidebar:
    st.header("Settings")
    st.write("Configure backend or use stub data for demos.")

    # Pinot config
    default_endpoint = os.getenv("PINOT_API_ENDPOINT", "")
    pinot_endpoint = st.text_input("Pinot SQL API Endpoint", value=default_endpoint, placeholder="https://pinot.example.com/query/sql")
    pinot_auth = st.text_input("Authorization Header (optional)", type="password", help="e.g., 'Bearer <token>' if your Pinot is secured")

    # Behavior
    use_stub = st.toggle("Use stub mode (no backend)", value=(not bool(default_endpoint)))
    batch_size = st.number_input("Query batch size", min_value=50, max_value=5000, value=1000, step=50, help="Number of UPCs per Pinot request")
    run_button_top = st.button("Run validation ▶", use_container_width=True)

# -----------------------------
# Helpers
# -----------------------------
def _clean_barcodes(df: pd.DataFrame) -> pd.DataFrame:
    if "barcode" not in df.columns:
        raise ValueError("CSV must include a 'barcode' column.")
    # normalize barcodes to strings without spaces
    df = df.copy()
    df["barcode"] = df["barcode"].astype(str).str.strip().str.replace(r"\s+", "", regex=True)
    df = df[df["barcode"].str.len() > 0].drop_duplicates(subset=["barcode"]).reset_index(drop=True)
    return df

def _pinot_headers(auth_header: Optional[str]) -> Dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if auth_header:
        headers["Authorization"] = auth_header
    return headers

def query_pinot_for_upcs(upcs: List[str], endpoint: str, auth_header: Optional[str]) -> pd.DataFrame:
    """Query Pinot using a SQL IN clause over UPCs. Returns product rows."""
    if not endpoint:
        raise ValueError("Pinot endpoint is required when stub mode is off.")

    chunks = [upcs[i:i+batch_size] for i in range(0, len(upcs), batch_size)]
    frames = []
    progress = st.progress(0.0, text="Querying Pinot...")

    for i, chunk in enumerate(chunks, start=1):
        in_list = ",".join([f"'{u}'" for u in chunk])
        sql = f"""
            SELECT
              barcode,
              brand,
              category,
              description,
              keywords
            FROM products
            WHERE barcode IN ({in_list})
        """
        payload = {"sql": textwrap.dedent(sql).strip()}
        try:
            resp = requests.post(endpoint, headers=_pinot_headers(auth_header), data=json.dumps(payload), timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            st.error(f"Pinot request failed on chunk {i}/{len(chunks)}: {e}")
            raise

        # Basic Pinot result parsing; adjust for your specific schema/response
        rows = data.get("resultTable", {}).get("rows", [])
        columns = data.get("resultTable", {}).get("dataSchema", {}).get("columnNames", [])
        if not rows or not columns:
            frames.append(pd.DataFrame(columns=["barcode","brand","category","description","keywords"]))
        else:
            frames.append(pd.DataFrame(rows, columns=columns))

        progress.progress(i/len(chunks), text=f"Querying Pinot... ({i}/{len(chunks)})")

    progress.empty()
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["barcode","brand","category","description","keywords"])
    # Ensure required cols exist
    for col in ["barcode","brand","category","description","keywords"]:
        if col not in out.columns:
            out[col] = None
    return out

def make_stub_catalog(upcs: List[str]) -> pd.DataFrame:
    """Create a small synthetic catalog for demo/testing without Pinot."""
    brands = ["Acme", "Globex", "Umbrella", "Initech"]
    categories = ["Snacks", "Beverages", "Household", "Personal Care"]
    rows = []
    for i, u in enumerate(upcs):
        brand = brands[i % len(brands)]
        cat = categories[(i // 2) % len(categories)]
        # Make some poor descriptions to trigger flags
        if i % 5 == 0:
            desc = "good product"
        elif i % 7 == 0:
            desc = "assorted item"
        else:
            desc = f"{brand} {cat} Item {u[-3:]} — 12oz"
        keys = f"{brand.lower()},{cat.lower()},item"
        rows.append({"barcode": u, "brand": brand, "category": cat, "description": desc, "keywords": keys})
    return pd.DataFrame(rows)

def validate_records(df: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Return (validated, flagged)."""
    df = df.copy()
    df["description"] = df["description"].fillna("")
    df["brand"] = df["brand"].fillna("")
    df["category"] = df["category"].fillna("")

    vague_terms = {"assorted", "misc", "variety", "good product", "item", "product"}
    def is_vague(desc: str) -> bool:
        lower = desc.lower()
        return any(term in lower for term in vague_terms) or len(lower.split()) < 3

    df["is_missing_core"] = df[["brand", "category", "description"]].apply(lambda r: any(pd.isna(r) | (r.str.len()==0)), axis=1)
    df["is_vague"] = df["description"].apply(is_vague)
    df["needs_review"] = df["is_missing_core"] | df["is_vague"]

    flagged = df[df["needs_review"]].copy()
    validated = df[~df["needs_review"]].copy()
    return validated, flagged

def recommend_related(df: pd.DataFrame, k: int = 2) -> pd.DataFrame:
    """Recommend other UPCs to consider by brand/category proximity."""
    if df.empty:
        return pd.DataFrame(columns=["source_barcode","suggested_barcode","reason"])

    # Build simple index by brand-category
    by_bc = df.groupby(["brand","category"])
    suggestions = []
    for (b, c), g in by_bc:
        barcodes = list(g["barcode"])
        for i, src in enumerate(barcodes):
            # suggest next k items in the group (toy logic)
            others = [x for x in barcodes if x != src][:k]
            for o in others:
                suggestions.append({"source_barcode": src, "suggested_barcode": o, "reason": f"Same brand '{b}' and category '{c}'"})

    return pd.DataFrame(suggestions).drop_duplicates().reset_index(drop=True)

def csv_download(name: str, df: pd.DataFrame) -> None:
    if df.empty:
        st.info(f"No rows to download for **{name}**.")
        return
    buf = io.BytesIO()
    df.to_csv(buf, index=False)
    st.download_button(
        f"Download {name}.csv",
        data=buf.getvalue(),
        file_name=f"{name}.csv",
        mime="text/csv",
        use_container_width=True,
    )

# -----------------------------
# Main UI
# -----------------------------
left, right = st.columns([2,1])

with left:
    st.subheader("1) Upload CSV of barcodes")
    uploaded = st.file_uploader("CSV must include a 'barcode' column", type=["csv"])

    st.subheader("2) Validate & analyze")
    run = st.button("Run validation (duplicate of sidebar) ▶", use_container_width=True)

with right:
    st.subheader("Help")
    st.markdown(
        """
        **CSV Format**  
        A single column named `barcode`:
        ```
        barcode
        0123456789012
        0001234567890
        ```

        **Stub Mode**  
        If enabled, the app generates a synthetic catalog so you can try the flow without Pinot.
        """
    )

trigger = run or run_button_top

if trigger:
    if uploaded is None:
        st.warning("Please upload a CSV first.")
        st.stop()

    try:
        raw = pd.read_csv(uploaded)
        upc_df = _clean_barcodes(raw)
    except Exception as e:
        st.error(f"Failed to read/clean CSV: {e}")
        st.stop()

    st.success(f"Loaded {len(upc_df)} unique barcodes.")
    st.dataframe(upc_df.head(20), use_container_width=True)

    # Query catalog
    if use_stub:
        with st.spinner("Generating stub catalog..."):
            catalog_df = make_stub_catalog(upc_df["barcode"].tolist())
    else:
        with st.spinner("Querying Pinot..."):
            catalog_df = query_pinot_for_upcs(upc_df["barcode"].tolist(), pinot_endpoint, pinot_auth)

    st.subheader("Catalog results")
    if catalog_df.empty:
        st.warning("No matches returned. Check your endpoint, auth, or UPC values.")
    st.dataframe(catalog_df.head(50), use_container_width=True)

    # Validation
    with st.spinner("Validating records..."):
        validated_df, flagged_df = validate_records(catalog_df)

    val_col, flag_col = st.columns(2)
    with val_col:
        st.markdown(f"### ✅ Validated ({len(validated_df)})")
        st.dataframe(validated_df.head(50), use_container_width=True)
        csv_download("validated", validated_df)

    with flag_col:
        st.markdown(f"### 🟥 Needs Review ({len(flagged_df)})")
        st.dataframe(flagged_df.head(50), use_container_width=True)
        csv_download("needs_review", flagged_df)

    # Recommendations
    with st.spinner("Building recommendations..."):
        recs_df = recommend_related(catalog_df, k=3)

    st.markdown(f"### 🔎 Related product suggestions ({len(recs_df)})")
    st.dataframe(recs_df.head(100), use_container_width=True)
    csv_download("recommendations", recs_df)

    st.toast("Done!", icon="✅")

st.markdown("---")
st.caption("Tip: Set `PINOT_API_ENDPOINT` as an environment variable to auto-fill the endpoint field.")
