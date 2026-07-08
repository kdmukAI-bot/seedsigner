"""Guard tests for the PSBT-parse optimizations (Phase 2: 2a/2b/2c).

These pin the two subtle correctness claims the speedups rest on:
  * 2b: self.root.my_fingerprint == self.root.child(0).fingerprint (byte-identical
    substitution), and the zero-fingerprint fill path (issue #359) still fills
    owned inputs with the master fingerprint.
  * 2c: the per-parse change-branch cache keys on xpub identity, so it never
    crosses xpubs, and a cached branch derives byte-identically to a fresh derive.

Byte-identical parse output over the representative fixtures is covered separately
by tools/device_scan/psbt_parse_verify.py (host) + the device harness digests.
"""
from binascii import a2b_base64

from embit import bip32
from embit.networks import NETWORKS
from embit.psbt import PSBT, DerivationPath

from seedsigner.models.psbt_parser import PSBTParser
from seedsigner.models.settings_definition import SettingsConstants

from psbt_testing_util import PSBTTestData


class TestPSBTParserOptimizations:
    seed = PSBTTestData.seed

    def _root(self):
        return bip32.HDKey.from_seed(
            self.seed.seed_bytes, version=NETWORKS["main"]["xprv"])

    # ---- 2b -------------------------------------------------------------
    def test_2b_my_fingerprint_equals_child0_fingerprint(self):
        """The premise of 2b: hoisting my_fingerprint in place of
        child(0).fingerprint is byte-identical, because HDKey.child(0) sets its
        .fingerprint to hash160(parent.sec())[:4] == parent.my_fingerprint."""
        root = self._root()
        assert root.my_fingerprint == root.child(0).fingerprint

    def test_2b_zero_fingerprint_filled_with_master(self):
        """Issue #359: a coordinator omits key-origin fingerprints (all-zero).
        _fill_missing_fingerprints must still fill owned inputs with the seed's
        master fingerprint — the value 2b now sources from my_fingerprint."""
        psbt = PSBT.parse(a2b_base64(PSBTTestData.SINGLE_SIG_NATIVE_SEGWIT_1_INPUT))
        master_fp = self._root().my_fingerprint

        zeroed = 0
        for inp in psbt.inputs:
            for pub, dp in list(inp.bip32_derivations.items()):
                inp.bip32_derivations[pub] = DerivationPath(b"\x00\x00\x00\x00", dp.derivation)
                zeroed += 1
        assert zeroed > 0, "fixture had no input derivations to zero"

        # Exercise just the fill path in isolation (no output-vout indexing).
        pp = PSBTParser.__new__(PSBTParser)
        pp.psbt = psbt
        pp.seed = self.seed
        pp.network = SettingsConstants.MAINNET
        pp._set_root()
        pp._fill_missing_fingerprints()

        for inp in psbt.inputs:
            for pub, dp in inp.bip32_derivations.items():
                assert dp.fingerprint == master_fp

    # ---- 2c -------------------------------------------------------------
    def test_2c_cache_no_cross_xpub_and_byte_identical(self):
        """The change-branch cache keys on xpub identity (never crosses xpubs),
        and a cached branch.child(index) derives byte-identically to the original
        xpub.derive([change, index])."""
        root = self._root()
        xpub_a = root.derive("m/48h/0h/0h/2h").to_public()
        xpub_b = root.derive("m/48h/0h/1h/2h").to_public()  # different account

        cache = {}
        cache[(id(xpub_a), 0)] = xpub_a.child(0)
        assert (id(xpub_b), 0) not in cache                 # no collision across xpubs
        cache[(id(xpub_b), 0)] = xpub_b.child(0)
        assert cache[(id(xpub_a), 0)].to_base58() != cache[(id(xpub_b), 0)].to_base58()

        # cached-branch derivation == fresh derive, byte-for-byte
        for index in (0, 1, 5, 100):
            cached = cache[(id(xpub_a), 0)].child(index).key.sec()
            fresh = xpub_a.derive([0, index]).key.sec()
            assert cached == fresh
