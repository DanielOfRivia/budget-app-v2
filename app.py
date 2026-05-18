import os
# from dotenv import load_dotenv
import streamlit as st
import pandas as pd
from normalize import build_transaction_frame, detect_source

# load_dotenv()
st.set_page_config(page_title="CSV Preview App", layout="wide")

st.title("Budget monthly update")
st.markdown(
    "Upload a CSV file and preview it. The app will identify if it's an AMEX or RBC export and normalize transactions."
)

uploaded_files = st.file_uploader(
    "Upload transaction CSV", type=["csv"], key="csv", accept_multiple_files=True
)

if uploaded_files:
    for uploaded_file in uploaded_files:
        try:
            df = pd.read_csv(uploaded_file)
        except Exception as e:
            st.error(f"Unable to read CSV file: {e}")
            continue
        
        st.dataframe(df)
        source_type = detect_source(df)
        standardized = build_transaction_frame(df, uploaded_file.name or "uploaded_file.csv", source_type)

        st.subheader(f"Detected source: {source_type} for file {uploaded_file.name or 'uploaded_file.csv'}")
        st.markdown("**Standardized transaction preview:**")
        st.dataframe(standardized)
else:
    st.info("Please upload a CSV file to preview it.")
