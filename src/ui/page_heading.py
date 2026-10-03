"""One navigation-owned heading per page, protected from browser translation."""
from html import escape
import streamlit as st


def page_heading(title):
    # Chrome translation can retain a heading from a reused Streamlit text node.
    # A page-specific container plus translate=no keeps the rendered title in sync.
    st.html('<h1 class="notranslate" translate="no" lang="de" '
            'style="font-size:2.75rem;font-weight:700;line-height:1.2;margin:0 0 1rem 0">'
            + escape(title) + '</h1>')


def protect_browser_translation():
    """Prevent translators from replacing React-owned text nodes on reruns."""
    st.html("""<script>
    (() => {
        document.documentElement.lang = "de";
        document.documentElement.setAttribute("translate", "no");
        document.documentElement.classList.add("notranslate");
        document.body.setAttribute("translate", "no");
        let meta = document.head.querySelector('meta[name="google"]');
        if (!meta) {
            meta = document.createElement("meta");
            meta.name = "google";
            document.head.appendChild(meta);
        }
        meta.content = "notranslate";
    })();
    </script>""", unsafe_allow_javascript=True)
