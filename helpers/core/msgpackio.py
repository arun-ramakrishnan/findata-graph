#!/usr/bin/env python3
"""msgpack I/O using msgspec — drop-in internal transport shim (S1)."""

import msgspec

_ENCODER = msgspec.msgpack.Encoder()
_DECODER = msgspec.msgpack.Decoder()


def encode(obj) -> bytes:
    return _ENCODER.encode(obj)


def decode(data: bytes, type_=None):
    if type_ is not None:
        import msgspec.msgpack as msgpack_mod
        return msgpack_mod.decode(data, type=type_)
    return _DECODER.decode(data)


def encode_struct(obj) -> bytes:
    return _ENCODER.encode(obj)


def decode_struct(data: bytes, cls):
    # msgspec msgpack Decoder.decode only takes buf; use msgspec.msgpack.decode directly
    import msgspec.msgpack as msgpack_mod

    return msgpack_mod.decode(data, type=cls)
