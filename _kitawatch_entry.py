import os
import sys
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    # windowed (--noconsole) apps have no stdout on Windows; uvicorn's log
    # formatter calls sys.stdout.isatty() and crashes without one.
    # When the launcher sets KITAWATCH_LOG_DIR, mirror stdout/stderr to
    # <dir>/<name>.log so users have something to paste into bug reports;
    # otherwise fall back to the null device.
    if sys.stdout is None:
        log_dir = os.environ.get("KITAWATCH_LOG_DIR", "")
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
            _log = open(os.path.join(log_dir, os.environ.get("LOG_NAME", "kitawatch-kuhi-api") + ".log"), "a", buffering=1)
            sys.stdout = _log
            sys.stderr = _log
        else:
            sys.stdout = open(os.devnull, "w")
            sys.stderr = open(os.devnull, "w")
    import uvicorn
    import api
    uvicorn.run(api.app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")), log_level=os.environ.get("LOG_LEVEL", "warning"))
