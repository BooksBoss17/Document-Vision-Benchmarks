"""Frozen V3+table content metric; pure functions extracted from final C06.

Only the run-specific orchestration and developer paths are omitted. Numeric
functions are preserved verbatim; math roles and lexical limits are unchanged.
"""
import collections,re,unicodedata
import numpy as np
import content_equiv as legacy

def alignment(a,b):
    """Same min edits / max correct objective as legacy DP; vectorized exact prefix scan.

    Encode each DP state as edits*B-correct, B=min(n,m)+1. Left-edge insertion
    recurrence is a prefix minimum of (candidate-j*B), so NumPy does it in C.
    Counts S/D/I follow uniquely from distance, correct, and the two lengths.
    """
    n0,m0 = len(a),len(b)
    prefix = 0
    while prefix < min(len(a),len(b)) and a[prefix] == b[prefix]:
        prefix += 1
    a,b = a[prefix:],b[prefix:]
    suffix = 0
    while suffix < min(len(a),len(b)) and a[-suffix-1] == b[-suffix-1]:
        suffix += 1
    if suffix:
        a,b = a[:-suffix],b[:-suffix]
    if not a or not b:
        d,c = len(a)+len(b),prefix+suffix
    else:
        if len(a) < len(b):
            a,b = b,a
        ids = {}
        def encode(xs):
            return np.asarray([ids.setdefault((x[0],tuple(x[1])),len(ids)) for x in xs],dtype=np.int64)
        aa,bb = encode(a),encode(b)
        n,m = len(a),len(b)
        base = min(n,m)+1
        offsets = np.arange(m+1,dtype=np.int64)*base
        old = offsets.copy()
        candidate = np.empty(m+1,dtype=np.int64)
        for i,symbol in enumerate(aa,1):
            candidate[0] = i*base
            candidate[1:] = np.minimum(old[1:]+base, old[:-1]+np.where(bb==symbol,-1,base))
            old = np.minimum.accumulate(candidate-offsets)+offsets
        score = int(old[-1])
        d = (score+base-1)//base
        c = d*base-score+prefix+suffix
    s = n0+m0-2*c-d
    dele,ins = n0-c-s,m0-c-s
    assert min(c,s,dele,ins) >= 0 and c+s+dele==n0 and c+s+ins==m0 and s+dele+ins==d
    return dict(reference_units=n0,predicted_units=m0,correct=c,substitutions=s,deletions=dele,insertions=ins,edits=d)

def markdown_cells(line):
    text=line.strip()
    if text.startswith("|"):text=text[1:]
    if text.endswith("|") and not text.endswith(r"\|"):text=text[:-1]
    cells=[[]];i=0;close=None
    while i<len(text):
        if close:
            if text.startswith(close,i):
                cells[-1].append(close);i+=len(close);close=None
            else:
                cells[-1].append(text[i]);i+=1
            continue
        ch=text[i]
        if ch=="\\" and i+1<len(text):
            opening=text[i:i+2]
            if opening in (r"\(",r"\["):
                ending=r"\)" if opening==r"\(" else r"\]"
                if text.find(ending,i+2)>=0:close=ending
            cells[-1].append(opening);i+=2
        elif ch in ("$","`"):
            count=1
            while i+count<len(text) and text[i+count]==ch:count+=1
            token=ch*count
            if text.find(token,i+count)>=0:close=token
            cells[-1].append(token);i+=count
        elif ch=="|":cells.append([]);i+=1
        else:cells[-1].append(ch);i+=1
    return "\n".join("".join(x) for x in cells)

def visible_table(text):
    """Remove proven table syntax only; keep all text/digits and mathematical bars."""
    text = re.sub(r"</?chart\b[^>]*>","",text,flags=re.I)
    lines = text.splitlines()
    divider = lambda line: bool(re.fullmatch(r"\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*",line))
    active = set()
    for i,line in enumerate(lines):
        if divider(line) and i and "|" in lines[i-1]:
            active.add(i-1)
            active.add(i)
            j=i+1
            while j<len(lines) and "|" in lines[j] and lines[j].strip():
                active.add(j)
                j+=1
    for i in active:
        if divider(lines[i]):
            lines[i]=""
        else:
            lines[i]=markdown_cells(lines[i])
    text="\n".join(lines)
    # Span counts/column specifications are layout, not visible cell data.
    def group(s,pos):
        while pos<len(s) and s[pos].isspace():
            pos+=1
        if pos>=len(s) or s[pos]!="{":
            return None,pos
        start=pos+1;depth=1;pos+=1
        while pos<len(s):
            if s[pos]=="{" and (pos==0 or s[pos-1]!="\\"):
                depth+=1
            elif s[pos]=="}" and s[pos-1]!="\\":
                depth-=1
                if not depth:
                    return s[start:pos],pos+1
            pos+=1
        return None,pos
    for _ in range(32):
        match=re.search(r"\\(?:multicolumn|multirow)\b(?:\[[^\]]*\])?",text)
        if not match:
            break
        pos=match.end();values=[]
        for k in range(3):
            value,pos=group(text,pos);values.append(value)
        if any(v is None for v in values):
            break  # Malformed structures stay conservative, never silently disappear.
        text=text[:match.start()]+" "+values[2]+" "+text[pos:]
    text=re.sub(r"\\(?:toprule|midrule|bottomrule)\b(?:\[[^\]]*\])?","",text)
    text=re.sub(r"\\(?:cline|cmidrule)(?:\([^)]*\))?\s*\{[\d-]+\}","",text)
    return text

GLYPH_ALIASES = {"⋅":"·", "∼":"~", "∣":"|", "⟂":"⊥", "⋯":"…", "ς":"σ"}

def fold_glyph_units(units):
    """Apply the input's compatibility policy also to glyphs expanded from TeX.

    Synthetic structural tokens remain atomic. Only scalar glyphs expand; every
    expanded glyph retains its original mathematical role.
    """
    out=[]
    for symbol,role in units:
        if len(symbol)!=1:
            out.append((symbol,role))
            continue
        value=unicodedata.normalize("NFKC",GLYPH_ALIASES.get(symbol,symbol)).translate(legacy.PUNCT)
        out.extend((ch,role) for ch in value if not ch.isspace())
    return out

def normalize(text,fmt):
    result=legacy.normalize(visible_table(text) if fmt=="TABLE_HTML" else text)
    result["units"]=fold_glyph_units(result["units"])
    return result

def summary(items):
    keys=["reference_units","predicted_units","correct","substitutions","deletions","insertions","edits"]
    totals={k:sum(x[k] for x in items) for k in keys}
    n,m,c=totals["reference_units"],totals["predicted_units"],totals["correct"]
    return dict(samples=len(items),**totals,precision=c/m if m else 0,recall=c/n if n else 0,
                F1=2*c/(n+m) if n+m else 0,CER=totals["edits"]/n if n else None,
                exact_content=sum(x["edits"]==0 for x in items),
                reference_supported=sum(x["reference_supported"] for x in items),
                prediction_supported=sum(x["prediction_supported"] for x in items),
                empty=sum(x["predicted_units"]==0 for x in items),truncated=sum(x["truncated"] for x in items))
