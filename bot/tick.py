import time

tick_funcs = []

def main_loop():
    past = time.perf_counter()
    while True:
        for f in tick_funcs:
            f()
        now = time.perf_counter()
        diff, past = now-past, now
        if diff < 1:
            time.sleep(1-diff)

def tick(f):
    tick_funcs.append(f)