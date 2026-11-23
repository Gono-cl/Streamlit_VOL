import pandas as pd
import streamlit as st
from io import BytesIO
from core.utils.log_units import unit_labeled_dataframe

def export_to_csv(df, filename="results.csv", **unit_settings):
    csv = unit_labeled_dataframe(df, **unit_settings).to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        label="📥 Download CSV",
        data=csv,
        file_name=filename,
        mime="text/csv"
    )

def export_to_excel(df, filename="results.xlsx", **unit_settings):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        unit_labeled_dataframe(df, **unit_settings).to_excel(writer, index=False, sheet_name="Results")
    processed_data = output.getvalue()
    st.download_button(
        label="📥 Download Excel",
        data=processed_data,
        file_name=filename,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

