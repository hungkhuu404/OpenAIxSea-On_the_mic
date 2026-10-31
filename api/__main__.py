"""Loopback-only server entry point."""

import argparse

import uvicorn

from core.config import set_override
from api.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", action="store_true")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if args.public:
        set_override("PUBLIC_MODE", 1)
        set_override("OFFLINE", 1)
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
