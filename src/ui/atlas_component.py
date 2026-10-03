"""Small local component bridging PyVis clicks to persisted concept/edge editors."""
from pathlib import Path
import streamlit.components.v1 as components

_atlas = components.declare_component("knowledge_atlas", path=str(Path(__file__).with_name("atlas_component")))

def atlas_graph(html, height=930, key="atlas"):
    return _atlas(html=html, height=height, default=None, key=key)
