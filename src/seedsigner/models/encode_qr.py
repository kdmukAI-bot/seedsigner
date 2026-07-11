import math

from embit import bip32
from embit.networks import NETWORKS
from binascii import hexlify
from collections import namedtuple
from embit import bip32
from embit.networks import NETWORKS
from embit.psbt import PSBT
from seedsigner.helpers.ur2.encoder import UREncoder
from seedsigner.helpers.ur2.ur import UR
from seedsigner.helpers.qr import QR
from seedsigner.models.seed import Seed
from seedsigner.models.settings import SettingsConstants

# urtypes.crypto is imported lazily inside the UR encoder classes below (it isn't
# available on MicroPython); the UR-format QR-encode path is deferred there.



XpubData = namedtuple("XpubData", ["root", "xpub", "xpubstring"])


def build_xpub_data(seed, derivation, network, sig_type) -> XpubData:
    """
    Derive the transport-agnostic xpub material shared by every xpub QR encoder: the
    BIP32 root key, the derived public key, and the `[fingerprint/derivation]xpub`
    descriptor string. Returned as an immutable record so each encoder can serialize it
    in its own QR format.
    """
    version = seed.detect_version(derivation, network, sig_type)
    root = bip32.HDKey.from_seed(
        seed.seed_bytes,
        version=NETWORKS[SettingsConstants.map_network_to_embit(network)]["xprv"],
    )
    fingerprint = root.child(0).fingerprint
    xprv = root.derive(derivation)
    xpub = xprv.to_public()
    xpub_base58 = xpub.to_string(version=version)
    xpubstring = "[{}{}]{}".format(
        hexlify(fingerprint).decode("utf-8"),
        derivation[1:],
        xpub_base58,
    )
    return XpubData(root=root, xpub=xpub, xpubstring=xpubstring)



class BaseQrEncoder:
    def __init__(self, qr_density: int = SettingsConstants.DENSITY__DEFAULT, **kwargs):
        self.qr_density = qr_density
        super().__init__(**kwargs)
        self.qr = QR()


    @property
    def is_complete(self):
        raise Exception("Not implemented in child class")

    @property
    def qr_max_fragment_size(self):
        raise Exception("Not implemented in child class")

    @property
    def qr_px_per_module(self) -> int:
        """The QR density setting (SETTING__QR_DENSITY) as an int pixels-per-module (3-6).

        Clamped to the supported band, so a hand-edited settings.json or a legacy tier that
        slipped past the read-time migration can't produce an out-of-range lookup key — it
        falls back to the default rather than raising while a signing QR is on screen."""
        from seedsigner.models.qr_density import QR_PX_PER_MODULE_MIN, QR_PX_PER_MODULE_MAX
        try:
            px = int(self.qr_density)
        except (TypeError, ValueError):
            px = int(SettingsConstants.DENSITY__DEFAULT)
        return min(QR_PX_PER_MODULE_MAX, max(QR_PX_PER_MODULE_MIN, px))

    def seq_len(self):
        raise Exception("Not implemented in child class")

    def next_part(self) -> str:
        raise Exception("Not implemented in child class")
    
    def cur_part(self) -> str:
        raise Exception("Not implemented in child class")
    
    def restart(self):
        # only used by animated QR encoders
        pass

    def _create_parts(self):
        raise Exception("Not implemented in child class")


    def part_to_image(self, part, width, height, border: int = 3, background_color: str = "ffffff"):
        return self.qr.qrimage_io(part, width, height, border, background_color=background_color)


    def next_part_image(self, width=240, height=240, border=3, background_color="bdbdbd"):
        part = self.next_part()
        return self.part_to_image(part, width, height, border, background_color=background_color)




"""**************************************************************************************
    STATIC QR encoders
**************************************************************************************"""
class BaseStaticQrEncoder(BaseQrEncoder):
    def seq_len(self):
        return 1
    
    def cur_part(self) -> str:
        """ static QRs only have a single part, which `next_part` always returns """
        return self.next_part()


    @property
    def is_complete(self):
        return True



