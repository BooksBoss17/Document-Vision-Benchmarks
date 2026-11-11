"""Bounded conservative TeX layout AST, not algebra or general TeX equivalence.

Only whitelisted command arities/aliases are normalized. Unsupported syntax is
an explicit coverage gap; no macro expansion, arity guessing or TeX execution.
"""
import re
import unicodedata

MAX_CHARACTERS = 32768
MAX_TOKENS = 16384
MAX_DEPTH = 64
MAX_NODES = 8192
RULE_VERSION = 'known-tex-layout-v1'


class Unsupported(ValueError):
    pass


ALIASES = {r'\dfrac': r'\frac', r'\tfrac': r'\frac', r'\le': r'\leq',
           r'\ge': r'\geq', r'\ne': r'\neq', r'\to': r'\rightarrow',
           r'\gets': r'\leftarrow', r'\lnot': r'\neg',
           r'\lvert': '|', r'\rvert': '|', r'\vert': '|',
           r'\lVert': r'\Vert', r'\rVert': r'\Vert',
           r'\|': r'\Vert',
           r'\lbrace': r'\{', r'\rbrace': r'\}',
           r'\lparen': '(', r'\rparen': ')', r'\lbrack': '[', r'\rbrack': ']'}
UNICODE = dict(zip('αβγδθλμπστωΓΔΩ', [
    r'\alpha', r'\beta', r'\gamma', r'\delta', r'\theta', r'\lambda', r'\mu',
    r'\pi', r'\sigma', r'\tau', r'\omega', r'\Gamma', r'\Delta', r'\Omega']))
UNICODE.update({'×': r'\times', '·': r'\cdot', '±': r'\pm', '∓': r'\mp',
                '≤': r'\leq', '≥': r'\geq', '≠': r'\neq', '∈': r'\in',
                '∂': r'\partial', '∞': r'\infty', '∑': r'\sum', '∫': r'\int',
                '−': '-', '→': r'\rightarrow', '←': r'\leftarrow', '÷': r'\div',
                '∇': r'\nabla', '⊗': r'\otimes', '⊕': r'\oplus', 'ℏ': r'\hbar',
                '↦': r'\mapsto', '↑': r'\uparrow', '↓': r'\downarrow'})
SYMBOLS = set(r'''\alpha \beta \gamma \delta \epsilon \varepsilon \zeta \eta
\theta \vartheta \iota \kappa \lambda \mu \nu \xi \pi \varpi \rho \varrho
\sigma \varsigma \tau \upsilon \phi \varphi \chi \psi \omega
\Gamma \Delta \Theta \Lambda \Xi \Pi \Sigma \Upsilon \Phi \Psi \Omega
\times \cdot \pm \mp \leq \geq \neq \in \notin \partial \infty
\sum \prod \int \iint \oint \lim \sin \cos \tan \cot \sec \csc \log \ln
\arcsin \arccos \arctan \exp \min \max \sup \inf \det \gcd
\rightarrow \leftarrow \leftrightarrow \Rightarrow \Leftarrow \Leftrightarrow
\approx \sim \simeq \equiv \cong \propto \parallel \perp \bot
\angle \triangle \square \circ \forall \exists \emptyset
\cap \cup \subset \supset \subseteq \supseteq \wedge \vee \neg
\therefore \because \ldots \cdots \vdots \ddots \prime
\langle \rangle \Vert \{ \} \% \# \_ \$ \&'''.split())
SYMBOLS.update(r'''\mid \nmid \div \nabla \mapsto \uparrow \downarrow \updownarrow
\Uparrow \Downarrow \Updownarrow \otimes \oplus \ominus \odot \oslash
\setminus \top \hbar \ell \aleph \imath \jmath \Re \Im \succeq \preceq
\succ \prec \lesssim \gtrsim \leqslant \geqslant \varnothing'''.split())
SPACING = set(r'\quad \qquad \enspace \, \; \: \!'.split()) | {'\\ ', '~'}
# Style declarations can change limit placement and remain coverage gaps.
FONTS = set(r'\mathrm \mathbf \mathit \mathsf \mathtt \mathcal \mathbb \mathfrak \boldsymbol'.split())
TEXT = set(r'\text \textrm \textbf \textit \mbox'.split())
UNARY = set(r'\overline \underline \bar \hat \widehat \tilde \widetilde \vec \dot \ddot'.split())
UNARY.update(r'\overrightarrow \overleftarrow \overleftrightarrow \check \breve \acute \grave \mathring'.split())
BINARY = set(r'\frac \binom \overset \underset \stackrel'.split())
DELIMS = {'(', ')', '[', ']', '|', '.', r'\{', r'\}', r'\langle', r'\rangle', r'\Vert'}
SIZING = set(r'\big \Big \bigg \Bigg \bigl \bigr \Bigl \Bigr \biggl \biggr \Biggl \Biggr'.split())
COMPOUND = FONTS | TEXT | UNARY | BINARY | SIZING | {r'\sqrt', r'\operatorname', r'\left', r'\right'}
# Control words consume ALL ASCII letters, preventing HME command merging.
TOKEN = re.compile(r'\\[A-Za-z]+|\\.|\s+|.', re.S)


