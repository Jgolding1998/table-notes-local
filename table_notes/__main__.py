import argparse

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="Open Table Notes on this computer.")
    parser.add_argument("--port", type=int, default=8127)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    print(f"Table Notes: http://127.0.0.1:{args.port}")
    print("Press Ctrl+C to stop. Chosen files are processed locally without saving them.")
    uvicorn.run(
        "table_notes.app:app",
        host="127.0.0.1",
        port=args.port,
        access_log=False,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