class SeedQrEncoder(BaseStaticQrEncoder):
    def __init__(self,
                 mnemonic: list[str] = None,
                 wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH,
                 **kwargs):
        self.mnemonic = mnemonic
        self.wordlist_language_code = wordlist_language_code
        self.wordlist = Seed.get_wordlist(self.wordlist_language_code)
        super().__init__(**kwargs)

        self.data = ""
        # Output as Numeric data format
        for word in self.mnemonic:
            index = self.wordlist.index(word)
            self.data += str("%04d" % index)
    

    def next_part(self):
        return self.data



class CompactSeedQrEncoder(SeedQrEncoder):
    def next_part(self):
        # Output as binary data format
        binary_str = ""
        for word in self.mnemonic:
            index = self.wordlist.index(word)

            # Index as 11-bit zero-padded binary. MicroPython 1.27 has no str.zfill;
            # the format mini-language zero-pads on both CPython and MicroPython.
            binary_str += "{:011b}".format(index)

        # We can exclude the checksum bits at the end
        if len(self.mnemonic) == 24:
            # 8 checksum bits in a 24-word seed
            binary_str = binary_str[:-8]

        elif len(self.mnemonic) == 12:
            # 4 checksum bits in a 12-word seed
            binary_str = binary_str[:-4]

        # Now convert to bytes, 8 bits at a time
        as_bytes = bytearray()
        for i in range(0, math.ceil(len(binary_str) / 8)):
            # int conversion reads byte data as a string prefixed with '0b'
            as_bytes.append(int('0b' + binary_str[i*8:(i+1)*8], 2))
        
        # Must return data as `bytes` for `qrcode` to properly recognize it as byte data
        return bytes(as_bytes)



class GenericStaticQrEncoder(BaseStaticQrEncoder):
    def __init__(self, data: str = None, **kwargs):
        self.data = data
        super().__init__(**kwargs)

    def next_part(self):
        return self.data



class StaticXpubQrEncoder(BaseStaticQrEncoder):
    def __init__(self,
                 seed: Seed = None,
                 derivation: str = None,
                 network: str = SettingsConstants.MAINNET,
                 sig_type: str = None,
                 **kwargs):
        self.seed = seed
        self.derivation = derivation
        self.network = network
        self.sig_type = sig_type
        super().__init__(**kwargs)
        self.xpub_data = build_xpub_data(seed, derivation, network, sig_type)


    def next_part(self):
        return self.xpub_data.xpubstring



"""**************************************************************************************
    Simple animated QR encoders
**************************************************************************************"""
class BaseSimpleAnimatedQREncoder(BaseQrEncoder):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.parts = []
        self.part_num_sent = 0
        self.sent_complete = False
        self._create_parts()


    @property
    def is_complete(self):
        return self.sent_complete


    def seq_len(self):
        return len(self.parts)


    def next_part(self) -> str:
        # if part num sent is gt number of parts, start at 0
        if self.part_num_sent > (len(self.parts) - 1):
            self.part_num_sent = 0

        part = self.parts[self.part_num_sent]

        # when parts sent eq num of parts in list
        if self.part_num_sent == (len(self.parts) - 1):
            self.sent_complete = True

        # increment to next part
        self.part_num_sent += 1

        return part


    def cur_part(self) -> str:
        if self.part_num_sent == 0:
            # Rewind all the way back to the end
            self.part_num_sent = len(self.parts) - 1
        else:
            self.part_num_sent -= 1
        return self.next_part()


    def restart(self) -> str:
        self.part_num_sent = 0



