from math import prod

import msgpack
import numpy as np

MAX_MESSAGE_BYTES = 16 * 1024 * 1024
MIN_MESSAGE_BYTES = 4096
_ARRAY_EXT = 1
_MAX_NDIM = 32
_DTYPE_SIZES = {"b": {1}, "i": {1, 2, 4, 8}, "u": {1, 2, 4, 8}, "f": {2, 4, 8}}


class CodecError(ValueError):
    """The value cannot be represented by the SDK wire format."""


def _supported_dtype(dtype: np.dtype) -> bool:
    return dtype.metadata is None and dtype.itemsize in _DTYPE_SIZES.get(dtype.kind, ())


def _check_map_keys(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CodecError("map keys must be strings")
            _check_map_keys(item)
    elif isinstance(value, list):
        for item in value:
            _check_map_keys(item)


def pack(frame: dict, *, max_size: int = MAX_MESSAGE_BYTES) -> bytes:
    def encode_array(value: object) -> msgpack.ExtType:
        if not isinstance(value, np.ndarray):
            raise CodecError(f"unsupported value type: {type(value).__name__}")
        if not _supported_dtype(value.dtype) or value.ndim > _MAX_NDIM:
            raise CodecError(f"unsupported array dtype or rank: {value.dtype}, {value.ndim}")
        if value.nbytes > max_size:
            raise CodecError(f"array exceeds {max_size} bytes")
        payload = msgpack.packb(
            [value.dtype.str, list(value.shape), value.nbytes, value.tobytes(order="C")],
            use_bin_type=True,
        )
        return msgpack.ExtType(_ARRAY_EXT, payload)

    try:
        if not isinstance(frame, dict):
            raise CodecError("message must be a map")
        _check_map_keys(frame)
        data = msgpack.packb(frame, default=encode_array, use_bin_type=True, strict_types=True)
    except (TypeError, ValueError, OverflowError, RecursionError) as error:
        raise CodecError(str(error)) from error
    if len(data) > max_size:
        raise CodecError(f"message exceeds {max_size} bytes")
    return data


def _decode_array(code: int, payload: bytes) -> np.ndarray:
    if code != _ARRAY_EXT:
        raise CodecError(f"unsupported extension: {code}")
    header = msgpack.unpackb(payload, raw=False)
    if not isinstance(header, list) or len(header) != 4:
        raise CodecError("array requires dtype, shape, byte length and data")
    dtype_name, shape, size, data = header
    if not isinstance(dtype_name, str) or len(dtype_name) > 4:
        raise CodecError("invalid array dtype")
    dtype = np.dtype(dtype_name)
    if not _supported_dtype(dtype) or dtype.str != dtype_name:
        raise CodecError(f"unsupported array dtype: {dtype_name}")
    if (
        not isinstance(shape, list)
        or len(shape) > _MAX_NDIM
        or any(type(dim) is not int or dim < 0 for dim in shape)
    ):
        raise CodecError("invalid array shape")
    if (
        not isinstance(data, bytes)
        or type(size) is not int
        or len(data) != size
        or prod(shape) * dtype.itemsize != size
    ):
        raise CodecError("array byte length does not match dtype and shape")
    return np.frombuffer(data, dtype=dtype).reshape(shape).copy()


def _decode_map(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise CodecError("map keys must be unique strings")
        result[key] = value
    return result


def unpack(data: bytes | str, *, max_size: int = MAX_MESSAGE_BYTES) -> dict:
    if not isinstance(data, bytes):
        raise CodecError("expected a binary MessagePack frame")
    if len(data) > max_size:
        raise CodecError(f"message exceeds {max_size} bytes")
    try:
        frame = msgpack.unpackb(
            data, raw=False, ext_hook=_decode_array, object_pairs_hook=_decode_map
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise CodecError(str(error)) from error
    if not isinstance(frame, dict):
        raise CodecError("message must be a map")
    return frame
