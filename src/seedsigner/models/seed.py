import logging
import hashlib

from binascii import hexlify
from embit import bip39, bip32, bip85
from embit.networks import NETWORKS

from seedsigner.compat.hmac import digest as hmac_digest
from seedsigner.models.settings import SettingsConstants

logger = logging.getLogger(__name__)


class InvalidSeedException(Exception):
    pass


def _require_ascii(value: str, label: str) -> None:
    """Reject non-ASCII input to the seed-derivation path.

    SeedSigner no longer NFKD/NFC-normalizes mnemonics or passphrases: the
    device's only passphrase source is an ASCII keyboard and the only supported
    BIP-39 wordlist is English (also ASCII), where Unicode normalization is a
    provable no-op. This guard is a future-facing safety check — without
    normalization, a non-ASCII passphrase or mnemonic would silently derive the
    *wrong* seed. We do not allow any non-ASCII passphrase characters, and are
    unlikely to add a BIP-39 wordlist that needs them; but if either ever
    changes, this fails loud here instead of producing wrong keys. (`ord(c)` is
    used rather than `str.isascii()` to stay portable to MicroPython.)
    """
    if any(ord(c) >= 0x80 for c in value):
        raise InvalidSeedException(f"{label} contains non-ASCII characters")



class Seed:
    def __init__(self,
                 mnemonic: list[str] = None,
                 passphrase: str = "",
                 wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> None:
        self._wordlist_language_code = wordlist_language_code

        if not mnemonic:
            raise Exception("Must initialize a Seed with a mnemonic List[str]")
        mnemonic_str = " ".join(mnemonic).strip()
        _require_ascii(mnemonic_str, "Mnemonic")
        self._mnemonic: list[str] = mnemonic_str.split()

        self._passphrase: str = ""
        self.set_passphrase(passphrase, regenerate_seed=False)

        self.seed_bytes: bytes = None
        self._generate_seed()


    @staticmethod
    def get_wordlist(wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> list[str]:
        # TODO: Support other BIP-39 wordlist languages!
        if wordlist_language_code == SettingsConstants.WORDLIST_LANGUAGE__ENGLISH:
            return bip39.WORDLIST
        else:
            raise Exception(f"Unrecognized wordlist_language_code {wordlist_language_code}")


    def _generate_seed(self):
        try:
            self.seed_bytes = bip39.mnemonic_to_seed(self.mnemonic_str, password=self._passphrase, wordlist=self.wordlist)
        except Exception as e:
            logger.info(repr(e), exc_info=True)
            raise InvalidSeedException(repr(e))


    @property
    def mnemonic_str(self) -> str:
        return " ".join(self._mnemonic)
    

    @property
    def mnemonic_list(self) -> list[str]:
        return self._mnemonic


    @property 
    def wordlist_language_code(self) -> str:
        return self._wordlist_language_code


    @property
    def mnemonic_display_str(self) -> str:
        return " ".join(self._mnemonic)


    @property
    def mnemonic_display_list(self) -> list[str]:
        return list(self._mnemonic)


    @property
    def has_passphrase(self):
        return self._passphrase != ""


    @property
    def passphrase(self):
        return self._passphrase
        

    @property
    def passphrase_display(self):
        return self._passphrase


    def set_passphrase(self, passphrase: str, regenerate_seed: bool = True):
        if passphrase:
            _require_ascii(passphrase, "Passphrase")
            self._passphrase = passphrase
        else:
            # Passphrase must always have a string value, even if it's just the empty
            # string.
            self._passphrase = ""

        if regenerate_seed:
            # Regenerate the internal seed since passphrase changes the result
            self._generate_seed()


    @property
    def wordlist(self) -> list[str]:
        return Seed.get_wordlist(self.wordlist_language_code)


    def set_wordlist_language_code(self, language_code: str):
        # TODO: Support other BIP-39 wordlist languages!
        raise Exception("Not yet implemented!")


    @property
    def script_override(self) -> str:
        return None


    def derivation_override(self, sig_type: str = SettingsConstants.SINGLE_SIG) -> str:
        return None


    def detect_version(self, derivation_path: str, network: str = SettingsConstants.MAINNET, sig_type: str = SettingsConstants.SINGLE_SIG) -> str:
        embit_network = NETWORKS[SettingsConstants.map_network_to_embit(network)]
        return bip32.detect_version(derivation_path, default="xpub", network=embit_network)


    @property
    def passphrase_label(self) -> str:
        return SettingsConstants.LABEL__BIP39_PASSPHRASE


    @property
    def seedqr_supported(self) -> bool:
        return True


    @property
    def bip85_supported(self) -> bool:
        return True


    def get_fingerprint(self, network: str = SettingsConstants.MAINNET) -> str:
        root = bip32.HDKey.from_seed(self.seed_bytes, version=NETWORKS[SettingsConstants.map_network_to_embit(network)]["xprv"])
        return hexlify(root.child(0).fingerprint).decode('utf-8')


    def get_xpub(self, wallet_path: str = '/', network: str = SettingsConstants.MAINNET):
        # Import here to avoid slow startup times; takes 1.35s to import the first time
        from seedsigner.helpers import embit_utils
        return embit_utils.get_xpub(seed_bytes=self.seed_bytes, derivation_path=wallet_path, embit_network=SettingsConstants.map_network_to_embit(network))


    def get_bip85_child_mnemonic(self, bip85_index: int, bip85_num_words: int, network: str = SettingsConstants.MAINNET):
        """Derives the seed's nth BIP-85 child mnemonic"""
        root = bip32.HDKey.from_seed(self.seed_bytes, version=NETWORKS[SettingsConstants.map_network_to_embit(network)]["xprv"])

        # TODO: Support other BIP-39 wordlist languages!
        return bip85.derive_mnemonic(root, bip85_num_words, bip85_index)
        

    ### override operators    
    def __eq__(self, other):
        if isinstance(other, Seed):
            return self.seed_bytes == other.seed_bytes
        return False



class ElectrumSeed(Seed):

    def _generate_seed(self):
        if len(self._mnemonic) != 12:
            raise InvalidSeedException(f"Unsupported Electrum seed length: {len(self._mnemonic)}")

        s = hmac_digest(b"Seed version", self.mnemonic_str.encode('utf8'), hashlib.sha512).hex()
        prefix = s[0:3]

        # only support Electrum Segwit version for now
        if SettingsConstants.ELECTRUM_SEED_SEGWIT == prefix:
            self.seed_bytes=hashlib.pbkdf2_hmac('sha512', self.mnemonic_str.encode('utf-8'), b'electrum' + self._passphrase.encode('utf-8'), iterations = SettingsConstants.ELECTRUM_PBKDF2_ROUNDS)

        else:
            raise InvalidSeedException(f"Unsupported Electrum seed format: {prefix}")


    def set_passphrase(self, passphrase: str, regenerate_seed: bool = True):
        if passphrase:
            self._passphrase = ElectrumSeed.normalize_electrum_passphrase(passphrase)
        else:
            # Passphrase must always have a string value, even if it's just the empty
            # string.
            self._passphrase = ""

        if regenerate_seed:
            # Regenerate the internal seed since passphrase changes the result
            self._generate_seed()


    @staticmethod
    def normalize_electrum_passphrase(passphrase : str) -> str:
        _require_ascii(passphrase, "Passphrase")
        # NFKD normalization is a no-op on the ASCII-only input the device accepts
        # (see _require_ascii). Electrum additionally lowercases and collapses
        # whitespace, both ASCII-safe.
        passphrase = passphrase.lower()
        # normalize whitespaces
        passphrase = ' '.join(passphrase.split())
        return passphrase


    @property
    def script_override(self) -> str:
        return SettingsConstants.NATIVE_SEGWIT


    def derivation_override(self, sig_type: str = SettingsConstants.SINGLE_SIG) -> str:
        return "m/0h" if sig_type == SettingsConstants.SINGLE_SIG else "m/1h"


    def detect_version(self, derivation_path: str, network: str = SettingsConstants.MAINNET, sig_type: str = SettingsConstants.SINGLE_SIG) -> str:
        embit_network = NETWORKS[SettingsConstants.map_network_to_embit(network)]
        return embit_network["zpub"] if sig_type == SettingsConstants.SINGLE_SIG else embit_network["Zpub"]


    @property
    def passphrase_label(self) -> str:
        return SettingsConstants.LABEL__CUSTOM_EXTENSION


    @property
    def seedqr_supported(self) -> bool:
        return False


    @property
    def bip85_supported(self) -> bool:
        return False
