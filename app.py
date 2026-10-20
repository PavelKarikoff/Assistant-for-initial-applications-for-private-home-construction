"""Страница помощника: текст заявки, кнопка обработки и карточка результата."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from samples import SAMPLES

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

st.set_page_config(page_title="Заявки на строительство домов", page_icon="🏠", layout="centered")

try:
    from gigachat_client import LeadAssistantError, analyze_lead
except ModuleNotFoundError:
    st.title("Заявки на строительство домов")
    st.error(
        "Не хватает библиотеки для запуска. Откройте README и выполните установку: "
        "команда pip install -r requirements.txt внутри папки проекта."
    )
    st.stop()

TEXT_FIELDS = (
    ("company", "Компания"),
    ("summary", "Суть запроса"),
)

if "lead_text" not in st.session_state:
    st.session_state.lead_text = ""
if "result" not in st.session_state:
    st.session_state.result = None
if "error" not in st.session_state:
    st.session_state.error = None


def _use_sample(text: str) -> None:
    st.session_state.lead_text = text
    st.session_state.result = None
    st.session_state.error = None


st.title("Заявки на строительство домов")
st.write(
    "Вставьте текст обращения. Помощник выделит данные, оценит заявку "
    "и подготовит черновик ответа. Итоговое решение остаётся за сотрудником."
)

sample_columns = st.columns(3)
for column, (title, text) in zip(sample_columns, SAMPLES):
    with column:
        st.button(title, width="stretch", on_click=_use_sample, args=(text,))

st.text_area("Текст заявки", height=220, key="lead_text", placeholder="Вставьте текст обращения клиента")

if st.button("Обработать", type="primary", width="stretch"):
    st.session_state.result = None
    st.session_state.error = None
    with st.spinner("Обрабатываем заявку…"):
        try:
            analysis = analyze_lead(st.session_state.lead_text)
        except LeadAssistantError as exc:
            st.session_state.error = exc.message
        else:
            st.session_state.result = analysis.model_dump()

if st.session_state.error:
    st.error(st.session_state.error)

result = st.session_state.result
if result:
    lead_type = result["lead_type"]
    color = {"целевой": "green", "нецелевой": "red", "неясно": "orange"}.get(lead_type, "gray")
    with st.container(border=True):
        st.subheader("Карточка заявки")
        st.markdown(f"**Тип лида:** :{color}[{lead_type}]")
        st.markdown(f"**Нужен человек:** {'Да' if result['needs_human'] else 'Нет'}")
        for key, label in TEXT_FIELDS:
            st.markdown(f"**{label}**")
            st.write(result[key])
        st.markdown("**Известные данные**")
        for item in result["key_data"]:
            st.write(f"• {item}")
        st.markdown("**Чего не хватает**")
        for item in result["missing_data"]:
            st.write(f"• {item}")
        st.markdown("**Следующий шаг**")
        st.write(result["next_step"])
        st.markdown("**Почему нужен человек**")
        st.write(result["human_reason"])
        st.session_state.reply_draft_view = result["reply_draft"]
        st.text_area("Черновик ответа", height=180, disabled=True, key="reply_draft_view")
