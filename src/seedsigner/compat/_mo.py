"""Pure-Python gettext ``.mo`` catalog reader + safe ``Plural-Forms`` evaluator.

CPython ships ``gettext``; stock MicroPython 1.27 does not, and its stdlib has no
``.mo`` reader. This module is the MicroPython text-translation core sitting behind
``compat.l10n``: it parses a GNU ``.mo`` catalog into an in-memory table and
evaluates the catalog's ``Plural-Forms`` rule to pick the correct plural form — the
two things ``gettext`` / ``ngettext`` need that MicroPython can't otherwise provide.

It is importable and unit-testable on **both** interpreters, so the parser and the
plural engine run in host CI. It depends only on ``int.from_bytes`` and basic
str/bytes ops — no ``struct``, no ``re``, no ``eval`` (all three are absent,
limited, or unsafe here).

Security posture: a ``.mo`` is untrusted input even on a signing device (it is an
SD-delivered asset, and defence-in-depth applies even once packs are individually
signed). Every offset and length is bounds-checked against the file size; the
plural expression is evaluated by a bounded recursive-descent parser — never
``eval`` / ``exec`` — with a capped length and parenthesis depth and the final index
clamped into ``[0, nplurals)``. Anything malformed raises :class:`MOError`; the
caller (``compat.l10n``) catches it and falls back to the English passthrough, so a
bad catalog can never crash a screen.

Semantics match CPython's ``gettext.GNUTranslations``: a singular entry is keyed by
its ``msgid`` string; a plural entry is keyed by ``(msgid, form_index)``.
"""

# GNU MO magic, read little-endian; the reversed value signals a big-endian file.
_MAGIC_LE = 0x950412de
_MAGIC_BE = 0xde120495

# Untrusted-input guards.
_MAX_STRINGS = 65536          # reject a catalog claiming more entries than this
_MAX_PLURAL_EXPR_LEN = 1000   # Plural-Forms expression length cap
_MAX_PAREN_DEPTH = 20         # parenthesis-nesting cap (real rules nest <= ~3)
_MAX_NPLURALS = 100           # sane upper bound for the header's nplurals
_DEFAULT_NPLURALS = 2         # gettext default when the header is missing/bad


class MOError(Exception):
    """Raised for any malformed/untrusted ``.mo`` content. The caller fails closed."""


def _default_plural(n):
    """The gettext default rule: form 0 for the singular, form 1 otherwise."""
    return 0 if n == 1 else 1


# ---------------------------------------------------------------------------
# Plural-Forms expression evaluator (recursive descent, no eval)
# ---------------------------------------------------------------------------
#
# The gettext plural rule is a C expression over the single non-negative variable
# ``n``. We support exactly the operators gettext uses, with C precedence
# (high -> low): unary ! ; * / % ; + - ; < > <= >= ; == != ; && ; || ; ?: .
# Each parse step returns a closure ``f(n) -> int`` so evaluation is a tree of real
# Python closures (never generated source).

def _safe_div(a, b):
    # n is non-negative and real divisors are positive literals, so floor division
    # matches C truncation here; guard division by zero so a hostile rule can't
    # raise inside a screen render.
    return 0 if b == 0 else a // b


def _safe_mod(a, b):
    return 0 if b == 0 else a % b


def _make_binop(op, l, r):
    """Return a closure for the binary ``op`` over sub-expression closures l, r.

    Comparisons and logical ops yield 0/1 (C booleans are ints), matching gettext.
    """
    if op == "||":
        return lambda n: 1 if (l(n) or r(n)) else 0
    if op == "&&":
        return lambda n: 1 if (l(n) and r(n)) else 0
    if op == "==":
        return lambda n: 1 if l(n) == r(n) else 0
    if op == "!=":
        return lambda n: 1 if l(n) != r(n) else 0
    if op == "<":
        return lambda n: 1 if l(n) < r(n) else 0
    if op == ">":
        return lambda n: 1 if l(n) > r(n) else 0
    if op == "<=":
        return lambda n: 1 if l(n) <= r(n) else 0
    if op == ">=":
        return lambda n: 1 if l(n) >= r(n) else 0
    if op == "+":
        return lambda n: l(n) + r(n)
    if op == "-":
        return lambda n: l(n) - r(n)
    if op == "*":
        return lambda n: l(n) * r(n)
    if op == "/":
        return lambda n: _safe_div(l(n), r(n))
    if op == "%":
        return lambda n: _safe_mod(l(n), r(n))
    raise MOError("bad operator")


