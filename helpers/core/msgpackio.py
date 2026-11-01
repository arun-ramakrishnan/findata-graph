#!/usr/bin/env python3
"""msgpack I/O using msgspec — drop-in internal transport shim (S1)."""

import msgspec

_ENCODER = msgspec.msgpack.Encoder()
_DECODER = msgspec.msgpack.Decoder()


def encode(obj) -> bytes:
    return _ENCODER.encode(obj)


def decode(data: bytes, type_=None):
    try:
        if type_ is not None:
            import msgspec.msgpack as msgpack_mod

            return msgpack_mod.decode(data, type=type_)
        return _DECODER.decode(data)
    except UnicodeDecodeError as exc:
        # A msgpack str chunk carrying invalid UTF-8 raises a bare
        # UnicodeDecodeError — a ValueError SIBLING of DecodeError, not a
        # subclass — so callers catching only DecodeError missed it. The
        # seam unifies to DecodeError: one typed error class for
        # transport corruption (framing, truncation, and now encoding).
        raise msgspec.DecodeError(str(exc)) from exc


def encode_struct(obj) -> bytes:
    return _ENCODER.encode(obj)


def decode_struct(data: bytes, cls):
    # msgspec msgpack Decoder.decode only takes buf; use msgspec.msgpack.decode directly
    import msgspec.msgpack as msgpack_mod

    return msgpack_mod.decode(data, type=cls)
