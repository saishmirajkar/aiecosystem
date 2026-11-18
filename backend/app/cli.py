"""Operational CLI.

  python -m app.cli seed                     # reference data (permissions, roles, departments, units, settings)
  python -m app.cli bootstrap --company "Acme Forge" --admin-email admin@acme.example --admin-name "Asha" [--demo]
  python -m app.cli export-openapi [path]    # write the OpenAPI document
  python -m app.cli sync-agents              # register agents/tools in the database
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

from app.core.database import SessionLocal


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="above")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    b = sub.add_parser("bootstrap")
    b.add_argument("--company", required=True)
    b.add_argument("--admin-email", required=True)
    b.add_argument("--admin-name", default="Administrator")
    b.add_argument("--currency", default="INR")
    b.add_argument("--timezone", default="Asia/Kolkata")
    b.add_argument("--demo", action="store_true", help="Load synthetic demo data (demo environments only)")
    e = sub.add_parser("export-openapi")
    e.add_argument("path", nargs="?", default="../docs/api/openapi.json")
    sub.add_parser("sync-agents")
    args = parser.parse_args(argv)

    if args.cmd == "seed":
        from app.modules.erp_core.seed import seed_reference_data
        with SessionLocal() as db:
            seed_reference_data(db)
            db.commit()
        print("Reference data seeded.")
    elif args.cmd == "bootstrap":
        from app.modules.erp_core import schemas as s
        from app.modules.erp_core.service import initialize
        password = os.environ.get("ADMIN_PASSWORD") or getpass.getpass("Admin password: ")
        with SessionLocal() as db:
            initialize(db, s.SetupIn(company=s.CompanyIn(name=args.company, currency_code=args.currency, timezone=args.timezone),
                                     admin=s.AdminIn(full_name=args.admin_name, email=args.admin_email, password=password),
                                     load_demo_data=args.demo))
            db.commit()
        print(f"Installation initialised for {args.company}. Admin: {args.admin_email}" + (" (demo data loaded)" if args.demo else ""))
    elif args.cmd == "export-openapi":
        from app.main import app
        with open(args.path, "w") as fh:
            json.dump(app.openapi(), fh, indent=2)
        print(f"OpenAPI written to {args.path}")
    elif args.cmd == "sync-agents":
        from app.orchestrator.bootstrap import sync_registry_to_db
        sync_registry_to_db()
        print("Agent registry synchronised.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
