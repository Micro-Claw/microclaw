"""The completion predicate against ndstorage 0.1.18's actual disk format."""
from pathlib import Path

import numpy as np
import pytest
from ndstorage import Dataset
from ndstorage.ndtiff_dataset import NDTiffDataset

from microclaw.tools import _mda_index_complete


@pytest.fixture
def dataset(tmp_path):
    # NDTiffDataset.__init__/initialize/put_image/finish are the writable API
    # in the installed source; Dataset is the read-only format-dispatch factory.
    writer = NDTiffDataset(str(tmp_path), name='mda', writable=True)
    writer.initialize({})
    try:
        for frame in range(3):
            writer.put_image({'time': frame}, np.full((512, 512), frame, dtype=np.uint16), {})
        writer.finish()
    finally:
        writer.close()
    return Path(writer.path)


def test_real_ndtiff_image_count(dataset):
    reader = Dataset(str(dataset))
    try:
        assert np.array_equal(reader.read_image(time=2), np.full((512, 512), 2, dtype=np.uint16))
        count = len(reader.index)
        assert count == 3
    finally:
        reader.close()
    assert _mda_index_complete(str(dataset), count) is True


def test_real_ndtiff_incomplete_count(dataset):
    assert _mda_index_complete(str(dataset), 4) is False


def test_missing_ndtiff_index(tmp_path):
    assert _mda_index_complete(str(tmp_path), 0) is False


def test_truncated_ndtiff_index_returns_false(dataset):
    index = dataset / 'NDTiff.index'
    index.write_bytes(index.read_bytes()[:-1])  # inside the last entry's fixed-width tail
    assert _mda_index_complete(str(dataset), 3) is False
