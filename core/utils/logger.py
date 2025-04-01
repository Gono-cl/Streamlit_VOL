import streamlit as st
import sys
import io

class StreamlitLogger:
    def __init__(self, placeholder=None):
        self.output = io.StringIO()
        self.placeholder = placeholder or st.empty()

    def write(self, message):
        self.output.write(message)
        self.placeholder.code(self.output.getvalue())

    def flush(self):
        pass  # Required for compatibility with sys.stdout
