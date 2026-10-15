"""Stdlib-only growing NDTiff metrics observer; no hardware access."""
import json
import os
from pathlib import Path
import queue
import struct
import sys
import threading
import time
from collections import deque


PER_FRAME = ()
INTERVALS = {}
GAUGE = 'temperature'

# Same native index layout as ndstorage.ndtiff_index.NDTiffIndexEntry.
def index_path(dataset):
    full = dataset / 'Full resolution'
    return (full if full.is_dir() else dataset) / 'NDTiff.index'


def entries(stream):
    start = stream.tell()
    limit = stream.seek(0, os.SEEK_END)
    stream.seek(start)
    while True:
        raw = stream.read(4)
        if len(raw) != 4:
            return
        axes_size, = struct.unpack('I', raw)
        if not axes_size or stream.tell() + axes_size + 4 > limit:
            return
        axes = stream.read(axes_size)
        name_size, = struct.unpack('I', stream.read(4))
        if stream.tell() + name_size + 32 > limit:
            return
        name = stream.read(name_size)
        fields = struct.unpack('IIIIIIII', stream.read(32))
        yield stream.tell(), axes, name, fields


class Observer:
    def __init__(self, dataset, emit, parameters):
        self.dataset, self.emit, self.parameters = Path(dataset), emit, parameters
        self.offset = 0
        self.output = dict(frames_read=0, bytes_read=0)
        self.pending = {name: deque() for name in PER_FRAME}
        self.next_ordinal = {name: 0 for name in PER_FRAME}
        self.next_report = {name: 0.0 for name in PER_FRAME}
        self.next_wire = self.next_gauge = 0.0
        self.last_status = None
        self.broken = False
        self.gauge_dirty = False

    def drain(self):
        # Bound pending values while a slower metric catches up. The fixture's
        # read backlog never changes acquisition or the writer's own cadence.
        if any(len(values) >= 4000 for values in self.pending.values()):
            return False
        path = index_path(self.dataset)
        try:
            stream = path.open('rb')
        except FileNotFoundError:
            return False
        read = 0
        with stream:
            stream.seek(self.offset)
            for end, axes, name, fields in entries(stream):
                json.loads(axes)
                pixel_offset, width, height, pixel_type, compression, *_ = fields
                if compression or pixel_type not in (0, 1):
                    raise ValueError('fixture supports uncompressed uint8/uint16 pixels')
                size = width * height * (1 if pixel_type == 0 else 2)
                try:
                    with (path.parent / name.decode('utf-8')).open('rb') as frame:
                        frame.seek(pixel_offset)
                        pixels = frame.read(size)
                except FileNotFoundError:
                    break
                if len(pixels) != size:
                    break  # Retry this ordinal once its pixels are complete.
                value = (sum(pixels) if pixel_type == 0 else
                         sum(v[0] for v in struct.iter_unpack('<H', pixels))) / (width * height)
                for values in self.pending.values():
                    values.append(value)
                self.offset = end
                self.output['frames_read'] += 1
                self.output['bytes_read'] += size
                self.output['last_frame_bytes'] = size
                self.output['last_mean'] = value
                self.gauge_dirty = True
                read += 1
                if read == 2000:
                    break
        status = f"frames_read={self.output['frames_read']}; index_offset={self.offset}; index_size={path.stat().st_size}"
        if status != self.last_status:
            self.emit('status', message=status)
            self.last_status = status
        return bool(read)

    def report(self, *, final=False):
        now = time.perf_counter()
        flooding = self.parameters.get('flood', False)
        if not flooding and now < self.next_wire:
            return
        for name in PER_FRAME:
            values = self.pending[name]
            if not values or (not final and not flooding and now < self.next_report[name]):
                continue
            first = self.next_ordinal[name]
            # Deliberately omit ordinal 1 only in the explicit broken variant.
            if name == 'flux' and self.parameters.get('broken_contiguity') and not self.broken and first <= 1 < first + len(values):
                if first == 0:
                    count = 1
                else:
                    values.popleft()
                    self.next_ordinal[name] += 1
                    self.broken = True
                    if not values:
                        return
                    first = self.next_ordinal[name]
                    count = 1
            else:
                count = min(len(values), 1 if flooding else 2000)
            batch = [values.popleft() for _ in range(count)]
            payload = {name: batch}
            if name == 'flux':
                payload['total'] = first + count
                if not flooding and first + count == self.output['frames_read']:
                    payload['load'] = self.output['last_frame_bytes']
            self.emit('metrics', frames=[first, first + count - 1], values=payload)
            self.next_ordinal[name] += count
            self.next_report[name] = now + INTERVALS[name]
            self.next_wire = now + 0.101
            return
        if not PER_FRAME and self.gauge_dirty and (final or now >= self.next_gauge):
            last = self.output['frames_read'] - 1
            self.emit('metrics', frames=[last, last], values={GAUGE: self.output['bytes_read'] / self.output['frames_read']})
            self.gauge_dirty = False
            self.next_gauge = now + 0.2
            self.next_wire = now + 0.101
        elif flooding and not final and self.output['frames_read']:
            # Sustained load, including between frames, until writer completion.
            last = self.output['frames_read'] - 1
            self.emit('metrics', frames=[last, last], values={'load': self.output['last_frame_bytes']})


def main():
    job = json.loads(sys.stdin.readline())
    def emit(kind, **fields):
        print(json.dumps(dict(protocol=job['protocol'], job_id=job['job_id'], type=kind, **fields)), flush=True)
    p = job['parameters']
    def result(state='succeeded', output=None, complete=True):
        fields = dict(state=state, output=output or {}, artifacts=[])
        if job['operation'] != 'self_check':
            fields['input_complete'] = complete
        emit('result', **fields)
    if job['operation'] == 'self_check':
        result()
        return
    if 'lines' in p:
        # Explicit protocol-test mode, independent of dataset observation.
        for item in p['lines']:
            if item.get('type') == 'result':
                result()
            else:
                emit('metrics', **item)
        for i in range(p.get('scripted_flood', 0)):
            emit('metrics', frames=[i*2000, i*2000+1999], values={'flux': [i % 7]*2000})
        emit('status', message='ready')
        while p.get('gate') and not Path(p['gate']).exists():
            time.sleep(0.001)
        if p.get('lifecycle'):
            for line in sys.stdin:
                notification = json.loads(line)
                if notification['type'] in ('writer', 'cancel') or (notification['type'] == 'acquisition' and notification['writer'] == 'finished'):
                    break
        if p.get('crash'):
            sys.exit(2)
        result()
        return
    inbox = queue.Queue(maxsize=8)
    def receive():
        for line in sys.stdin:
            inbox.put(json.loads(line))
        inbox.put(None)
    threading.Thread(target=receive, daemon=True).start()
    observer = Observer(job['input']['dataset'], emit, p)
    emit('status', message='ready')
    final = False
    while True:
        try:
            message = inbox.get(timeout=0 if p.get('flood') and observer.output['frames_read'] else 0.02)
        except queue.Empty:
            message = {}
        if message is None or message.get('type') == 'cancel':
            result('cancelled', observer.output, False)
            return
        if message.get('type') == 'writer' or (message.get('type') == 'acquisition' and message.get('writer') == 'finished'):
            final = True
        read = observer.drain()
        observer.report(final=final)
        if final and not read and not any(observer.pending.values()) and not observer.gauge_dirty:
            result(output=observer.output)
            return
        if PER_FRAME:
            observer.gauge_dirty = False


if __name__ == '__main__':
    main()
