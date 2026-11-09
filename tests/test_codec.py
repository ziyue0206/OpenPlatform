import msgpack
import numpy as np
import pytest
from openplatform_sdk.codec import CodecError, pack, unpack


@pytest.mark.parametrize(
    "array",
    [
        np.arange(30, dtype=">f4").reshape(5, 6)[::2, ::-2],
        np.array(7, dtype="<i2"),
        np.empty((2, 0, 3), dtype=np.uint8),
        np.array([True, False]),
    ],
)
def test_roundtrip_preserves_array_values_shape_and_dtype(array):
    result = unpack(pack({"obs": [{"value": array}]}))["obs"][0]["value"]
    assert result.dtype == array.dtype
    assert result.shape == array.shape
    assert result.flags.writeable
    np.testing.assert_array_equal(result, array)


@pytest.mark.parametrize(
    "header",
    [
        ["|O", [1], 1, b"x"],
        ["<f4", [True], 4, b"xxxx"],
        ["<f4", [-1], 4, b"xxxx"],
        ["<f4", [2], 4, b"xxxx"],
        ["<f4", [1], 5, b"xxxx"],
        ["<f4", [0, 2**63], 0, b""],
    ],
)
def test_rejects_malformed_array_metadata(header):
    payload = msgpack.packb({"obs": msgpack.ExtType(1, msgpack.packb(header))})
    with pytest.raises(CodecError):
        unpack(payload)


@pytest.mark.parametrize("frame", ["text", b"\xc1", b"\x82\xa1x\x01\xa1x\x02", b"\x81\x01\x02"])
def test_rejects_invalid_frames(frame):
    with pytest.raises(CodecError):
        unpack(frame)


def test_rejects_oversize_frames_and_unsupported_values():
    with pytest.raises(CodecError, match="exceeds"):
        pack({"obs": np.zeros(17, dtype=np.uint8)}, max_size=16)
    with pytest.raises(CodecError, match="exceeds"):
        unpack(pack({"obs": "test"}), max_size=4)
    with pytest.raises(CodecError, match="dtype"):
        pack({"obs": np.array([object()], dtype=object)})
    with pytest.raises(CodecError, match="map keys"):
        pack({"obs": {1: 2}})
