"""Atomic run snapshots and durable JSONL, isolated LIVE / SIMULATED modes."""
from contextlib import contextmanager
from datetime import datetime,timezone
import fcntl
import json
import os
from pathlib import Path
import re
import threading

TERMINAL={'SOLVED','RAN_OUT_OF_MOVES','OUT_OF_TIME','ERROR_MODEL','INFRA_ERROR','NOT_RUN'}


class RunStore:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.thread_lock=threading.RLock()
        for mode in ('LIVE','SIMULATED','BASELINE'):(self.root/mode).mkdir(exist_ok=True)

    @contextmanager
    def lock(self):
        with self.thread_lock:
            with (self.root/'.index.lock').open('a') as stream:
                fcntl.flock(stream,fcntl.LOCK_EX)
                try:yield
                finally:fcntl.flock(stream,fcntl.LOCK_UN)

    def execution_lease(self):
        stream=(self.root/'.execution.lock').open('a')
        try:fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:stream.close();return None
        return stream

    def _id(self,run_id):
        if not isinstance(run_id,str) or not re.fullmatch(r'[a-f0-9]{32}',run_id):
            raise ValueError('Invalid run ID')
        return run_id

    def directory(self,run_id):
        self._id(run_id)
        for mode in ('LIVE','SIMULATED'):
            p=self.root/mode/run_id
            if p.is_dir():return p
        raise KeyError('Unknown run')

    def atomic(self,path,value):
        temp=path.with_suffix(path.suffix+'.tmp')
        with temp.open('w') as stream:
            json.dump(value,stream,allow_nan=False,ensure_ascii=False)
            stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(temp,path)

    def create(self,run):
        self._id(run['id'])
        path=self.root/run['mode']/run['id']
        path.mkdir(exist_ok=False);(path/'trials').mkdir()
        self.atomic(path/'run.json',run)

    def save(self,run):
        with self.lock():self.atomic(self.directory(run['id'])/'run.json',run)

    def get(self,run_id):
        with self.lock():return json.loads((self.directory(run_id)/'run.json').read_text())

    def runs(self):
        with self.lock():
            return [json.loads(p.read_text()) for mode in ('LIVE','SIMULATED')
                    for p in sorted((self.root/mode).glob('*/run.json'))]

    def save_trial(self,run_id,trial):
        with self.lock():self.atomic(self.directory(run_id)/'trials'/(trial['trial_id']+'.json'),trial)

    def trials(self,run_id):
        with self.lock():return [json.loads(p.read_text()) for p in sorted((self.directory(run_id)/'trials').glob('*.json'))]

    def trial(self,run_id,trial_id):
        if not re.fullmatch(r'[a-f0-9]{32}',trial_id):raise ValueError('Invalid trial ID')
        with self.lock():
            path=self.directory(run_id)/'trials'/(trial_id+'.json')
            return json.loads(path.read_text()) if path.exists() else None

    def append(self,run_id,record):
        """Only move/run records enter records.jsonl; events also include thinking."""
        with self.lock():
            path=self.directory(run_id)
            names=['events.jsonl']+(['records.jsonl'] if record['type'] in ('move','run') else [])
            for name in names:
                with (path/name).open('a') as stream:
                    stream.write(json.dumps(record,allow_nan=False,ensure_ascii=False)+'\n')
                    stream.flush();os.fsync(stream.fileno())

    def events(self,run_id,after=0):
        with self.lock():
            path=self.directory(run_id)/'events.jsonl'
            if not path.exists():return []
            lines=path.read_text().splitlines()
            events=[]
            for i,line in enumerate(lines):
                try:event=json.loads(line)
                except ValueError:
                    # A crash may leave only the final append incomplete; never infer from it.
                    if i==len(lines)-1:break
                    raise
                if i>=after:events.append(dict(event_id=i+1,**event))
            return events

    def recover(self):
        lease=self.execution_lease()
        if lease is None:return
        try:
            for run in self.runs():
                if run['status'] in ('RUNNING','QUEUED'):
                    run['status']='INTERRUPTED';self.save(run)
                    for trial in self.trials(run['id']):
                        if trial['status']=='RUNNING':
                            trial['status']='INFRA_ERROR';trial['error_class']='INTERRUPTED'
                            trial['total_time_s']=trial.get('elapsed_s',0)
                            self.save_trial(run['id'],trial)
                            self.append(run['id'],dict(type='run',**trial))
        finally:lease.close()
