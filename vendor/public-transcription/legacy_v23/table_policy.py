"""Declared table serialization policies; no content repair or grid completion."""
import re
POLICY='TABLE_LAYOUT_V1'
INSTRUCTION=(' Use table serialization policy TABLE_LAYOUT_V1. Omit layout-only '
    'rows that contain no td or th cells. Preserve every real cell, including '
    'empty cells, spans, all values, currencies and decimal points. Runs of six '
    'or more whitespace-separated dots identified as typographic leaders are '
    'layout and omitted from cell text. Three-dot ellipses remain content. '
    'An empty header stub occupies its actual grid slots; splitting or merging '
    'jointly empty slots does not move any content cell.')


def normalize_table_layout(html):
    # Never remove rows with even one actual td/th, including empty cells.
    html=re.sub(r'<tr\b[^>]*>\s*</tr\s*>','',html,flags=re.IGNORECASE)
    parts=re.split(r'(<[^>]*>)',html)
    for i in range(0,len(parts),2):
        parts[i]=re.sub(r'(?<![\d.])\.(?:[ \t]+\.){5,}(?![\d.])','',parts[i])
    return ''.join(parts)
