"""Stdlib-only metrics protocol fixture; all observations travel over stdout."""
import json
import sys
from pathlib import Path

job = json.loads(sys.stdin.readline())
def emit(kind, **fields):
    print(json.dumps(dict(protocol=job['protocol'], job_id=job['job_id'], type=kind, **fields)), flush=True)
p = job['parameters']
if job['operation'] != 'self_check':
    for item in p.get('lines', []):
        if item.get('type') == 'result':
            emit('result', state='succeeded', output={}, artifacts=[], input_complete=True)
        else:
            emit('metrics', **item)
    for i in range(p.get('flood', 0)):
        emit('metrics', frames=[i*2000, i*2000+1999], values={p.get('metric', 'flux'): [i % 7]*2000})
    emit('status', message='ready')
    if p.get('gate'):
        # Parent synchronizes via observable status and a file, never a sleep
        # or an assumption that a lifecycle notification reaches a live child.
        import time
        while not Path(p['gate']).exists():
            time.sleep(0.001)
    if p.get('lifecycle'):
        for line in sys.stdin:
            notification = json.loads(line)
            if notification['type'] == 'acquisition' and notification['writer'] == 'finished':
                break
            if notification['type'] in ('writer', 'cancel'):
                break
if p.get('crash'):
    sys.exit(2)
emit('result', state='succeeded', output={}, artifacts=[], **({} if job['operation']=='self_check' else {'input_complete': True}))
