"""Tests for the pure-Python `.mo` reader + Plural-Forms evaluator (compat/_mo.py).

Two flavours of coverage:

  * **Oracle cross-checks** against CPython's own ``gettext.GNUTranslations`` over the
    real compiled catalogs — same bytes in, must produce the same translations and
    the same plural-form index for every ``n``. Skipped if the catalogs haven't been
    compiled (``python setup.py compile_catalog``); CI compiles them first.
  * **Hand-built / malformed inputs** that need no external files — deterministic
    reader + evaluator coverage, plus the fail-closed guarantees on hostile input.
"""
import gettext as _gettext
import os
import struct

import pytest

from seedsigner.compat import _mo

from langpack_catalog import resolve_catalog_root


# The catalogs live in the staged language packs (src/lang-packs) when built, else the
# bundled translations submodule — either way at <root>/<locale>/LC_MESSAGES/messages.mo.
CATALOG_ROOT = resolve_catalog_root()

# Every plural class in the shipped set: 1-form (ja/th), 2-form (fa, en-like), and
# the 3/4-form Slavic + es rules with nested ternaries and && / || precedence.
ORACLE_LOCALES = ["es", "cs", "ru", "pl", "fa", "ja", "th", "de", "fr"]


def _mo_path(locale):
    return os.path.join(CATALOG_ROOT, locale, "LC_MESSAGES", "messages.mo")


def _require_mo(locale):
    path = _mo_path(locale)
    if not os.path.exists(path):
        pytest.skip(f"{locale} messages.mo not staged in src/lang-packs "
                    "(build: ../seedsigner-language-packs/scripts/build_packs.sh --out-dir src/lang-packs)")
    return path


# ---------------------------------------------------------------------------
# Oracle cross-checks vs CPython gettext
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("locale", ORACLE_LOCALES)
def test_singular_matches_cpython_oracle(locale):
    """Every singular msgid resolves to exactly what CPython's gettext returns."""
    path = _require_mo(locale)
    ours = _mo.load_catalog_file(path)
    assert ours is not None
    with open(path, "rb") as f:
        ref = _gettext.GNUTranslations(f)

    msgids = [k for k in ref._catalog.keys() if isinstance(k, str) and k != ""]
    assert msgids, "oracle catalog unexpectedly empty"
    for msgid in msgids:
        assert ours.gettext(msgid) == ref.gettext(msgid)


@pytest.mark.parametrize("locale", ORACLE_LOCALES)
def test_plural_index_matches_cpython_oracle(locale):
    """Our Plural-Forms evaluator yields the same form index as CPython for all n."""
    path = _require_mo(locale)
    ours = _mo.load_catalog_file(path)
    with open(path, "rb") as f:
        ref = _gettext.GNUTranslations(f)
    for n in range(0, 1000):
        assert ours._plural(n) == ref.plural(n), f"{locale} diverges at n={n}"


@pytest.mark.parametrize("locale", ORACLE_LOCALES)
def test_ngettext_matches_cpython_oracle(locale):
    """ngettext over every plural msgid matches CPython across a range of n."""
    path = _require_mo(locale)
    ours = _mo.load_catalog_file(path)
    with open(path, "rb") as f:
        ref = _gettext.GNUTranslations(f)

    # Recover (singular, plural) pairs from the oracle's plural keys.
    singulars = {k[0] for k in ref._catalog.keys() if isinstance(k, tuple)}
    for singular in singulars:
        # The English plural msgid isn't stored; use the singular as a stand-in — the
        # fallback only matters when a form is missing, which the oracle handles too.
        for n in (0, 1, 2, 3, 5, 11, 21, 100, 101):
            assert ours.ngettext(singular, singular, n) == ref.ngettext(singular, singular, n)


def test_unknown_key_passes_through():
    path = _require_mo("es")
    ours = _mo.load_catalog_file(path)
    assert ours.gettext("this key does not exist — zzz") == "this key does not exist — zzz"


