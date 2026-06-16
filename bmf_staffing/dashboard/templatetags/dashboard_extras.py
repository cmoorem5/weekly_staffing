"""Dashboard template helpers.

The KPI engine stores internal RAG states as ``Green`` / ``Yellow`` / ``Red``.
Per ``docs/report-generator-spec.md`` and ``.cursor/rules/report-generator.mdc``,
user-facing surfaces must never show those words. These helpers translate the
internal state into the approved labels (``On target`` / ``Monitor`` /
``Action needed``) and matching CSS classes for badges.
"""

from __future__ import annotations

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString

register = template.Library()

# internal RAG state -> (user-facing label, badge CSS modifier)
_RAG_MAP: dict[str, tuple[str, str]] = {
    "Green": ("On target", "bmf-rag--ontarget"),
    "Yellow": ("Monitor", "bmf-rag--monitor"),
    "Red": ("Action needed", "bmf-rag--action"),
}
_RAG_NONE = ("No data", "bmf-rag--none")


def _resolve(rag: object) -> tuple[str, str]:
    key = (str(rag).strip().title()) if rag is not None else ""
    return _RAG_MAP.get(key, _RAG_NONE)


@register.filter(name="rag_label")
def rag_label(rag: object) -> str:
    """Return the board-safe label for an internal RAG state."""
    return _resolve(rag)[0]


@register.simple_tag(name="rag_badge")
def rag_badge(rag: object) -> SafeString:
    """Render a status pill using the board-safe label and a semantic color."""
    label, css = _resolve(rag)
    return format_html('<span class="bmf-rag {}">{}</span>', css, label)
