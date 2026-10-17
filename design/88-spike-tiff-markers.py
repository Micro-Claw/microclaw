"""88a spike: are MultipageTiffWriter's close-time markers in finished stack files?

Read-only. From MMJ_.jar 2.0.3 (javap): the constructor pre-sizes each stack file to
4294967296 bytes; close() writes DISPLAY_SETTINGS_OFFSET_HEADER (483765892) at byte 16 and
COMMENTS_OFFSET_HEADER (99384722) at byte 24, then truncates the file to its real length.
For every *.tif under the given folder this prints the byte order, the three header ints
(index map at 8, display settings at 16, comments at 24), the size, and whether the size is
still the 4 GiB pre-allocation.
"""
import json
import struct
import sys
from pathlib import Path

PREALLOCATED = 4294967296
out = {}
for path in sorted(Path(sys.argv[1]).rglob('*.tif')):
    with path.open('rb') as f:
        head = f.read(32)
    order = '<' if head[:2] == b'II' else '>'
    ints = {at: struct.unpack(order + 'I', head[at:at + 4])[0] for at in (8, 16, 24)}
    size = path.stat().st_size
    out[str(path.relative_to(sys.argv[1]))] = dict(
        byte_order=head[:2].decode('latin-1'), index_map_header_8=ints[8],
        display_settings_header_16=ints[16], comments_header_24=ints[24], size=size,
        still_preallocated=size == PREALLOCATED,
        closed_markers=ints[16] == 483765892 and ints[24] == 99384722)
    if len(out) >= 6:
        break
print(json.dumps(out, indent=1))
