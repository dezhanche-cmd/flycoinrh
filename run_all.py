"""
Container entrypoint: the fly roamer.

One container, one process. roam.py drives the connectome around the web and
serves its telemetry. If a run dies it is restarted after a short pause.

FLY_ALLOW_BROWSER=1 must be set, or the browser won't open.
"""
import os
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = "4660"
PORT = os.environ.get("PORT", DEFAULT_PORT)
RESTART_AFTER_S = 10
RESTART_MAX_S = 600
HEALTHY_AFTER_S = 300
GIVE_UP_AFTER = 8


def say(msg):
    try:
        print(f"[run_all] {msg}", flush=True)
    except UnicodeEncodeError:
        print(f"[run_all] {msg}".encode("ascii", "replace").decode(), flush=True)


class Proc:
    def __init__(self, name, argv):
        self.name, self.argv = name, argv
        self.p = None
        self.died_at = None
        self.started_at = None
        self.fast_deaths = 0
        self.gave_up = False

    def start(self):
        say(f"starting {self.name}: {' '.join(self.argv[1:])}")
        self.p = subprocess.Popen(self.argv, cwd=HERE, env=os.environ.copy())
        self.started_at = time.time()
        self.died_at = None

    def delay(self):
        return min(RESTART_MAX_S, RESTART_AFTER_S * (2 ** max(0, self.fast_deaths - 1)))

    def tick(self):
        if self.p is None or self.gave_up:
            return
        rc = self.p.poll()
        if rc is None:
            return
        if self.died_at is None:
            self.died_at = time.time()
            lived = self.died_at - (self.started_at or self.died_at)
            self.fast_deaths = self.fast_deaths + 1 if lived < HEALTHY_AFTER_S else 1
            if self.fast_deaths >= GIVE_UP_AFTER:
                self.gave_up = True
                say(f"{self.name} died {self.fast_deaths} times in a row; giving up")
                return
            say(f"{self.name} exited with {rc} after {lived:.0f}s; restarting in {self.delay()}s")
        elif time.time() - self.died_at >= self.delay():
            self.start()

    def stop(self):
        if self.p and self.p.poll() is None:
            self.p.terminate()
            try:
                self.p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.p.kill()


def main():
    proc = Proc("roam", [sys.executable, "roam.py"])

    stopping = {"now": False}

    def on_signal(signum, _frame):
        stopping["now"] = True
        say(f"signal {signum}, stopping")

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, on_signal)
        except (ValueError, OSError):
            pass

    proc.start()
    rc = 0
    try:
        while not stopping["now"]:
            proc.tick()
            if proc.gave_up:
                rc = 1
                break
            time.sleep(2)
    finally:
        proc.stop()
        say("stopped")
    sys.exit(rc)


if __name__ == "__main__":
    main()
