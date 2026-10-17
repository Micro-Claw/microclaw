"""Real TIFFs shaped by the 2.0.3 TIFF spike and MultipageTiffWriter.close()."""
import time

import numpy as np
import pytest
import tifffile

from microclaw import tools


def write_stack(path, pages=2, *, byteorder='<', markers=True, preallocated=None, description=None):
    # Reserve MM's extended header before the first real TIFF IFD.
    with tifffile.TiffWriter(path, byteorder=byteorder, ome=False) as writer:
        writer.filehandle.seek(40)
        for frame in range(pages):
            writer.write(np.full((512, 512), frame, dtype=np.uint16), contiguous=True,
                         photometric='minisblack', metadata=None,
                         description=description if frame == 0 else None)
    with path.open('r+b') as stream:
        end = stream.seek(0, 2)
        if markers:
            order = 'little' if byteorder == '<' else 'big'
            stream.seek(16)
            stream.write(tools._MDA_DISPLAY_SETTINGS_HEADER.to_bytes(4, order))
            stream.seek(24)
            stream.write(tools._MDA_COMMENTS_HEADER.to_bytes(4, order))
        stream.truncate(end if preallocated is None else preallocated)
    return path


def test_multipage_two_closed_files_count_pages_not_series(tmp_path):
    names = ['run_MMStack_Pos0.ome.tif', 'run_MMStack_Pos0_1.ome.tif']
    uuids = ['urn:uuid:00000000-0000-0000-0000-000000000001',
             'urn:uuid:00000000-0000-0000-0000-000000000002']
    for index, pages in enumerate((2, 3)):
        # Each OME series describes all five frames across both physical files,
        # matching the spike's split-stack series semantics.
        description = f'''<?xml version="1.0" encoding="UTF-8"?>
<OME xmlns="http://www.openmicroscopy.org/Schemas/OME/2016-06" UUID="{uuids[index]}">
<Image ID="Image:0"><Pixels ID="Pixels:0" DimensionOrder="XYZCT" Type="uint16"
SizeX="512" SizeY="512" SizeZ="1" SizeC="1" SizeT="5">
<Channel ID="Channel:0:0" SamplesPerPixel="1"/>
<TiffData IFD="0" FirstT="0" PlaneCount="2"><UUID FileName="{names[0]}">{uuids[0]}</UUID></TiffData>
<TiffData IFD="0" FirstT="2" PlaneCount="3"><UUID FileName="{names[1]}">{uuids[1]}</UUID></TiffData>
</Pixels></Image></OME>'''
        write_stack(tmp_path / names[index], pages, description=description)
    for name, pages in zip(names, (2, 3)):
        with tifffile.TiffFile(tmp_path / name) as stack:
            assert len(stack.pages) == pages
            assert len(stack.series) == 1
            assert len(stack.series[0]) == 5
    assert tools._mda_writer_complete(str(tmp_path), 5, 'MULTIPAGE_TIFF')
    assert not tools._mda_writer_complete(str(tmp_path), 6, 'MULTIPAGE_TIFF')


@pytest.mark.parametrize('markers', [False, True])
def test_multipage_preallocated_is_not_closed(tmp_path, monkeypatch, markers):
    # Small stand-in: never allocate the jar's real 4 GiB on Windows CI.
    size = 4 * 1024 * 1024
    monkeypatch.setattr(tools, '_MDA_MULTIPAGE_PREALLOCATED_BYTES', size)
    write_stack(tmp_path / 'run_MMStack_Pos0.ome.tif', markers=markers, preallocated=size)
    assert not tools._mda_writer_complete(str(tmp_path), 2, 'MULTIPAGE_TIFF')


