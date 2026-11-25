"""Local DevTools pipe for the packaged Windows launcher."""
import json
import queue
import threading
import time


class DevTools:
    def __init__(self, reader, writer):
        self.writer, self.responses, self.sequence = writer, queue.Queue(), 0
        threading.Thread(target=self.read, args=(reader,), daemon=True).start()

    def read(self, reader):
        saved = b''
        try:
            while chunk := reader.read(32768):
                parts = (saved + chunk).split(bytes(1))
                saved = parts.pop()
                for part in parts:
                    response = json.loads(part)
                    if 'id' in response:
                        self.responses.put(response)
        finally:
            self.responses.put(None)

    def call(self, method, params=None, session=None):
        self.sequence += 1
        request = {'id': self.sequence, 'method': method, 'params': params or {}}
        if session:
            request['sessionId'] = session
        self.writer.write(json.dumps(request).encode() + bytes(1))
        deadline = time.monotonic() + 5
        while True:
            reply = self.responses.get(timeout=max(0, deadline - time.monotonic()))
            if reply is None:
                raise EOFError('Test client closed')
            if reply.get('id') == self.sequence:
                if 'error' in reply:
                    raise RuntimeError(method)
                return reply['result']

    def evaluate(self, session, expression):
        result = self.call('Runtime.evaluate', {'expression': expression, 'returnByValue': True}, session)
        if 'exceptionDetails' in result:
            raise RuntimeError('Renderer script failed')
        return result['result'].get('value')
