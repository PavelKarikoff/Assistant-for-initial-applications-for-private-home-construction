"""Страница помощника: форма заявки, кнопка обработки и карточка результата."""

from __future__ import annotations

from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from anonymizer import anonymize
from samples import SAMPLES

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

st.set_page_config(page_title="Заявки на строительство домов", page_icon="🏠", layout="centered")

try:
    from bitrix_client import BitrixError, publish_application
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

for key in ("client_name", "client_phone", "client_email", "lead_text"):
    if key not in st.session_state:
        st.session_state[key] = ""
if "result" not in st.session_state:
    st.session_state.result = None
if "error" not in st.session_state:
    st.session_state.error = None
for key in ("crm_error", "crm_message", "crm_warning"):
    if key not in st.session_state:
        st.session_state[key] = None


def _clear_outcome() -> None:
    st.session_state.result = None
    st.session_state.error = None
    st.session_state.crm_error = None
    st.session_state.crm_message = None
    st.session_state.crm_warning = None


def _use_sample(sample: dict) -> None:
    st.session_state.client_name = sample["name"]
    st.session_state.client_phone = sample["phone"]
    st.session_state.client_email = sample["email"]
    st.session_state.lead_text = sample["text"]
    _clear_outcome()


def _compose_lead(name: str, phone: str, email: str, request: str) -> str:
    lines = [
        f"Имя: {name.strip()}",
        f"Телефон: {phone.strip()}",
        f"Почта: {email.strip()}",
        "",
        request.strip(),
    ]
    return "\n".join(lines)


def _anonymized_for_model() -> str:
    """Текст, который уходит в нейросеть: персональные данные уже заменены токенами."""
    name = st.session_state.client_name
    phone = st.session_state.client_phone
    email = st.session_state.client_email
    request = st.session_state.lead_text
    if not any(value.strip() for value in (name, phone, email, request)):
        return ""
    anonymized, _mapping = anonymize(_compose_lead(name, phone, email, request))
    return anonymized


st.title("Заявки на строительство домов")
st.write(
    "Заполните форму заявки. Помощник выделит данные, оценит обращение "
    "и подготовит черновик ответа. Итоговое решение остаётся за сотрудником."
)

sample_rows = (st.columns(2), st.columns(2))
sample_columns = (*sample_rows[0], *sample_rows[1])
for column, (title, sample) in zip(sample_columns, SAMPLES):
    with column:
        st.button(title, width="stretch", on_click=_use_sample, args=(sample,))

with st.form("lead_form", border=True):
    st.text_input("Имя", key="client_name", placeholder="Иван Иванов")
    contact_columns = st.columns(2)
    with contact_columns[0]:
        st.text_input("Телефон", key="client_phone", placeholder="+7 900 000-00-00")
    with contact_columns[1]:
        st.text_input("Почта", key="client_email", placeholder="client@example.com")
    st.text_area(
        "Заявка",
        height=180,
        key="lead_text",
        placeholder="Опишите дом, участок, бюджет и желаемый срок начала стройки",
    )
    submitted = st.form_submit_button("Обработать", type="primary", width="stretch")

with st.container(border=True):
    st.subheader("Текст для нейросети")
    st.caption("Персональные данные заменены токенами. В таком виде заявка уходит на генерацию.")
    anonymized_text = _anonymized_for_model()
    if anonymized_text:
        st.session_state.anonymized_view = anonymized_text
        st.text_area(
            "Анонимизированный текст",
            height=220,
            disabled=True,
            key="anonymized_view",
            label_visibility="collapsed",
        )
    else:
        st.write("Выберите учебный пример или заполните форму и нажмите «Обработать».")

if submitted:
    _clear_outcome()
    missing = [
        label
        for label, value in (
            ("имя", st.session_state.client_name),
            ("телефон", st.session_state.client_phone),
            ("почту", st.session_state.client_email),
            ("текст заявки", st.session_state.lead_text),
        )
        if not value.strip()
    ]
    if missing:
        st.session_state.error = "Заполните " + ", ".join(missing) + "."
    else:
        lead = _compose_lead(
            st.session_state.client_name,
            st.session_state.client_phone,
            st.session_state.client_email,
            st.session_state.lead_text,
        )
        with st.spinner("Обрабатываем заявку…"):
            try:
                analysis = analyze_lead(lead)
            except LeadAssistantError as exc:
                st.session_state.error = exc.message
            else:
                st.session_state.result = analysis.model_dump()
        if st.session_state.result:
            with st.spinner("Передаём заявку в Битрикс24…"):
                try:
                    published = publish_application(
                        name=st.session_state.client_name,
                        phone=st.session_state.client_phone,
                        email=st.session_state.client_email,
                        request_text=st.session_state.lead_text,
                        analysis=st.session_state.result,
                    )
                except BitrixError as exc:
                    st.session_state.crm_error = exc.message
                else:
                    st.session_state.crm_message = (
                        f"Заявка записана в Битрикс24: {published.entity_label} "
                        f"№{published.entity_id}."
                    )
                    st.session_state.crm_warning = published.warning

if st.session_state.error:
    st.error(st.session_state.error)
if st.session_state.crm_error:
    st.error(st.session_state.crm_error)
if st.session_state.crm_message:
    st.success(st.session_state.crm_message)
if st.session_state.crm_warning:
    st.warning(st.session_state.crm_warning)

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