class SpecterLegacyXPubQrEncoder(BaseSimpleAnimatedQREncoder):
    """
    Legacy "pXofY" format. Included here for compatibility with much older versions of
    Specter Desktop. Can probably eventually be removed.
    """
    def __init__(self,
                 seed: Seed = None,
                 derivation: str = None,
                 network: str = SettingsConstants.MAINNET,
                 sig_type: str = None,
                 **kwargs):
        self.seed = seed
        self.derivation = derivation
        self.network = network
        self.sig_type = sig_type
        super().__init__(**kwargs)


    # Fixed fragment size, formerly the "Medium" tier of the old Low/Medium/High density
    # model. The resolution-aware px/module density model (QR_DENSITY_BY_RESOLUTION) targets
    # the UR fountain encoders only; this legacy "pXofY" format is out of scope for it. This
    # encoder is expected to be unimportant, and is likely to be removed entirely in the near
    # future (see the class docstring), so it simply keeps the old Medium value.
    QR_MAX_FRAGMENT_SIZE = 65

    @property
    def qr_max_fragment_size(self):
        return self.QR_MAX_FRAGMENT_SIZE


    def _create_parts(self):
        xpubstring = build_xpub_data(self.seed, self.derivation, self.network, self.sig_type).xpubstring
        start = 0
        stop = self.qr_max_fragment_size
        qr_cnt = ((len(xpubstring)-1) // self.qr_max_fragment_size) + 1

        if qr_cnt == 1:
            self.parts.append(xpubstring[start:stop])

        cnt = 0
        while cnt < qr_cnt and qr_cnt != 1:
            part = "p" + str(cnt+1) + "of" + str(qr_cnt) + " " + xpubstring[start:stop]
            self.parts.append(part)

            start = start + self.qr_max_fragment_size
            stop = stop + self.qr_max_fragment_size
            if stop > len(xpubstring):
                stop = len(xpubstring)
            cnt += 1



def _vertical_resolution() -> int:
    """The active display's vertical resolution (px), for the density lookup.

    Reads whichever Renderer is configured (PIL on CPython/Pi, LvglRenderer on MicroPython).
    Falls back to the smallest supported panel (240) when no Renderer is configured yet — e.g.
    a unit test that builds an encoder directly — so resolving density never crashes."""
    try:
        from seedsigner.gui.renderer import Renderer
        resolution = int(Renderer.get_instance().canvas_height)
    except Exception:
        # Unconfigured singleton, or a mocked/absent Renderer (int() rejects a non-number).
        resolution = 0
    return resolution or 240


"""**************************************************************************************
    Fountain encoded animated QR encoders
**************************************************************************************"""
class BaseFountainQrEncoder(BaseQrEncoder):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # The UR object (type + cbor) to fountain-encode; set by the subclass, kept so the
        # encoder can be rebuilt when the density (px/module) changes on the live slider.
        self._qr_ur = None
        self.ur2_encode = None


    def _build_encoder(self):
        """(Re)build the fountain encoder at the current qr_max_fragment_size."""
        self.ur2_encode = UREncoder(ur=self._qr_ur, max_fragment_len=self.qr_max_fragment_size)


    def set_px_per_module(self, px_per_module):
        """Live-slider hook: adopt a new px/module density and re-split the fountain from part 0.

        Changing px/module changes qr_max_fragment_size (the per-frame byte budget), so the whole
        message must be re-split — restart() alone only rewinds at the current fragment size."""
        self.qr_density = int(px_per_module)
        self._build_encoder()


    @property
    def is_complete(self):
        return self.ur2_encode.is_complete()


    @property
    def qr_max_fragment_size(self):
        # Resolution-aware: pick the densest per-frame byte budget that still renders the QR
        # at >= the selected pixels-per-module on this panel. See models/qr_density.py.
        from seedsigner.models.qr_density import max_fragment_len_for
        return max_fragment_len_for(_vertical_resolution(), self.qr_px_per_module)


    def _create_parts(self):
        """ parts are dynamically generated by the fountain encoder """
        pass


    def seq_len(self):
        return self.ur2_encode.fountain_encoder.seq_len()


    def next_part(self) -> str:
        return self.ur2_encode.next_part().upper()


    def cur_part(self) -> str:
        return self.ur2_encode.current_part().upper()
    

    def restart(self):
        self.ur2_encode.fountain_encoder.restart()



class UrXpubQrEncoder(BaseFountainQrEncoder):
    def __init__(self,
                 seed: Seed = None,
                 derivation: str = None,
                 network: str = SettingsConstants.MAINNET,
                 sig_type: str = None,
                 **kwargs):
        self.seed = seed
        self.derivation = derivation
        self.network = network
        self.sig_type = sig_type
        super().__init__(**kwargs)

        from urtypes.crypto import Account, HDKey, Output, Keypath, PathComponent, SCRIPT_EXPRESSION_TAG_MAP, CoinInfo

        xd = build_xpub_data(self.seed, self.derivation, self.network, self.sig_type)

        def derivation_to_keypath(path: str) -> list:
            arr = path.split("/")
            if arr[0] == "m":
                arr = arr[1:]
            if len(arr) == 0:
                return Keypath([],xd.root.my_fingerprint, None)
            if arr[-1] == "":
                # trailing slash
                arr = arr[:-1]

            for i, e in enumerate(arr):
                if e[-1] == "h" or e[-1] == "'":
                    arr[i] = PathComponent(int(e[:-1]), True)
                else:
                    arr[i] = PathComponent(int(e), False)

            return Keypath(arr, xd.root.my_fingerprint, len(arr))

        origin = derivation_to_keypath(self.derivation)

        # Implemts "use_info" member on HDKey class (urtypes/crypto packages-libs folder) construct,
        # so if working on TESTNET, Xpub can be exported accordingly. Default case, MAINNET: None value.
        self.use_info = None if self.network == SettingsConstants.MAINNET else CoinInfo(type=None, network=1)

        self.ur_hdkey = HDKey({ 'key': xd.xpub.key.serialize(),
        'chain_code': xd.xpub.chain_code,
        'origin': origin,
        'parent_fingerprint': xd.xpub.fingerprint,
        'use_info': self.use_info })

        ur_outputs = []

        if len(origin.components) > 0:
            if origin.components[0].index == 84: # Native Single Sig
                ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[404]],self.ur_hdkey))
            elif origin.components[0].index == 49: # Nested Single Sig
                ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[400], SCRIPT_EXPRESSION_TAG_MAP[404]],self.ur_hdkey))
            elif origin.components[0].index == 48: # Multisig
                if len(origin.components) >= 4:
                    if origin.components[3].index == 2:  # Native Multisig
                        ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[401]],self.ur_hdkey))
                    elif origin.components[3].index == 1:  # Nested Multisig
                        ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[400], SCRIPT_EXPRESSION_TAG_MAP[401]],self.ur_hdkey))
            elif origin.components[0].index == 86: # P2TR
                ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[409]],self.ur_hdkey))
            elif origin.components[0].index == 44: # P2PKH
                ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[403]],self.ur_hdkey))
            elif origin.components[0].index == 45: # P2SH 
                ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[400]],self.ur_hdkey))
        
        # If empty, add all script types
        if len(ur_outputs) == 0:
            ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[404]],self.ur_hdkey))
            ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[400], SCRIPT_EXPRESSION_TAG_MAP[404]],self.ur_hdkey))
            ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[401]],self.ur_hdkey))
            ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[400], SCRIPT_EXPRESSION_TAG_MAP[401]],self.ur_hdkey))
            ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[403]],self.ur_hdkey))
            ur_outputs.append(Output([SCRIPT_EXPRESSION_TAG_MAP[400]],self.ur_hdkey))
        
        ur_account = Account(xd.root.my_fingerprint, ur_outputs)

        self._qr_ur = UR("crypto-account", ur_account.to_cbor())
        self._build_encoder()



class UrPsbtQrEncoder(BaseFountainQrEncoder):
    def __init__(self, psbt: PSBT = None, **kwargs):
        self.psbt = psbt
        super().__init__(**kwargs)
        from urtypes.crypto import PSBT as UR_PSBT
        self._qr_ur = UR("crypto-psbt", UR_PSBT(self.psbt.serialize()).to_cbor())
        self._build_encoder()