def alias(token):
    token = UNICODE.get(token, token)
    return ALIASES.get(token, token)


def sequence(items):
    flat = []
    for item in items:
        flat.extend(item[1:] if item[0] == 'seq' else (item,))
    # Coalesce digits only AFTER TeX arity/script parsing. Never lex 12 as one
    # token: \\frac12 and x^12 have different argument boundaries.
    merged = []
    for item in flat:
        if merged and item[0] == merged[-1][0] == 'number':
            merged[-1] = ('number', merged[-1][1] + item[1])
        else:
            merged.append(item)
    return merged[0] if len(merged) == 1 else ('seq', *merged)


class Parser:
    def __init__(self, text):
        if len(text) > MAX_CHARACTERS:
            raise Unsupported('character resource limit')
        text = unicodedata.normalize('NFC', text).strip()
        for left, right in [('$$', '$$'), (r'\[', r'\]'), (r'\(', r'\)'), ('$', '$')]:
            if text.startswith(left) and text.endswith(right) and len(text) >= len(left) + len(right):
                text = text[len(left):-len(right)].strip()
                break
        self.tokens = TOKEN.findall(text)
        if len(self.tokens) > MAX_TOKENS:
            raise Unsupported('token resource limit')
        self.i = self.nodes = 0

    def depth_check(self, depth):
        if depth > MAX_DEPTH:
            raise Unsupported('nesting resource limit')

    def node(self, *parts):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise Unsupported('node resource limit')
        return tuple(parts)

    def peek(self, skip_spacing_commands=True):
        while self.i < len(self.tokens) and (self.tokens[self.i].isspace() or
                (skip_spacing_commands and self.tokens[self.i] in SPACING)):
            self.i += 1
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def pop(self, skip_spacing_commands=True):
        token = self.peek(skip_spacing_commands)
        if token is None:
            raise Unsupported('missing TeX argument')
        self.i += 1
        return token

    def expression(self, stop=None, depth=0):
        self.depth_check(depth)
        items = []
        while self.peek() is not None and self.peek() != stop:
            if self.peek() in {'}', r'\right'}:
                raise Unsupported('unexpected closing group/delimiter')
            node = self.atom(depth)
            if node[0] == 'sized':
                # Sizing is presentation only. Scripts belong to the visible
                # closing delimiter, just as in an ordinary (x)^2 spelling.
                _, content, visible_right = node
                if not visible_right and self.peek() in ('_', '^'):
                    raise Unsupported('script on invisible right delimiter')
                parts = list(content[1:]) if content[0] == 'seq' else [content]
                items.extend(parts[:-1])
                node = parts[-1]
            sub = sup = None
            while self.peek() in ('_', '^'):
                op = self.pop()
                if (op == '_' and sub is not None) or (op == '^' and sup is not None):
                    raise Unsupported('double subscript' if op == '_' else 'double superscript')
                arg = self.argument(depth + 1)
                if op == '_':
                    sub = arg
                else:
                    sup = arg
            if sub is not None or sup is not None:
                node = self.node('scripts', node, sub, sup)
            items.append(node)
        if stop is not None:
            if self.peek() != stop:
                raise Unsupported('unclosed TeX group/delimiter')
            self.pop()
        if not items:
            raise Unsupported('empty formula/group')
        return sequence(items)

    def argument(self, depth):
        # TeX scans unbraced macro arguments as single tokens, not recursively.
        # Accept zero-arity tokens or a braced expression; fail closed otherwise.
        self.depth_check(depth)
        token = self.peek(skip_spacing_commands=False)
        if token in SPACING:
            raise Unsupported('spacing command used as an unbraced TeX argument')
        if token is not None and alias(token) in COMPOUND:
            raise Unsupported('unbraced compound TeX argument ' + token)
        return self.atom(depth)

    def text_argument(self, depth):
        self.depth_check(depth)
        if self.pop(skip_spacing_commands=False) != '{':
            raise Unsupported('text requires a braced argument')
        nesting, parts = 1, []
        while self.i < len(self.tokens):
            token = self.tokens[self.i]
            self.i += 1
            if token == '{':
                nesting += 1
                self.depth_check(depth + nesting)
            elif token == '}':
                nesting -= 1
                if nesting == 0:
                    result = re.sub(r'\s+', ' ', ''.join(parts))
                    if not result.strip():
                        raise Unsupported('empty text argument')
                    return result
            elif token in {r'\%', r'\#', r'\_', r'\$', r'\&', r'\{', r'\}'}:
                parts.append(token[1:])
            elif token.startswith('\\') or token in '$^_%&#~':
                raise Unsupported('complex text-mode construct')
            else:
                parts.append(token)
        raise Unsupported('unclosed text argument')

    def delimiter(self, invisible=False):
        delimiter = alias(self.pop(skip_spacing_commands=False))
        if delimiter not in DELIMS or (delimiter == '.' and not invisible):
            raise Unsupported('unknown sized delimiter')
        return None if delimiter == '.' else self.node('atom', delimiter)

    def atom(self, depth=0):
        self.depth_check(depth)
        token = alias(self.pop())
        if token == '{':
            return self.expression('}', depth + 1)
        if token == "'":
            raise Unsupported('active prime shorthand unsupported')
        if token in ('}', '^', '_', '&', '#', '$', '%', '~'):
            raise Unsupported('unsupported or misplaced TeX syntax ' + token)
        if token == r'\left':
            left = self.delimiter(invisible=True)
            inner = self.expression(r'\right', depth + 1)
            right = self.delimiter(invisible=True)
            return self.node('sized', sequence([part for part in (left, inner, right) if part is not None]), right is not None)
        if token == r'\right':
            raise Unsupported('unmatched right delimiter')
        if token in SIZING:
            return self.delimiter()
        if token == r'\sqrt':
            degree = None
            if self.peek(skip_spacing_commands=False) == '[':
                self.pop()
                degree = self.expression(']', depth + 1)
            return self.node('sqrt', degree, self.argument(depth + 1))
        if token in BINARY:
            return self.node(token[1:], self.argument(depth + 1), self.argument(depth + 1))
        if token in UNARY:
            return self.node(token[1:], self.argument(depth + 1))
        if token in FONTS:
            return self.node('font', token, self.argument(depth + 1))
        if token in TEXT:
            return self.node('text', token, self.text_argument(depth + 1))
        if token == r'\operatorname':
            name = self.text_argument(depth + 1)
            command = '\\' + name
            # Limit-bearing operators such as lim/min are deliberately excluded.
            operators = {r'\sin', r'\cos', r'\tan', r'\cot', r'\sec', r'\csc', r'\log', r'\ln',
                         r'\arcsin', r'\arccos', r'\arctan', r'\exp', r'\det', r'\gcd'}
            return self.node('atom', command) if command in operators else self.node('operatorname', name)
        if token in SYMBOLS:
            return self.node('atom', token)
        if token.startswith('\\'):
            raise Unsupported('unknown arity/control word ' + token)
        if len(token) == 1 and '0' <= token <= '9':
            return self.node('number', token)
        return self.node('atom', token)

    def parse(self):
        return self.expression()