def _tokenize(expr):
    """Tokenize a plural expression. Raise :class:`MOError` on any stray character."""
    if len(expr) > _MAX_PLURAL_EXPR_LEN:
        raise MOError("plural expression too long")
    tokens = []
    i = 0
    length = len(expr)
    two_char = ("&&", "||", "==", "!=", "<=", ">=")
    single = "?:()<>+-*/%!"
    while i < length:
        c = expr[i]
        if c == " " or c == "\t" or c == "\n" or c == "\r":
            i += 1
            continue
        if c.isdigit():
            j = i
            while j < length and expr[j].isdigit():
                j += 1
            tokens.append(("num", int(expr[i:j])))
            i = j
            continue
        if c == "n":
            tokens.append(("n", None))
            i += 1
            continue
        if expr[i:i + 2] in two_char:
            tokens.append(("op", expr[i:i + 2]))
            i += 2
            continue
        if c in single:
            tokens.append(("op", c))
            i += 1
            continue
        raise MOError("bad token")
    tokens.append(("end", None))
    return tokens


class _Parser:
    """Recursive-descent parser building a closure tree from plural tokens."""

    def __init__(self, tokens):
        self._toks = tokens
        self._pos = 0
        self._depth = 0

    def _peek(self):
        return self._toks[self._pos]

    def _expect_op(self, op):
        t = self._toks[self._pos]
        if t[0] == "op" and t[1] == op:
            self._pos += 1
            return
        raise MOError("expected '" + op + "'")

    def parse(self):
        f = self._ternary()
        if self._peek()[0] != "end":
            raise MOError("trailing tokens")
        return f

    def _ternary(self):
        cond = self._binary(self._logic_and, ("||",))
        t = self._peek()
        if t[0] == "op" and t[1] == "?":
            self._pos += 1
            a = self._ternary()
            self._expect_op(":")
            b = self._ternary()
            return lambda n: a(n) if cond(n) else b(n)
        return cond

    def _binary(self, sub, ops):
        left = sub()
        while True:
            t = self._peek()
            if t[0] == "op" and t[1] in ops:
                self._pos += 1
                right = sub()
                left = _make_binop(t[1], left, right)
            else:
                return left

    def _logic_and(self):
        return self._binary(self._equality, ("&&",))

    def _equality(self):
        return self._binary(self._relational, ("==", "!="))

    def _relational(self):
        return self._binary(self._additive, ("<", ">", "<=", ">="))

    def _additive(self):
        return self._binary(self._multiplicative, ("+", "-"))

    def _multiplicative(self):
        return self._binary(self._unary, ("*", "/", "%"))

    def _unary(self):
        t = self._peek()
        if t[0] == "op" and t[1] == "!":
            self._pos += 1
            operand = self._unary()
            return lambda n: 0 if operand(n) else 1
        return self._primary()

    def _primary(self):
        t = self._toks[self._pos]
        self._pos += 1
        if t[0] == "num":
            val = t[1]
            return lambda n: val
        if t[0] == "n":
            return lambda n: n
        if t[0] == "op" and t[1] == "(":
            self._depth += 1
            if self._depth > _MAX_PAREN_DEPTH:
                raise MOError("paren nesting too deep")
            inner = self._ternary()
            self._expect_op(")")
            self._depth -= 1
            return inner
        raise MOError("unexpected token")


def _compile_plural(expr, nplurals):
    """Compile a plural expression string into a clamped ``f(n) -> int``.

    Raises :class:`MOError` if the expression is malformed; callers fall back to
    the default rule. The returned function never raises and always yields an
    index in ``[0, nplurals)``.
    """
    raw = _Parser(_tokenize(expr)).parse()

    def plural(n):
        try:
            idx = raw(n)
        except Exception:
            return 0
        if idx is True:
            idx = 1
        elif idx is False:
            idx = 0
        if not isinstance(idx, int):
            return 0
        if idx < 0:
            return 0
        if idx >= nplurals:
            return nplurals - 1
        return idx

    return plural


def _parse_plural_forms(value):
    """Parse an ``nplurals=N; plural=EXPR;`` header value.

    Returns ``(nplurals, plural_func)``, defaulting either part on malformed input.
    """
    nplurals = _DEFAULT_NPLURALS
    expr = None
    for seg in value.split(";"):
        seg = seg.strip()
        if seg.startswith("nplurals="):
            try:
                nplurals = int(seg[len("nplurals="):].strip())
            except ValueError:
                nplurals = _DEFAULT_NPLURALS
        elif seg.startswith("plural="):
            expr = seg[len("plural="):].strip()
    if nplurals < 1 or nplurals > _MAX_NPLURALS:
        nplurals = _DEFAULT_NPLURALS
    if not expr:
        return nplurals, _default_plural
    try:
        return nplurals, _compile_plural(expr, nplurals)
    except MOError:
        return nplurals, _default_plural


