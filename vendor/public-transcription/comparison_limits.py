"""Bounded scoring capacity. Capacity rejection is never silently a zero score."""
MAX_PREDICTION_CHARACTERS=131072
MAX_PREDICTION_VIEW_CHARACTERS=524288
MAX_PREDICTION_FILE_BYTES=134217728
MAX_PAIR_EDIT_WORK=8000000
MAX_TOTAL_EDIT_WORK=200000000
_REMAINING=MAX_TOTAL_EDIT_WORK

def reset_work():
    global _REMAINING
    _REMAINING=MAX_TOTAL_EDIT_WORK

def reserve_work(units):
    global _REMAINING
    if units>MAX_PAIR_EDIT_WORK or units>_REMAINING:
        raise ValueError('SCORING_CAPACITY_EXCEEDED: edit-distance work budget; no partial score published')
    _REMAINING-=units

def validate_text(text):
    if not isinstance(text,str):raise ValueError('prediction text must be string')
    if len(text)>MAX_PREDICTION_CHARACTERS:raise ValueError('SCORING_CAPACITY_EXCEEDED: prediction text length')
