"""Keep the multidict reference-leak regression fixed in our pinned runtime."""
import gc
import sys

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
