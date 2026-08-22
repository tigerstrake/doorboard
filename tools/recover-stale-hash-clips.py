#!/usr/bin/env python3
"""Re-archive clips whose recorded sha256 was corrupted by the T-204 finalize race.

WHY THIS EXISTS, AND WHY IT IS NOT A GENERAL TOOL
=================================================
door-sync refuses to upload a clip whose bytes on disk don't hash to what
door-media declared at finalize ("local checksum mismatch"), and retiring those
items to dead-letter is correct: normally it means a truncated or corrupt file,
and retrying identical bytes cannot fix it.

Before T-204, finalize_recording could run several times concurrently for one
recording. Each run re-globbed segments with a later ended_epoch and rewrote the
SAME out_path, so the file grew between cuts while door-sync recorded the FIRST
cut's hash. The clip on disk is a complete, valid video — a *later* cut of the
same doorbell event. Only the recorded hash is stale.

So this recomputes the true on-disk hash and re-queues. That deliberately
bypasses an integrity guard, which is why it:
  - is dry-run unless you pass --apply
  - ffprobes every file first and refuses anything that isn't a decodable
    stream with a positive duration
  - refuses to touch an item whose stored hash already matches (that would be a
    different failure, and re-queueing it would just loop)
  - backs up both databases before writing

Use it for the T-204 fallout and nothing else. A checksum mismatch on a clip
finalized after T-204 means real corruption; recovering that would be laundering
a bad file into the archive.

Both databases must be updated together. door-media's mark_synced() applies
`WHERE recording_id=? AND sha256=?`, so if its row keeps the stale hash the
upload succeeds but the recording never leaves sync_status='pending' — and
oldest_unsynced_s (MIN(started_at_utc) WHERE sync_status='pending') stays pinned
forever, which is the 31-day reading this is meant to clear.

Files that are simply gone ("local media missing") cannot be recovered. Their
door-media row is marked deleted so it stops counting as an unsynced backlog
item, and the queue row is left dead-lettered as an accurate record.

Usage:
    python3 recover-stale-hash-clips.py                 # report only
    python3 recover-stale-hash-clips.py --apply         # write
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Defaults mirror the services' own resolution, so they stay correct if either
# service is reconfigured only via SSD_DATA_ROOT:
#   door-sync   Settings.queue_db_path -> ssd_data_root / "sync" / "queue.sqlite"
#   door-media  app.py                 -> ssd_data_root / "door_media.db"
SSD_DATA_ROOT = Path("/mnt/ssd/doorboard")
SYNC_DB = SSD_DATA_ROOT / "sync" / "queue.sqlite"
MEDIA_DB = SSD_DATA_ROOT / "door_media.db"

# Thumbnail rows are handled the same as clips: the queue already carries each
# item's own local_path and recording_id, so nothing here needs to re-derive
# door_sync.engine's uuid5 thumbnail item_id.


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe(path: Path) -> tuple[bool, str]:
    """Is this a decodable stream with a positive duration?

    The whole premise is that these files are fine and only the hash is stale.
    If ffprobe can't make sense of one, the premise is wrong for that file and
    it must stay dead-lettered.
    """
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_type,codec_name",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"ffprobe failed to run: {exc}"
    if out.returncode != 0:
        # ffprobe's stderr is multi-line and ends with the path; flatten it so
        # the per-item report stays one line per item.
        reason = " ".join((out.stderr or "").split())[:120]
        return False, f"ffprobe rejected the file: {reason}"
    try:
        meta = json.loads(out.stdout or "{}")
    except json.JSONDecodeError:
        return False, "ffprobe emitted unparseable output"
    streams = meta.get("streams") or []
    if not streams:
        return False, "no decodable streams"
    codecs = ",".join(f"{s.get('codec_type')}:{s.get('codec_name')}" for s in streams)
    duration = meta.get("format", {}).get("duration")
    if path.suffix.lower() in (".jpg", ".jpeg"):
        # Stills legitimately have no meaningful duration.
        return True, codecs
    try:
        if float(duration) <= 0:
            return False, f"non-positive duration ({duration})"
    except (TypeError, ValueError):
        return False, f"unreadable duration ({duration!r})"
    return True, f"{codecs} {float(duration):.2f}s"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write changes (default: report only)")
    ap.add_argument("--ssd-root", type=Path, default=SSD_DATA_ROOT)
    ap.add_argument("--sync-db", type=Path, default=SYNC_DB)
    ap.add_argument("--media-db", type=Path, default=MEDIA_DB)
    args = ap.parse_args()

    for db in (args.sync_db, args.media_db):
        if not db.exists():
            print(f"ERROR: no database at {db}", file=sys.stderr)
            return 2

    sync = sqlite3.connect(args.sync_db)
    sync.row_factory = sqlite3.Row
    media = sqlite3.connect(args.media_db)
    media.row_factory = sqlite3.Row

    rows = sync.execute(
        """SELECT item_id, kind, recording_id, local_path, expected_sha256, last_error
           FROM queue_item
           WHERE target='nas' AND status='dead_letter'
           ORDER BY created_at"""
    ).fetchall()

    if not rows:
        print("no dead-lettered NAS items — nothing to do")
        return 0

    print(f"{len(rows)} dead-lettered NAS item(s)\n")
    requeue: list[tuple[str, str]] = []  # (item_id, true_sha)
    abandon: set[str] = set()  # recording_ids whose files are gone
    skipped: list[tuple[str, str]] = []

    for row in rows:
        item_id = row["item_id"]
        rel = row["local_path"]
        label = f"{row['kind']:<9} {rel or '(no path)'}"
        if not rel:
            skipped.append((item_id, "no local_path recorded"))
            print(f"SKIP     {label} — no local_path")
            continue

        abs_path = (args.ssd_root / rel).resolve()
        if not abs_path.exists():
            rid = row["recording_id"]
            if rid:
                abandon.add(rid)
            print(f"GONE     {label} — file missing, will mark the recording deleted")
            continue

        ok, detail = probe(abs_path)
        if not ok:
            skipped.append((item_id, detail))
            print(f"REFUSE   {label} — {detail}")
            continue

        true_sha = sha256_file(abs_path)
        if row["expected_sha256"] and true_sha == row["expected_sha256"]:
            skipped.append((item_id, "stored hash already matches — different failure"))
            print(f"SKIP     {label} — hash already matches, not a T-204 case")
            continue

        requeue.append((item_id, true_sha))
        print(f"RECOVER  {label}")
        print(f"         valid: {detail}")
        print(f"         stored {str(row['expected_sha256'])[:16]}… -> true {true_sha[:16]}…")

    print()
    print(
        f"summary: {len(requeue)} to re-queue, {len(abandon)} unrecoverable, {len(skipped)} skipped"
    )

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply to commit.")
        return 0

    stamp = time.strftime("%Y%m%dT%H%M%S")
    for db in (args.sync_db, args.media_db):
        backup = db.with_suffix(f"{db.suffix}.bak.recover.{stamp}")
        shutil.copy2(db, backup)
        print(f"backed up {db.name} -> {backup.name}")

    now = datetime.now(UTC).isoformat()

    for item_id, true_sha in requeue:
        sync.execute(
            """UPDATE queue_item
               SET expected_sha256=?, status='pending', attempts=0,
                   permanent_failures=0, next_attempt_at=0,
                   last_error=NULL, error_class=NULL, updated_at=?
               WHERE item_id=?""",
            (true_sha, now, item_id),
        )
        # The clip's item_id IS its recording_id (door_sync.engine); keep
        # door-media's declared hash in step or mark_synced can never apply.
        media.execute(
            "UPDATE recordings SET sha256=? WHERE recording_id=? AND sync_status='pending'",
            (true_sha, item_id),
        )

    for rid in sorted(abandon):
        media.execute(
            """UPDATE recordings SET sync_status='deleted', deleted_at_utc=?
               WHERE recording_id=? AND sync_status='pending'""",
            (now, rid),
        )

    sync.commit()
    media.commit()
    print(f"\napplied: {len(requeue)} re-queued, {len(abandon)} marked deleted")
    print("door-sync will pick the pending items up on its next poll.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
