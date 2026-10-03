"""Provider settings with cached, non-generative model availability checks."""
import streamlit as st
from src.llm.config import DEFAULT_MODELS, PROVIDER_LABELS, LLMConfig, load_config, save_config
from src.llm.connection_status import connection_status, _cached_status

def show_settings_page():
    status = st.empty()
    try:
        active = load_config().provider
    except (ValueError, OSError):
        active = "openai"
    provider = st.selectbox("KI-Anbieter", list(PROVIDER_LABELS),
        index=list(PROVIDER_LABELS).index(active), format_func=PROVIDER_LABELS.get, key="settings_provider")
    try:
        config = load_config(provider)
    except (ValueError, OSError):
        status.error("Es ist keine gültige Verbindung vorhanden.")
        st.error("Die lokale Konfiguration konnte nicht gelesen werden.")
        return
    ok, detail = connection_status(config)
    if ok:
        status.success("Eine gültige Verbindung ist vorhanden.")
    else:
        status.error("Es ist keine gültige Verbindung vorhanden.")
    st.caption(detail + " Prüfung ohne Textgenerierung; Ergebnis für fünf Minuten gespeichert.")
    if st.session_state.pop("clear_saved_api_input", None) == provider:
        st.session_state[f"api_key_{provider}"] = ""
    if message := st.session_state.pop("settings_saved_message", None):
        st.success(message)
    with st.form(f"llm_settings_{provider}"):
        api_key = st.text_input("API-Key", type="password", value="", key=f"api_key_{provider}")
        model = st.text_input("KI-Modell", value=config.model or DEFAULT_MODELS[provider])
        st.caption("Ein leeres Schlüssel-Feld behält den vorhandenen API-Key. Schlüssel werden lokal gespeichert.")
        test = st.form_submit_button("Verbindung testen")
        save = st.form_submit_button("Speichern")
    if test or save:
        try:
            if test:
                _cached_status.clear()
                ok, detail = connection_status(LLMConfig(provider, model.strip(), api_key.strip() or config.api_key))
                (status.success if ok else status.error)("Eine gültige Verbindung ist vorhanden." if ok
                                                        else "Es ist keine gültige Verbindung vorhanden.")
                st.caption(detail)
            if save:
                # Existing internal embedding configuration remains intact.
                save_config(provider, model, api_key)
                st.session_state.clear_saved_api_input = provider
                st.session_state.settings_saved_message = "Einstellungen wurden lokal gespeichert."
                st.rerun()
        except (ValueError, OSError) as error:
            st.error(str(error) or "Die Einstellungen konnten nicht gespeichert werden.")
