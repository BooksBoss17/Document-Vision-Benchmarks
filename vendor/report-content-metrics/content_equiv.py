"""Content characters, independent of transport/style; no algebraic rewriting."""
from __future__ import annotations
import html, importlib.util, pathlib, re, unicodedata
from html.parser import HTMLParser

VERSION='CONTENT_CHAR_EQUIV_V1'
LAYOUT_PATH=pathlib.Path(__file__).resolve().with_name('latex_layout.py')
spec=importlib.util.spec_from_file_location('content_existing_layout',LAYOUT_PATH)
layout=importlib.util.module_from_spec(spec);spec.loader.exec_module(layout)
ZERO='\ue000'
GREEK=dict(zip('alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega'.split(),'αβγδεζηθικλμνξοπρστυφχψω'))
GREEK.update({k.capitalize():v.upper() for k,v in list(GREEK.items())})
GREEK.update(vartheta='θ',varphi='φ',varepsilon='ε',varrho='ρ',varpi='π',varsigma='σ')
SYMBOLS={'\\'+k:v for k,v in GREEK.items()}
SYMBOLS.update({'\\'+k:v for k,v in dict(times='×',cdot='·',pm='±',mp='∓',leq='≤',geq='≥',neq='≠',partial='∂',infty='∞',sum='∑',prod='∏',int='∫',iint='∬',oint='∮',rightarrow='→',leftarrow='←',leftrightarrow='↔',Rightarrow='⇒',Leftarrow='⇐',Leftrightarrow='⇔',approx='≈',sim='~',simeq='≃',equiv='≡',propto='∝',parallel='∥',perp='⊥',bot='⊥',angle='∠',triangle='△',square='□',circ='∘',forall='∀',exists='∃',emptyset='∅',varnothing='∅',cap='∩',cup='∪',subset='⊂',supset='⊃',subseteq='⊆',supseteq='⊇',wedge='∧',vee='∨',neg='¬',therefore='∴',because='∵',ldots='…',cdots='…',vdots='⋮',ddots='⋱',prime='′',langle='⟨',rangle='⟩',Vert='‖',mid='|',div='÷',nabla='∇',mapsto='↦',uparrow='↑',downarrow='↓',otimes='⊗',oplus='⊕',hbar='ℏ',ell='ℓ').items()})
PUNCT=str.maketrans({'。':'.','“':'"','”':'"','‘':"'",'’':"'",'−':'-','•':'·','ϕ':'φ','ϑ':'θ','ϵ':'ε'})

def group(text,start,left='{',right='}'):
    if start>=len(text) or text[start]!=left:return None,start
    depth=0
    for i in range(start,len(text)):
        if text[i]=='\\':continue
        if text[i]==left:depth+=1
        elif text[i]==right:
            depth-=1
            if not depth:return text[start+1:i],i+1
    return None,start

def tikz_labels(text):
    """Only explicit visible node/axis labels; never drawing coordinates/code."""
    labels=[]
    for m in re.finditer(r'(?:\\node\b|\bnode\b)',text):
        i=m.end()
        while i<len(text):
            if text[i] in '[(':
                _,j=group(text,i,text[i],']' if text[i]=='[' else ')')
                if j==i:break
                i=j;continue
            if text[i]=='{':
                body,j=group(text,i)
                if body is not None:labels.append((m.start(),body))
                break
            if text[i]==';':break
            i+=1
    for m in re.finditer(r'\b(?:xlabel|ylabel|zlabel|title|legend entries|[xyz]ticklabels)\s*=\s*',text):
        body,j=group(text,m.end())
        if body is not None:labels.append((m.start(),body))
    return '\n'.join(value for _,value in sorted(set(labels)))

def chart_labels(text):
    """Native chart serialization: explicit labels, not inferred plot data."""
    parts=[];metadata=set()
    for line in text.splitlines():
        axis=re.match(r'\s*axis\[[^]]+\]:\s*(.*)',line)
        bind=re.match(r'\s*bind:\s*([^;]+)',line)
        if axis:
            value=axis[1].strip();parts.append(value);metadata.add(value);continue
        if bind:
            value=bind[1].strip()
            if value not in {'value','category'}:parts.append(value);metadata.add(value)
            continue
        if line.strip().startswith('|'):
            cells=[c.strip() for c in line.strip().strip('|').split('|')]
            for cell in cells:
                if not cell or cell in metadata or re.fullmatch(r':?-+:?|category|value|column_\d+',cell):continue
                if re.fullmatch(r'[-+]?\d+(?:\.\d+)?',cell):continue
                parts.append(cell)
    return '\n'.join(parts)

