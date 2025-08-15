# 🔍 UPC Validator & Product Recommender

A Streamlit app to validate product UPCs and recommend related items using Fetch's Pinot-based catalog.

## 🚀 Features
- Validates uploaded UPCs against brand/category/description rules
- Flags vague or unclear product descriptions
- Suggests additional UPCs based on brand/category/keywords
- Download results as CSVs
- Docker-ready deployment

## 🧠 Tech Stack
- Python + Streamlit
- Pinot SQL queries via HTTP API
- Dockerized for easy deployment

## 🛠 How to Run Locally

```bash
pip install -r requirements.txt
streamlit run streamlit_app.py
```

## 🐳 Run with Docker

```bash
docker build -t upc-validator .
docker run -p 8501:8501 upc-validator
```

## 📄 File Upload Format
Upload a CSV with a single column named `barcode`.

```
barcode
0123456789012
0001234567890
...
```

## 🔐 Note
This tool assumes a Pinot SQL API is available and configured in the `PINOT_API_ENDPOINT` variable in `streamlit_app.py`.
