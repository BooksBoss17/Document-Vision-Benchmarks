"""Token-safe narrow formatting and bounded-memory exact Unicode edit distance."""
import sys
sys.dont_write_bytecode=True
import re,unicodedata
def narrow(text):
    text=unicodedata.normalize('NFC',text)
    text=re.sub(r'\\(?:left|right)(?![A-Za-z])','',text)
    text=re.sub(r'\\[,;! ]','',text)
    text=text.replace('\\(','').replace('\\)','').replace('\\[','').replace('\\]','').replace('$','')
    return re.sub(r'\s+','',text)
def distance(a,b):
    if len(a)>len(b):a,b=b,a
    if not a:return len(b)
    peq={}
    for i,c in enumerate(a):peq[c]=peq.get(c,0)|(1<<i)
    pv=~0;mv=0;score=len(a);high=1<<(len(a)-1)
    for c in b:
        eq=peq.get(c,0);xv=eq|mv;xh=(((eq&pv)+pv)^pv)|eq;ph=mv|~(xh|pv);mh=pv&xh
        if ph&high:score+=1
        if mh&high:score-=1
        ph=(ph<<1)|1;mh<<=1;pv=mh|~(xv|ph);mv=ph&xv
    return score
