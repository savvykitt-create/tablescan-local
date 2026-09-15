"""Frozen expanded-audit numeric/structure parsing; no ground-truth access."""
import ast
import json
from decimal import Decimal, InvalidOperation
import re
from html.parser import HTMLParser
class _Cells(HTMLParser):
    def __init__(self):super().__init__();self.cells=[];self.current=None;self.invalid=False;self.rows=[];self.row=[];self.tag=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr':
            if self.row:self.rows.append(self.row)
            self.row=[]
        if tag in {'td','th'}:
            if self.current is not None:self.invalid=True
            self.current=[];self.tag=tag
        elif self.current is not None and tag not in {'span','b','i','strong','em','p','br','div'}:self.invalid=True
    def handle_data(self,data):
        if self.current is not None:self.current.append(data)
    def handle_endtag(self,tag):
        if tag in {'td','th'} and self.current is not None:
            value=' '.join(self.current).strip();self.cells.append(value);self.row.append((self.tag,value));self.current=None
        if tag=='tr' and self.row:self.rows.append(self.row);self.row=[]
def html_cells(text,expected=None):
    if not re.search(r'<(?:td|th)[\s>]',text,re.I):return None
    parser=_Cells();parser.feed(text)
    if parser.invalid:return None
    if expected is None:return parser.cells if parser.current is None else None
    rows=parser.rows+([parser.row] if parser.row else [])
    # DataFrame row-index TH cells are not measurement TD cells. A complete
    # header is usable for a one-row crop when the model puts the data in THs.
    for kind in ['td','th']:
        candidates=[]
        for row in rows:
            values=[v for tag,v in row if tag==kind]
            if kind=='th' and len(values)==expected+1 and values[0]=='':values=values[1:]
            if len(values)==expected and any(values) and values not in candidates:candidates.append(values)
        if len(candidates)==1:return candidates[0]
        if len(candidates)>1:return None
    if len(parser.cells)==expected and parser.current is None:return parser.cells
    return None
def unwrap(text):
    text=text.strip()
    if text.startswith('```') and text.endswith('```'):
        text=re.sub(r'^```(?:text|json)?\s*','',text)[:-3].strip()
    cells=html_cells(text,expected=1)
    if cells is not None and len(cells)==1:text=cells[0]
    for a,b in [('\\(','\\)'),('\\[','\\]')]:
        if text.startswith(a) and text.endswith(b):text=text[len(a):-len(b)].strip()
    return text.strip('|$`* ').strip()

def num(value):
    if value is None or isinstance(value,bool): return None
    value=str(value).strip().replace('−','-').replace(',','.')
    if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?',value):return None
    try:
        decimal=Decimal(value)
        return '0' if not decimal else format(decimal.normalize(),'f')
    except InvalidOperation:return None

def row_values(text,count):
    cells=html_cells(text,expected=count)
    if cells is not None:return [unwrap(x) for x in cells] if len(cells)==count else None
    text=unwrap(text)
    try:
        data=json.loads(text)
        if isinstance(data,list) and len(data)==count:return data
    except ValueError:
        # Some models emit Python-style lists with single quotes. Literal-only
        # parsing accepts their structure without executing model output.
        try:
            data = ast.literal_eval(text)
            if isinstance(data, list) and len(data) == count and all(
                    value is None or type(value) in (str, int, float) for value in data):
                return data
        except (ValueError, SyntaxError, TypeError, RecursionError):
            pass
    for token in ['$','\\(','\\)','\\[','\\]','`']:text=text.replace(token,' ')
    tokens=text.split()
    if len(tokens)==count and all(num(t) is not None for t in tokens):return tokens
    matches=list(re.finditer(r'[+\-−]?\d+(?:\s*[.,]\s*\d+)?',text))
    if len(matches)!=count:return None
    previous=0
    for m in matches:
        if re.fullmatch(r'[\s|&]*',text[previous:m.start()]) is None:return None
        previous=m.end()
    if re.fullmatch(r'[\s|&]*',text[previous:]) is None:return None
    return [re.sub(r'\s+','',m.group()) for m in matches]

def parse_table(text,rows,columns):
    text=text.strip()
    if text.startswith('```') and text.endswith('```'):text=re.sub(r'^```(?:json)?\s*','',text)[:-3].strip()
    try:data=json.loads(text)
    except ValueError:return {}
    if not isinstance(data,list):return {}
    parsed={};duplicates=set()
    for item in data:
        if not isinstance(item,dict):continue
        r=item.get('row');values=item.get('values')
        if type(r) is not int or not 1<=r<=rows:continue
        if r in parsed:duplicates.add(r)
        parsed[r]=values if isinstance(values,list) and len(values)==columns else None
    return {r:v for r,v in parsed.items() if r not in duplicates and v is not None}