def layout_signature(ast):
    if ast is None:
        return None
    kind = ast[0]
    if kind in {'atom', 'number', 'operatorname'}:
        return (kind,)
    if kind == 'text':
        return (kind, ast[1])
    if kind == 'font':
        return (kind, ast[1], layout_signature(ast[2]))
    return (kind, *(layout_signature(child) for child in ast[1:]))


def canonical_latex(text):
    if not isinstance(text, str):
        raise TypeError('formula must be text')
    try:
        ast = Parser(text).parse()
        return dict(supported=True, ast=ast, layout=layout_signature(ast), reason=None)
    except (Unsupported, RecursionError) as exc:
        return dict(supported=False, ast=None, layout=None, reason=str(exc) or 'recursion resource limit')


def compare_latex_layout(reference, prediction):
    a, b = canonical_latex(reference), canonical_latex(prediction)
    both = a['supported'] and b['supported']
    # Reference support alone controls applicability; unsupported predictions fail.
    return dict(reference_supported=a['supported'], prediction_supported=b['supported'],
                comparison_supported=both,
                normalized_expression_exact=(both and a['ast'] == b['ast']) if a['supported'] else None,
                layout_structure_exact=(both and a['layout'] == b['layout']) if a['supported'] else None,
                reference_reason=a['reason'], prediction_reason=b['reason'], rule_version=RULE_VERSION,
                scope='Known TeX arities/aliases and layout only; no algebra or general TeX equivalence.')
