"""Admin CLI for Pi-scoped service tokens.

An HTTP admin endpoint also exists (`/admin/tokens`, see app.py) — this CLI
is the offline alternative for running directly on the NUC without needing
the service up, e.g. for bootstrapping the very first ingest token.

Usage:
    uv run python -m control_plane_api.cli issue-token \\
        --door-id primary --scope ingest --label "door-sync"
    uv run python -m control_plane_api.cli revoke-token <token_id>
    uv run python -m control_plane_api.cli list-tokens [--door-id primary]
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from control_plane_api import tokens as token_store
from control_plane_api.db import make_engine, make_session_factory, session_scope
from control_plane_api.settings import settings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="control-plane-api-admin")
    sub = parser.add_subparsers(dest="command", required=True)

    issue = sub.add_parser("issue-token")
    issue.add_argument("--door-id", required=True)
    issue.add_argument("--scope", required=True, choices=["ingest", "upload", "config"])
    issue.add_argument("--label", default=None)

    revoke = sub.add_parser("revoke-token")
    revoke.add_argument("token_id")

    listing = sub.add_parser("list-tokens")
    listing.add_argument("--door-id", default=None)

    args = parser.parse_args(argv)

    cfg = settings()
    engine = make_engine(cfg.postgres_dsn)
    factory = make_session_factory(engine)

    if args.command == "issue-token":
        with session_scope(factory) as session:
            issued = token_store.issue_and_store(
                session, scope=args.scope, door_id=args.door_id, label=args.label
            )
        print(f"token_id={issued.token_id}")  # noqa: T201
        print(f"token={issued.raw}")  # noqa: T201
        print("Store this token now — it will not be shown again.")  # noqa: T201
        return 0

    if args.command == "revoke-token":
        with session_scope(factory) as session:
            revoked = token_store.revoke(session, token_id=args.token_id)
        print("revoked" if revoked else "not found or already revoked")  # noqa: T201
        return 0 if revoked else 1

    if args.command == "list-tokens":
        with session_scope(factory) as session:
            records = token_store.list_active(session, door_id=args.door_id)
        for r in records:
            print(f"{r.token_id}\t{r.scope}\t{r.door_id}\t{r.label or ''}")  # noqa: T201
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
