import hashlib

from embit import bip39
from seedsigner.models.settings_definition import SettingsConstants
from seedsigner.models.seed import Seed

"""
    This is SeedSigner's internal mnemonic generation utility.
     
    It can also be run as an independently-executable CLI to facilitate external
    verification of SeedSigner's results for a given input entropy.

    see: docs/dice_verification.md (the "Command Line Tool" section).
"""

DICE__NUM_ROLLS__12WORD = 50
DICE__NUM_ROLLS__24WORD = 99



def calculate_checksum(mnemonic: list | str, wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> list[str]:
    """
        Provide 12- or 24-word mnemonic, returns complete mnemonic w/checksum as a list.

        Mnemonic may be a list of words or a string of words separated by spaces or commas.

        If 11- or 23-words are provided, append word `0000` to end of list as temp final
        word.
    """
    if type(mnemonic) == str:
        # split on commas or whitespace; str.split() drops empty tokens, matching
        # the previous findall-based split (findall is absent on MicroPython's re)
        mnemonic = mnemonic.replace(",", " ").split()

    if len(mnemonic) in [11, 23]:
        temp_final_word = Seed.get_wordlist(wordlist_language_code)[0]
        mnemonic.append(temp_final_word)

    if len(mnemonic) not in [12, 24]:
        raise Exception("Pass in a 12- or 24-word mnemonic")
    
    # Work on a copy of the input list
    mnemonic_copy = mnemonic.copy()

    # Convert the resulting mnemonic to bytes, but we `ignore_checksum` validation
    # because we assume it's incorrect since we either let the user select their own
    # final word OR we injected the 0000 word from the wordlist.
    # No Unicode normalization: BIP-39 wordlists supported here are English (ASCII),
    # where NFKD is a no-op. The resulting Seed enforces ASCII at the derivation
    # boundary (see Seed._require_ascii).
    mnemonic_bytes = bip39.mnemonic_to_bytes(" ".join(mnemonic_copy), ignore_checksum=True, wordlist=Seed.get_wordlist(wordlist_language_code))

    # This function will convert the bytes back into a mnemonic, but it will also
    # calculate the proper checksum bits while doing so. For a 12-word seed it will just
    # overwrite the last 4 bits from the above result with the checksum; for a 24-word
    # seed it'll overwrite the last 8 bits.
    return bip39.mnemonic_from_bytes(mnemonic_bytes).split()



def generate_mnemonic_from_bytes(entropy_bytes, wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> list[str]:
    return bip39.mnemonic_from_bytes(entropy_bytes, wordlist=Seed.get_wordlist(wordlist_language_code)).split()



def generate_mnemonic_from_camera_entropy(chain: bytes, frame: bytes, mnemonic_length: int, wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> list[str]:
    """Derive a BIP-39 mnemonic from the native ``camera_entropy`` result (ESP32 image entropy).

    The firmware returns a running SHA-256 ``chain`` over the optional caller seed + the preview
    frames — EXCLUDING the latched final image — plus the latched RGB565 ``frame``. The entropy is
    ``SHA-256(chain + frame)``, which mirrors the PIL image-entropy scheme's final step (chain the
    preview frames, then fold in the full-res final image): the same composition, so the camera
    frames remain the entropy source and the final frame is the headline contribution.

    12-word seeds use the first 128 bits / 16 bytes, matching ``generate_mnemonic_from_bytes``
    truncation in the PIL path. (The PIL path additionally folds in the Pi CPU serial + a
    timestamp as supplementary, non-secret starting bytes; those are deliberately dropped here —
    the camera-frame chain is the entropy foundation and a public serial / low-entropy timestamp
    do not materially strengthen it.)
    """
    entropy = hashlib.sha256(chain + frame).digest()
    if mnemonic_length == 12:
        # 12-word mnemonic only uses the first 128 bits / 16 bytes of entropy
        entropy = entropy[:16]
    return generate_mnemonic_from_bytes(entropy, wordlist_language_code)



def generate_mnemonic_from_dice(roll_data: str, wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> list[str]:
    """
        Takes a string of 50 or 99 dice rolls and returns a 12- or 24-word mnemonic.

        Uses the iancoleman.io/bip39 and bitcoiner.guide/seed "Base 10" or "Hex" mode approach:
        * dice rolls are treated as string data.
        * hashed via SHA256.

        Important note: This method is NOT compatible with iancoleman's "Dice" mode.
    """
    entropy_bytes = hashlib.sha256(roll_data.encode()).digest()

    if len(roll_data) == DICE__NUM_ROLLS__12WORD:
        # 12-word mnemonic; only use 128bits / 16 bytes
        entropy_bytes = entropy_bytes[:16]

    # Return as a list
    return bip39.mnemonic_from_bytes(entropy_bytes, wordlist=Seed.get_wordlist(wordlist_language_code)).split()



def generate_mnemonic_from_coin_flips(coin_flips: str, wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> list[str]:
    """
        Takes a string of 128 or 256 0s and 1s and returns a 12- or 24-word mnemonic.

        Uses the iancoleman.io/bip39 and bitcoiner.guide/seed "Binary" mode approach:
        * binary digit stream is treated as string data.
        * hashed via SHA256.
    """
    entropy_bytes = hashlib.sha256(coin_flips.encode()).digest()

    if len(coin_flips) == 128:
        # 12-word mnemonic; only use 128bits / 16 bytes
        entropy_bytes = entropy_bytes[:16]

    # Return as a list
    return bip39.mnemonic_from_bytes(entropy_bytes, wordlist=Seed.get_wordlist(wordlist_language_code)).split()



def get_partial_final_word(coin_flips: str, wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH) -> str:
    """ Look up the partial final word for the given coin flips.
        7 coin flips: 0101010 + **** where the final 4 bits will be replaced with the checksum
        3 coin flips: 010 + ******** where the final 8 bits will be replaced with the checksum
    """
    binary_string = coin_flips + "0" * (11 - len(coin_flips))
    wordlist_index = int(binary_string, 2)

    return Seed.get_wordlist(wordlist_language_code)[wordlist_index]
