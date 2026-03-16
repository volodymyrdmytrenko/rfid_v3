import time

class Debounce:
    def __init__(self, ms):
        self.ms = ms
        self.last = 0

    def allowed(self):
        now = time.time() * 1000
        if now - self.last >= self.ms:
            self.last = now
            return True
        return False
