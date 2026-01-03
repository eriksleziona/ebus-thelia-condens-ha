from collections import deque
import time

events = deque()
WINDOW = 3600


def register(state):
    now = time.time()
    if state == 1:
        events.append(now)
    while events and events[0] < now - WINDOW:
        events.popleft()
    return len(events)
