"""Keep the multidict reference-leak regression fixed in our pinned runtime."""
import gc
import sys
import json
import subprocess

import pytest
from multidict import CIMultiDict


@pytest.mark.parametrize("operation", [lambda d, rows: rows | d.items(), lambda d, rows: d.items() - rows])
def test_multidict_items_operations_release_operand_references(operation):
    value = object()
    rows = [(f"key-{i}", value) for i in range(32)]
    data = CIMultiDict(seed="test")
    gc.collect()
    before = sys.getrefcount(value)
    result = operation(data, rows)
    del result
    gc.collect()
    assert sys.getrefcount(value) == before


@pytest.mark.parametrize("mode", ["unpackb", "streaming", "stale_stack"])
def test_msgpack_native_cleanup_preserves_owned_values_and_releases_rejected_values(mode):
    # A disposable process bounds both memory use and native-parser failures.
    # Exception checks alone miss CVE-2026-114613's retained value references.
    code = """
import gc,json,sys,tracemalloc
import msgpack
control={'safe':[1,2,b'bytes']}
encoded=msgpack.packb(control,use_bin_type=True)
assert msgpack.unpackb(encoded,raw=False)==control
decoder=msgpack.Unpacker(raw=False);decoder.feed(encoded);assert list(decoder)==[control]
del decoder
if sys.argv[1]=='stale_stack':
    for _ in range(8):
        decoder=msgpack.Unpacker(raw=False)
        decoder.feed(msgpack.packb([[b'owned-value']],use_bin_type=True))
        result=decoder.unpack();inner=result[0];before=sys.getrefcount(inner)
        decoder.feed(bytes([0x91,0xc1]))
        try:decoder.skip()
        except ValueError:pass
        else:raise AssertionError('Malformed skipped object accepted')
        del decoder;gc.collect()
        assert sys.getrefcount(inner)==before
    print(json.dumps({'version':msgpack.__version__,'backend':msgpack.Unpacker.__module__,'retained_bytes':0}))
    sys.exit(0)
payload=msgpack.packb({42:b'A'*(1024*1024)},use_bin_type=True)
tracemalloc.start();gc.collect();before=tracemalloc.get_traced_memory()[0]
for _ in range(8):
    try:
        if sys.argv[1]=='unpackb':msgpack.unpackb(payload)
        else:
            decoder=msgpack.Unpacker();decoder.feed(payload);list(decoder)
    except ValueError:pass
    else:raise AssertionError('Malformed key accepted')
    if sys.argv[1]!='unpackb':del decoder
gc.collect()
print(json.dumps({'version':msgpack.__version__,'backend':msgpack.Unpacker.__module__,'retained_bytes':tracemalloc.get_traced_memory()[0]-before}))
"""
    result = subprocess.run([sys.executable, "-c", code, mode], check=True,
                            capture_output=True, text=True, timeout=20)
    data = json.loads(result.stdout)
    from packaging.version import Version
    assert Version(data["version"]) >= Version("1.2.3")
    if data["backend"] != "msgpack._cmsgpack":
        pytest.skip("Native MessagePack backend is not active; no native-parser proof claimed")
    assert data["retained_bytes"] < 512 * 1024
