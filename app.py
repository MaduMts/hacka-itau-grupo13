"""Agente Dossiê de Hipóteses — app Streamlit (Hackathon Itaú · Grupo 13).

Rodar: streamlit run app.py
"""
import streamlit as st

st.set_page_config(page_title="Dossiê de Hipóteses", page_icon=":material/manage_search:", layout="wide")

from ui.etapas import principal  # noqa: E402

principal()
