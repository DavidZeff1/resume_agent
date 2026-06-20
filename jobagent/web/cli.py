"""`jobagent web` — serve the local web UI with uvicorn."""

from __future__ import annotations

import argparse


def cmd_web(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "The web UI needs extra packages. Install them with:\n"
            "    .venv/bin/pip install -e '.[web]'"
        )
        return 1

    from .app import create_app

    app = create_app(debug=args.debug)
    url = f"http://{args.host}:{args.port}"
    print(f"jobagent web UI -> {url}   (Ctrl-C to stop)")
    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def register(sub) -> None:
    p = sub.add_parser("web", help="serve the local web UI (Starlette + uvicorn)")
    p.add_argument("--host", default="127.0.0.1", help="bind host (default: 127.0.0.1)")
    p.add_argument("--port", type=int, default=8000, help="bind port (default: 8000)")
    p.add_argument("--no-browser", action="store_true", help="do not auto-open a browser")
    p.add_argument("--debug", action="store_true", help="Starlette debug mode (tracebacks)")
    p.set_defaults(func=cmd_web)
