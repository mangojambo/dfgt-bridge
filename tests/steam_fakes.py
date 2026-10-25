"""Vetores sintéticos e processos falsos compartilhados pelos testes Steam."""
READY = dict(state='ready', physical_ready=True, binding=dict(ready=True,
    driver='hid-universal-pidff', evdev=['/dev/input/fake'], ff_capabilities=['40000 0'], ff_input=['fake']))


class FakeProc:
    def __init__(self):
        self.values = {55: dict(pid=55, uid=1000, ppid=1, ticks=100, comm='launcher')}
        self.apps = set()
    def get(self, pid): return self.values.get(pid)
    def all(self): return iter(self.values.values())
    def app(self, pid): return pid in self.apps


class FakeRunner:
    def __init__(self, *args, **kwargs):
        self.state = dict(state='starting');self.code = None;self.stops = [];self.closed = False
    def stop(self, now): self.stops.append(now)
    def poll(self, now): return self.code
    def close(self): self.closed = True

