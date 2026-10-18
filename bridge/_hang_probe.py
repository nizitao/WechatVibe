"""Run one test file with inflight instrumentation; never block on wait_for_idle."""
import faulthandler
import runpy
import sys
from pathlib import Path

import api_tasks

original_wait = api_tasks.ApiTaskCoordinator.wait_for_idle
original_begin = api_tasks.ApiTaskCoordinator.begin
original_finish = api_tasks.ApiTaskCoordinator.finish


def begin(self):
    original_begin(self)
    print("BEGIN inflight=%d" % self.inflight, flush=True)


def finish(self):
    original_finish(self)
    print("FINISH inflight=%d" % self.inflight, flush=True)


def wait_for_idle(self, timeout):
    print("WAIT inflight=%d" % self.inflight, flush=True)
    return original_wait(self, min(timeout, 2))


api_tasks.ApiTaskCoordinator.begin = begin
api_tasks.ApiTaskCoordinator.finish = finish
api_tasks.ApiTaskCoordinator.wait_for_idle = wait_for_idle


import backend_service as _bs
import time as _time
_orig_run = _bs.Backend._run_model_insights
_orig_bound = _bs.Backend._run_model_insights_bound

def _run(self, *args, **kwargs):
    print("RUN enter %.2f" % _time.monotonic(), flush=True)
    try:
        return _orig_run(self, *args, **kwargs)
    finally:
        print("RUN exit %.2f" % _time.monotonic(), flush=True)

def _bound(self, *args, **kwargs):
    print("BOUND enter %.2f" % _time.monotonic(), flush=True)
    try:
        return _orig_bound(self, *args, **kwargs)
    finally:
        print("BOUND exit %.2f" % _time.monotonic(), flush=True)

_bs.Backend._run_model_insights = _run
_bs.Backend._run_model_insights_bound = _bound

target = Path(__file__).with_name("test_runtime_binding.py")
faulthandler.dump_traceback_later(90, exit=True)
sys.argv = [str(target), "ApiBindingTests.test_switch_away_and_back_still_cancels_old_job"]
runpy.run_path(str(target), run_name="__main__")
