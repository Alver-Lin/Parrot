"""Private, single-process service owned by the native Parrot app."""

import argparse
import os
import signal
import threading
import time

import uvicorn


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    stopped = threading.Event()

    def watch_parent():
        while not stopped.wait(1):
            if os.getppid() != args.parent_pid:
                os.kill(os.getpid(), signal.SIGINT)
                # A killed GUI must not leave an invisible model process alive.
                time.sleep(15)
                os._exit(1)

    threading.Thread(target=watch_parent, daemon=True, name="desktop-owner").start()
    try:
        uvicorn.run("backend.main:app", host="127.0.0.1", port=args.port, access_log=False)
    finally:
        stopped.set()


if __name__ == "__main__":
    main()
