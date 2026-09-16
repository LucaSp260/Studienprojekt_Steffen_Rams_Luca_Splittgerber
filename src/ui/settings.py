"""Lokale Anbieter-Konfiguration; API-Aufrufe nur durch den Test-Button."""
import streamlit as st

from src.llm.base_provider import LLMError
from src.llm.config import DEFAULT_MODELS, PROVIDER_LABELS, LLMConfig, load_config, save_config
from src.llm.llm_service import LLMService
from src.llm.config import load_embedding_config


def show_settings_page():
    st.subheader('Einstellungen')
    try:
        active = load_config().provider
    except (ValueError, OSError):
        active = 'openai'
    provider = st.selectbox('KI-Anbieter', list(PROVIDER_LABELS),
                            index=list(PROVIDER_LABELS).index(active),
                            format_func=PROVIDER_LABELS.get)
    try:
        config = load_config(provider)
        embedding_config = load_embedding_config(provider)
    except OSError:
        st.error('Die lokale Konfiguration konnte nicht gelesen werden. Bitte die Leserechte prüfen.')
        return
    st.caption('Bei der Verarbeitung wird der PDF-Text an den gewählten Anbieter gesendet. '
               'Verbindungstest und Verarbeitung können API-Kosten verursachen.')
    if config.api_key:
        st.info('Ein API-Key ist vorhanden. Das Feld leer lassen, um ihn weiterzuverwenden.')
    else:
        st.warning('Kein API-Key für diesen Anbieter konfiguriert.')
    if st.session_state.pop('clear_saved_api_input', None) == provider:
        st.session_state[f'api_key_{provider}'] = ''
    if message := st.session_state.pop('settings_saved_message', None):
        st.success(message)
    with st.form(f'llm_settings_{provider}'):
        api_key = st.text_input('API-Key', type='password', value='', key=f'api_key_{provider}')
        model = st.text_input('KI-Modell', value=config.model or DEFAULT_MODELS[provider])
        embedding_model = st.text_input('Embedding-Modell', value=embedding_config.model)
        st.caption('Das Embedding-Modell ist für die Suche, das LLM-Modell für Knowledge Extraction. '
                   'Nach einem Embedding-Modellwechsel vorhandene Themen für dieses Modell indexieren.')
        test = st.form_submit_button('Verbindung testen')
        save = st.form_submit_button('Speichern')
    if test or save:
        try:
            if test:
                with st.spinner('Verbindung wird getestet …'):
                    LLMService(LLMConfig(provider, model.strip(), api_key.strip() or config.api_key)).test_connection()
                st.success('Verbindung erfolgreich. Anbieter und Modell unterstützen strukturierte Antworten.')
            if save:
                save_config(provider, model, api_key, embedding_model)
                st.session_state.clear_saved_api_input = provider
                st.session_state.settings_saved_message = 'Einstellungen wurden lokal gespeichert.'
                st.rerun()
        except (LLMError, ValueError) as error:
            st.error(str(error))
        except OSError:
            st.error('Die Einstellungen konnten nicht gespeichert werden. Bitte Schreibrechte prüfen.')