def latex_tables(text):
    """Drop table/align separators only inside their declared environment."""
    pattern=r'\\begin\s*\{(tabular|array)\}\s*\{[^{}]*\}(.*?)(?:\\end\s*\{\1\}|$)'
    def replace(m):
        body=re.sub(r'(?<!\\)&','\n',m[2])
        body=re.sub(r'\\\\(?:\[[^\]]*\])?','\n',body)
        return body
    return re.sub(pattern,replace,text,flags=re.S)

class VisibleHTML(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.parts=[]
    def handle_starttag(self,tag,attrs):
        if tag in {'sub','sup'}:self.parts.append('_'+'{' if tag=='sub' else '^'+'{')
        elif tag in {'br','p','tr','td','th','div','li'}:self.parts.append('\n')
        elif tag not in {'img','span','b','i','u','em','strong','table','thead','tbody','chart','ul','ol','a','s'}:self.parts.append(self.get_starttag_text())
    def handle_endtag(self,tag):
        if tag in {'sub','sup'}:self.parts.append('}')
        elif tag in {'p','tr','td','th','div','li'}:self.parts.append('\n')
        elif tag not in {'img','span','b','i','u','em','strong','table','thead','tbody','chart','ul','ol','a','s','br'}:self.parts.append('</'+tag+'>')
    def handle_data(self,data):self.parts.append(data)

def unicode_scripts(text):
    out=[];i=0
    while i<len(text):
        ch=text[i];name=unicodedata.name(ch,'')
        kind='_' if 'SUBSCRIPT' in name else '^' if 'SUPERSCRIPT' in name else None
        if kind:
            chars=[]
            while i<len(text):
                name=unicodedata.name(text[i],'');k='_' if 'SUBSCRIPT' in name else '^' if 'SUPERSCRIPT' in name else None
                if k!=kind:break
                chars.append(unicodedata.normalize('NFKC',text[i]));i+=1
            out.append(kind+'{'+''.join(chars)+'}');continue
        if ch=='°':out.append(r'^{\circ}')
        else:out.append(ch)
        i+=1
    return ''.join(out)

def preprocess(text):
    warnings=[]
    if re.search(r'\\begin\s*\{(?:tikzpicture|axis)\}',text):
        text=tikz_labels(text);warnings.append('DRAWING_EXPLICIT_LABELS_ONLY')
    if '<chart' in text:
        text=re.sub(r'<chart\b[^>]*>(.*?)</chart>',lambda m:chart_labels(m[1]),text,flags=re.S)
        warnings.append('CHART_EXPLICIT_LABELS_ONLY_COORDINATES_EXCLUDED')
    text=latex_tables(text)
    # Markdown emphasis is removed before HTML becomes a TeX script.
    text=re.sub(r'(?<!\w)_([^\W\d_]+)_(?!\w)',r'\1',text)
    text=re.sub(r'!\[[^\]]*\]\([^)]*\)','',text)
    parser=VisibleHTML();parser.feed(text);parser.close();text=''.join(parser.parts)
    text=re.sub(r'^\s*```[^\n]*\n|\n\s*```\s*$','',text)
    text=re.sub(r'(?m)^\s{0,3}#{1,6}\s+','',text)
    text=re.sub(r'\*\*(.*?)\*\*',r'\1',text,flags=re.S)
    text=re.sub(r'`([^`]+)`',r'\1',text)
    text=unicode_scripts(text)
    text=unicodedata.normalize('NFKC',text).translate(PUNCT)
    text=re.sub(r'(?<=\d)\s*~\s*(?=\d)',r'\\sim ',text)
    text=text.replace(r'\(', '').replace(r'\)','').replace(r'\[','').replace(r'\]','')
    text=re.sub(r'(?<!\\)\$', '',text)
    text=re.sub(r'\\(?:displaystyle|textstyle|scriptstyle|scriptscriptstyle|hline)\b','',text)
    text=re.sub(r'\\(?:boxed|fbox|mathop|underline)\b',r'\\mathrm',text)
    text=re.sub(r'\\bullet\b',r'\\cdot',text)
    text=re.sub(r'\\begin\s*\{(?:array|tabular)\}\s*\{[^{}]*\}','',text)
    text=re.sub(r'\\(?:begin|end)\s*\{(?:aligned|align\*?|gathered|gather\*?|array|tabular|equation\*?|matrix|pmatrix|bmatrix|cases)\}','',text)
    text=re.sub(r'\\\\(?:\[[^\]]*\])?','\n',text)
    text=re.sub(r'\\tag\s*\{([^{}]*)\}',r'(\1)',text)
    text=re.sub(r'\{(?:\s|\\quad\b|\\qquad\b|\\enspace\b|\\[,;! ])*\}',lambda m:'{'+ZERO+'}',text)
    if r'\left' in text or r'\right' in text:text=re.sub(r'\\\s*$','',text)
    text=re.sub(r'(?<!\\)&',r'\\&',text)
    # A standalone script glyph has no visible base; keep its script role.
    text=re.sub(r'^(\s*)(?=[_^])',lambda m:m.group(1)+ZERO,text)
    return text,warnings

def flatten(ast,role=()):
    kind=ast[0]
    if kind=='seq':return [u for child in ast[1:] for u in flatten(child,role)]
    if kind in {'font'}:return flatten(ast[2],role)
    if kind in {'atom','number','operatorname','text'}:
        value=ast[-1]
        if kind=='atom':
            value=SYMBOLS.get(value,value)
            if value.startswith('\\') and value[1:] in {'sin','cos','tan','cot','sec','csc','log','ln','lim','min','max','exp','det','gcd','arcsin','arccos','arctan','sup','inf'}:value=value[1:]
            elif value.startswith('\\') and len(value)==2:value=value[1:]
        return [(ch,role) for ch in value.translate(PUNCT) if not ch.isspace() and ch!=ZERO]
    if kind=='scripts':
        return flatten(ast[1],role)+(flatten(ast[2],role+('sub',)) if ast[2] else [])+(flatten(ast[3],role+('sup',)) if ast[3] else [])
    if kind=='frac':return flatten(ast[1],role+('numerator',))+flatten(ast[2],role+('denominator',))
    if kind=='sqrt':return [('√',role)]+(flatten(ast[1],role+('root-degree',)) if ast[1] else [])+flatten(ast[2],role+('radicand',))
    # Visible accents, fractions and ordered constructs stay distinct.
    return [(f'<{kind}>',role)]+[u for i,child in enumerate(ast[1:]) if child is not None for u in flatten(child,role+(f'{kind}:{i}',))]

def normalize(text):
    prepared,warnings=preprocess(text)
    if not prepared.strip():return dict(units=[],supported=True,warnings=warnings,prepared=prepared)
    parsed=layout.canonical_latex(prepared)
    if parsed['supported']:units=flatten(parsed['ast'])
    else:
        # Fail conservatively: retain unsupported content instead of deleting it.
        warnings.append('UNSUPPORTED_SYNTAX:'+parsed['reason'])
        units=[(ch,()) for ch in prepared if not ch.isspace()]
    return dict(units=units,supported=parsed['supported'],warnings=warnings,prepared=prepared)

def alignment(reference,prediction):
    """Exact Levenshtein alignment; maximize matches among min-edit paths."""
    n,m=len(reference),len(prediction);old=[(j,0,0,0,j) for j in range(m+1)]
    for i,a in enumerate(reference,1):
        new=[(i,0,0,i,0)]
        for j,b in enumerate(prediction,1):
            d,c,s,dele,ins=old[j-1];diag=(d+(a!=b),c+(a==b),s+(a!=b),dele,ins)
            d,c,s,dele,ins=old[j];delete=(d+1,c,s,dele+1,ins)
            d,c,s,dele,ins=new[-1];insert=(d+1,c,s,dele,ins+1)
            new.append(min((diag,delete,insert),key=lambda x:(x[0],-x[1],x[2])))
        old=new
    d,c,s,dele,ins=old[-1]
    assert c+s+dele==n and c+s+ins==m and s+dele+ins==d
    return dict(reference_units=n,predicted_units=m,correct=c,substitutions=s,deletions=dele,insertions=ins,edits=d)