# ---------------------------------------------------------------------------
# Hand-built .mo (no external files)
# ---------------------------------------------------------------------------

def _build_mo(pairs, header=b"", little=True):
    """Assemble a minimal valid .mo from (orig_bytes, trans_bytes) pairs.

    The empty-msgid header entry is inserted automatically. Layout: 28-byte header,
    the two descriptor tables, then the string blob.
    """
    items = [(b"", header)] + list(pairs)
    items.sort(key=lambda kv: kv[0])
    n = len(items)
    fmt = "<" if little else ">"
    magic = 0x950412de

    orig_table_off = 28
    trans_table_off = orig_table_off + 8 * n
    blob_off = trans_table_off + 8 * n

    blob = bytearray()
    orig_descs = []
    trans_descs = []
    for orig, trans in items:
        orig_descs.append((len(orig), blob_off + len(blob)))
        blob += orig + b"\x00"
    for orig, trans in items:
        trans_descs.append((len(trans), blob_off + len(blob)))
        blob += trans + b"\x00"

    out = bytearray()
    out += struct.pack(fmt + "I", magic)
    out += struct.pack(fmt + "I", 0)            # revision
    out += struct.pack(fmt + "I", n)            # count
    out += struct.pack(fmt + "I", orig_table_off)
    out += struct.pack(fmt + "I", trans_table_off)
    out += struct.pack(fmt + "I", 0)            # hash size
    out += struct.pack(fmt + "I", 0)            # hash offset
    for length, off in orig_descs:
        out += struct.pack(fmt + "II", length, off)
    for length, off in trans_descs:
        out += struct.pack(fmt + "II", length, off)
    out += blob
    return bytes(out)


@pytest.mark.parametrize("little", [True, False])
def test_roundtrip_singular_and_plural(little):
    header = b"Content-Type: text/plain; charset=UTF-8\nPlural-Forms: nplurals=2; plural=(n != 1);\n"
    mo = _build_mo(
        [
            ("Scan".encode(), "Escanear".encode()),
            ("Café".encode(), "Cafetería".encode()),  # non-ASCII UTF-8 round-trip
            (b"apple\x00apples", b"manzana\x00manzanas"),        # plural entry
        ],
        header=header,
        little=little,
    )
    cat = _mo.parse_catalog(mo)
    assert cat.gettext("Scan") == "Escanear"
    assert cat.gettext("Café") == "Cafetería"
    assert cat.gettext("unknown") == "unknown"
    # Plural msgid isn't a singular key (matches CPython), so gettext passes through.
    assert cat.gettext("apple") == "apple"
    assert cat.ngettext("apple", "apples", 1) == "manzana"
    assert cat.ngettext("apple", "apples", 2) == "manzanas"
    assert cat.ngettext("apple", "apples", 0) == "manzanas"
    # Missing key -> English fallback by the English plural rule.
    assert cat.ngettext("dog", "dogs", 1) == "dog"
    assert cat.ngettext("dog", "dogs", 3) == "dogs"


def test_missing_plural_header_defaults():
    mo = _build_mo([("x".encode(), "y".encode())], header=b"Content-Type: text/plain; charset=UTF-8\n")
    cat = _mo.parse_catalog(mo)
    assert cat._nplurals == 2
    assert cat._plural(1) == 0
    assert cat._plural(2) == 1


# ---------------------------------------------------------------------------
# Fail-closed on malformed / hostile input
# ---------------------------------------------------------------------------

def test_bad_magic_raises():
    with pytest.raises(_mo.MOError):
        _mo.parse_catalog(b"\x00\x01\x02\x03" + b"\x00" * 40)


def test_truncated_header_raises():
    with pytest.raises(_mo.MOError):
        _mo.parse_catalog(b"\xde\x12\x04\x95")  # magic only, too short