def test_multipage_every_file_must_be_closed(tmp_path, monkeypatch):
    size = 4 * 1024 * 1024
    monkeypatch.setattr(tools, '_MDA_MULTIPAGE_PREALLOCATED_BYTES', size)
    write_stack(tmp_path / 'run_MMStack_Pos0.ome.tif')
    write_stack(tmp_path / 'run_MMStack_Pos0_1.ome.tif', preallocated=size)
    assert not tools._mda_writer_complete(str(tmp_path), 4, 'MULTIPAGE_TIFF')


def test_multipage_big_endian_closed(tmp_path):
    write_stack(tmp_path / 'run_MMStack_Pos0.ome.tif', byteorder='>')
    assert tools._mda_writer_complete(str(tmp_path), 2, 'MULTIPAGE_TIFF')


@pytest.mark.parametrize('mode', ['MULTIPAGE_TIFF', 'SINGLEPLANE_TIFF_SERIES'])
def test_tiff_truncated_returns_not_yet(tmp_path, mode):
    (tmp_path / 'broken.ome.tif').write_bytes(b'MM\x00')
    assert not tools._mda_writer_complete(str(tmp_path), 1, mode, 512 * 512 * 2)


def test_multipage_empty_is_not_complete(tmp_path):
    assert not tools._mda_writer_complete(str(tmp_path), 0, 'MULTIPAGE_TIFF')


def test_singleplane_default_position_files(tmp_path):
    position = tmp_path / 'Default'
    position.mkdir()
    for frame in range(3):
        tifffile.imwrite(position / f'img_channel000_position000_time{frame:09}_z000.tif',
                         np.zeros((512, 512), np.uint16), byteorder='>')
    assert tools._mda_writer_complete(str(tmp_path), 3, 'SINGLEPLANE_TIFF_SERIES', 512 * 512 * 2)
    assert not tools._mda_writer_complete(str(tmp_path), 4, 'SINGLEPLANE_TIFF_SERIES', 512 * 512 * 2)
    (position / 'img_channel000_position000_time000000001_z000.tif').write_bytes(b'broken')
    assert not tools._mda_writer_complete(str(tmp_path), 3, 'SINGLEPLANE_TIFF_SERIES', 512 * 512 * 2)


def test_singleplane_count_and_size_never_opens_tiff(tmp_path, monkeypatch):
    position = tmp_path / 'Default'
    position.mkdir()
    minimum = 512 * 512 * 2
    image = position / 'img_channel000_position000_time000000000_z000.tif'
    tifffile.imwrite(image, np.zeros((512, 512), np.uint16), byteorder='>')
    opens = []
    original = tifffile.TiffFile
    def observed(*args, **kwargs):
        opens.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(tifffile, 'TiffFile', observed)
    assert tools._mda_writer_complete(str(tmp_path), 1, 'SINGLEPLANE_TIFF_SERIES', minimum)
    assert not opens, 'Single-plane verification must never open freshly written TIFFs'
    image.write_bytes(b'x' * minimum)
    assert tools._mda_writer_complete(str(tmp_path), 1, 'SINGLEPLANE_TIFF_SERIES', minimum)
    image.write_bytes(b'x' * (minimum - 1))
    assert not tools._mda_writer_complete(str(tmp_path), 1, 'SINGLEPLANE_TIFF_SERIES', minimum)
    image.unlink()
    assert not tools._mda_writer_complete(str(tmp_path), 1, 'SINGLEPLANE_TIFF_SERIES', minimum)


def test_singleplane_thousands_stat_pass(tmp_path):
    position = tmp_path / 'Default'
    position.mkdir()
    count = 8500
    for frame in range(count):
        tifffile.imwrite(position / f'img_channel000_position000_time{frame:09}_z000.tif',
                         np.zeros((16, 16), np.uint16), byteorder='>')
    started = time.perf_counter()
    complete = tools._mda_writer_complete(str(tmp_path), count, 'SINGLEPLANE_TIFF_SERIES', 16 * 16 * 2)
    elapsed = time.perf_counter() - started
    print(f'Single-plane verification: {count} small files, {elapsed:.6f} s (no rate assertion)')
    assert complete