def _parse_headers(header_bytes):
    """Extract ``(nplurals, plural_func)`` from a metadata msgstr block.

    Only the ``Plural-Forms:`` line matters here; a missing or garbled one yields
    the gettext defaults (``nplurals=2``, ``plural=(n!=1)``).
    """
    try:
        text = header_bytes.decode("utf-8")
    except UnicodeError:
        return _DEFAULT_NPLURALS, _default_plural
    for line in text.split("\n"):
        if line.lower().startswith("plural-forms:"):
            return _parse_plural_forms(line[len("plural-forms:"):])
    return _DEFAULT_NPLURALS, _default_plural


# ---------------------------------------------------------------------------
# .mo binary reader
# ---------------------------------------------------------------------------

class Catalog:
    """A parsed ``.mo`` exposing ``gettext`` / ``ngettext`` (CPython semantics)."""

    def __init__(self, mapping, plural_func, nplurals):
        self._catalog = mapping
        self._plural = plural_func
        self._nplurals = nplurals

    def gettext(self, message):
        return self._catalog.get(message, message)

    def ngettext(self, singular, plural, n):
        try:
            return self._catalog[(singular, self._plural(n))]
        except KeyError:
            return singular if n == 1 else plural


def parse_catalog(data):
    """Parse ``.mo`` bytes into a :class:`Catalog`. Raise :class:`MOError` if malformed.

    Bounds-checks every table entry and string slice against ``len(data)`` so a
    truncated or hostile file yields an error rather than an out-of-range read.
    """
    n = len(data)
    if n < 28:
        raise MOError("truncated header")

    if int.from_bytes(data[0:4], "little") == _MAGIC_LE:
        order = "little"
    elif int.from_bytes(data[0:4], "little") == _MAGIC_BE:
        order = "big"
    else:
        raise MOError("bad magic")

    def u32(off):
        if off < 0 or off + 4 > n:
            raise MOError("read past end")
        return int.from_bytes(data[off:off + 4], order)

    if (u32(4) >> 16) > 1:
        raise MOError("unsupported revision")
    num = u32(8)
    orig_off = u32(12)
    trans_off = u32(16)
    if num > _MAX_STRINGS:
        raise MOError("too many strings")
    # Each descriptor table holds `num` (length u32, offset u32) pairs = 8*num bytes.
    if orig_off + 8 * num > n or trans_off + 8 * num > n:
        raise MOError("descriptor table past end")

    def read_string(table_off, i):
        base = table_off + 8 * i
        slen = u32(base)
        soff = u32(base + 4)
        if soff + slen > n:
            raise MOError("string past end")
        return data[soff:soff + slen]

    catalog = {}
    nplurals = _DEFAULT_NPLURALS
    plural_func = _default_plural

    for i in range(num):
        orig = read_string(orig_off, i)
        trans = read_string(trans_off, i)

        if orig == b"":
            # Metadata entry: the header block carrying Plural-Forms.
            nplurals, plural_func = _parse_headers(trans)
            continue

        try:
            if b"\x00" in orig:
                # Plural: msgid \x00 msgid_plural  ->  msgstr[0] \x00 msgstr[1] ...
                msgid = orig.split(b"\x00", 1)[0].decode("utf-8")
                for idx, form in enumerate(trans.split(b"\x00")):
                    catalog[(msgid, idx)] = form.decode("utf-8")
            else:
                catalog[orig.decode("utf-8")] = trans.decode("utf-8")
        except UnicodeError:
            # A single undecodable entry is skipped, not fatal — one charset-mismatched
            # string shouldn't drop the whole catalog to passthrough.
            continue

    return Catalog(catalog, plural_func, nplurals)


def load_catalog_file(path):
    """Read and parse a ``.mo`` from ``path``.

    Returns a :class:`Catalog`, or ``None`` on ANY failure (missing/unreadable file,
    malformed content). Never raises — the caller falls back to the English
    passthrough.
    """
    try:
        f = open(path, "rb")
    except OSError:
        return None
    try:
        data = f.read()
    except OSError:
        return None
    finally:
        f.close()
    try:
        return parse_catalog(data)
    except MOError:
        return None
    except Exception:
        # Defence in depth: any unexpected parse error still fails closed.
        return None
