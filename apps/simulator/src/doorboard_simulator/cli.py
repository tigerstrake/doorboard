from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from doorboard_simulator.panel import serve_panel
from doorboard_simulator.scenarios import available_scenarios, result_to_json, run_scenario_name


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doorboard-sim")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run = subparsers.add_parser("run", help="run a headless scenario")
    run.add_argument("scenario", choices=available_scenarios())
    run.add_argument("--artifact-root", type=Path, default=Path(".simulator-artifacts"))
    run.add_argument("--output", type=Path)

    subparsers.add_parser("list", help="list available scenarios")

    panel = subparsers.add_parser("panel", help="serve the local simulator control panel")
    panel.add_argument("--host", default="127.0.0.1")
    panel.add_argument("--port", type=int, default=8765)
    panel.add_argument("--artifact-root", type=Path, default=Path(".simulator-artifacts"))
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "list":
        for scenario in available_scenarios():
            print(scenario)
        return
    if args.command == "run":
        result = asyncio.run(run_scenario_name(args.scenario, artifact_root=args.artifact_root))
        rendered = result_to_json(result)
        if args.output is None:
            print(rendered, end="")
            return
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        return
    if args.command == "panel":
        serve_panel(host=args.host, port=args.port, artifact_root=args.artifact_root)


if __name__ == "__main__":
    main()