def test_out_of_bounds_offsets_raise():
    # Valid magic + revision but the descriptor table claims 1000 entries the file
    # can't hold -> a bounds error, not an out-of-range read.
    data = struct.pack("<IIIIIII", 0x950412de, 0, 1000, 28, 28 + 8000, 0, 0)
    with pytest.raises(_mo.MOError):
        _mo.parse_catalog(data)


def test_load_catalog_file_missing_returns_none():
    assert _mo.load_catalog_file("/no/such/path/messages.mo") is None


def test_load_catalog_file_garbage_returns_none(tmp_path):
    p = tmp_path / "messages.mo"
    p.write_bytes(b"not a real mo file at all")
    assert _mo.load_catalog_file(str(p)) is None


# ---------------------------------------------------------------------------
# Plural evaluator unit tests (no eval, bounded, safe arithmetic)
# ---------------------------------------------------------------------------

def test_evaluator_known_rules():
    # English / Germanic: form 1 unless n == 1.
    f = _mo._compile_plural("n != 1", 2)
    assert [f(n) for n in (0, 1, 2, 5)] == [1, 0, 1, 1]

    # French / Portuguese-BR style: singular also covers 0.
    f = _mo._compile_plural("n > 1", 2)
    assert [f(n) for n in (0, 1, 2)] == [0, 0, 1]

    # Russian 3-way rule exercises %, &&, ||, nested ternary and precedence.
    ru = ("(n%10==1 && n%100!=11 ? 0 : n%10>=2 && n%10<=4 && "
          "(n%100<12 || n%100>14) ? 1 : n%10==0 || (n%10>=5 && n%10<=9) || "
          "(n%100>=11 && n%100<=14)? 2 : 3)")
    f = _mo._compile_plural(ru, 4)
    assert f(1) == 0
    assert f(2) == 1 and f(3) == 1 and f(4) == 1
    assert f(5) == 2 and f(11) == 2 and f(0) == 2
    assert f(22) == 1  # 22 % 10 == 2, 22 % 100 == 22 -> form 1


def test_evaluator_clamps_out_of_range_index():
    # A rule that returns 9 is clamped to nplurals-1.
    f = _mo._compile_plural("9", 3)
    assert f(0) == 2
    # A negative result is clamped to 0.
    f = _mo._compile_plural("0 - 5", 3)
    assert f(0) == 0


def test_evaluator_safe_division_and_modulo_by_zero():
    # Hostile divisor: must not raise; yields a clamped index.
    f = _mo._compile_plural("n / 0", 2)
    assert f(5) == 0
    f = _mo._compile_plural("n % 0", 2)
    assert f(5) == 0


def test_evaluator_rejects_unknown_tokens():
    with pytest.raises(_mo.MOError):
        _mo._compile_plural("n + x", 2)
    with pytest.raises(_mo.MOError):
        _mo._compile_plural("__import__('os')", 2)


def test_evaluator_rejects_overlong_expression():
    with pytest.raises(_mo.MOError):
        _mo._compile_plural("n" + " + n" * 1000, 2)


def test_evaluator_rejects_deep_paren_nesting():
    with pytest.raises(_mo.MOError):
        _mo._compile_plural("(" * 50 + "n" + ")" * 50, 2)


def test_evaluator_rejects_trailing_and_unbalanced():
    with pytest.raises(_mo.MOError):
        _mo._compile_plural("n 1", 2)       # two primaries, no operator
    with pytest.raises(_mo.MOError):
        _mo._compile_plural("(n", 2)        # unbalanced paren
    with pytest.raises(_mo.MOError):
        _mo._compile_plural("n ? 1", 2)     # ternary missing ':'


def test_malformed_plural_header_falls_back_to_default():
    # A garbled plural expression in the header must not blow up the load — the
    # catalog still parses, with the default (n != 1) rule.
    mo = _build_mo(
        [("x".encode(), "y".encode())],
        header=b"Plural-Forms: nplurals=2; plural=n +/* broken;\n",
    )
    cat = _mo.parse_catalog(mo)
    assert cat.gettext("x") == "y"
    assert cat._plural(1) == 0 and cat._plural(2) == 1
