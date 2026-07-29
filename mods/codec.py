"""Small text codecs retained for persistent link reactions."""

import base64


def decode_unicode(binary_data: str) -> str:
    result = []
    for binary in binary_data.replace("\n", " ").split():
        try:
            result.append(chr(int(binary, 2)))
        except (ValueError, OverflowError):
            result.append(f"[{binary}]")
    return "".join(result)


def encode_unicode(text: str) -> str:
    return " ".join(bin(ord(character))[2:] for character in text)


def encode_base85(text) -> str:
    value = text.encode("utf-8") if isinstance(text, str) else text
    return base64.b85encode(value).decode("utf-8")


def decode_base85(encoded_text) -> str:
    value = encoded_text.encode("utf-8") if isinstance(encoded_text, str) else encoded_text
    return base64.b85decode(value).decode("utf-8")


decode_dict = {"unicode": decode_unicode, "base85": decode_base85}
encode_dict = {"unicode": encode_unicode, "base85": encode_base85}
