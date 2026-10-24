"""Command-line entrypoint for running Lead2Meeting server, seeding data, or running SLA scans."""

import argparse
import json
import uvicorn
from lead2meeting.config import get_settings
from lead2meeting.database import init_db
from lead2meeting.seed import seed_demo_data
from lead2meeting.services.recovery_service import scan_for_stalled_leads


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Lead2Meeting — B2B Lead-to-Meeting Conversion & Revenue Recovery CLI"
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="Run the FastAPI web server")
    serve_p.add_argument("--host", default=None, help="Bind host (default from env or 127.0.0.1)")
    serve_p.add_argument("--port", type=int, default=None, help="Bind port (default from env or 8000)")
    serve_p.add_argument("--reload", action="store_true", help="Enable auto-reload for development")

    seed_p = sub.add_parser("seed", help="Initialize database schema and load sample B2B data")
    seed_p.add_argument("--reset", action="store_true", help="Wipe existing rows and re-seed")

    sub.add_parser("scan", help="Run SLA watchdog scan to flag stalled leads")

    args = parser.parse_args()
    settings = get_settings()

    if args.command == "seed":
        res = seed_demo_data(force_reset=args.reset)
        print(json.dumps(res, indent=2))
    elif args.command == "scan":
        init_db()
        res = scan_for_stalled_leads()
        print(json.dumps(res, indent=2))
    else:
        host = getattr(args, "host", None) or settings.host
        port = getattr(args, "port", None) or settings.port
        reload_flag = getattr(args, "reload", False)
        uvicorn.run("lead2meeting.app:app", host=host, port=port, reload=reload_flag)


if __name__ == "__main__":
    main()
