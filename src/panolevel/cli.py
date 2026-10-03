"""Command line interface for PanoLevel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .batch import DEFAULT_PTS_SUFFIX, run_batch
from .buildpts import OutputSettings
from .detect import detect_vertical_report
from .pipeline import autolevel_one
from .ptsio import (
    default_output_path,
    image_size,
    insert_vertical_lines,
    list_vertical_lines,
    load_pts,
    position_params,
    save_pts_copy,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="panolevel")
    subparsers = parser.add_subparsers(dest="command", required=True)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect a PTGui .pts file")
    inspect_parser.add_argument("--pts", required=True, type=Path)

    inject_parser = subparsers.add_parser(
        "inject", help="Inject vertical control points into a .pts copy"
    )
    inject_parser.add_argument("--pts", required=True, type=Path)
    inject_parser.add_argument("--lines", required=True, help="x1,y1,x2,y2[;...]")
    inject_parser.add_argument("--out", type=Path, default=None)

    detect_parser = subparsers.add_parser(
        "detect", help="Detect vertical lines in an equirectangular panorama"
    )
    detect_parser.add_argument("--image", required=True, type=Path)
    detect_parser.add_argument("--band-center", type=float, default=0.40)
    detect_parser.add_argument("--band-height", type=float, default=0.34)
    detect_parser.add_argument("--reduce", type=int, default=4, choices=[1, 2, 4, 8])
    detect_parser.add_argument("--angle-tol-deg", type=float, default=20.0)
    detect_parser.add_argument("--min-len-frac", type=float, default=0.30)
    detect_parser.add_argument("--target-columns", type=int, default=16)
    detect_parser.add_argument("--max-lines", type=int, default=12)
    detect_parser.add_argument("--format", choices=["json", "text"], default="json")

    auto_parser = subparsers.add_parser(
        "autolevel",
        help="End-to-end: detect verticals, write a .pts copy + overlay + report",
    )
    auto_parser.add_argument("--image", required=True, type=Path)
    auto_parser.add_argument(
        "--mode",
        choices=["cp", "angle", "rotate"],
        default="cp",
        help="cp=CPs (PTGui optimiert), angle=roll/pitch ins .pts, rotate=PNG direkt",
    )
    auto_parser.add_argument(
        "--pts", type=Path, default=None, help="Existing .pts for this panorama"
    )
    auto_parser.add_argument(
        "--template", type=Path, default=None, help="Settings template for panos without .pts"
    )
    auto_parser.add_argument("--out", type=Path, default=None, help="Output .pts path")
    auto_parser.add_argument("--overlay", type=Path, default=None, help="Overlay PNG path")
    auto_parser.add_argument("--output-format", default="tiff")
    auto_parser.add_argument("--output-datatype", default="u16")
    auto_parser.add_argument("--overlay-reduce", type=int, default=8, choices=[1, 2, 4, 8])
    auto_parser.add_argument("--band-center", type=float, default=0.40)
    auto_parser.add_argument("--band-height", type=float, default=0.34)
    auto_parser.add_argument("--reduce", type=int, default=4, choices=[1, 2, 4, 8])
    auto_parser.add_argument("--angle-tol-deg", type=float, default=20.0)
    auto_parser.add_argument("--min-len-frac", type=float, default=0.30)
    auto_parser.add_argument("--target-columns", type=int, default=16)
    auto_parser.add_argument("--max-lines", type=int, default=12)
    auto_parser.add_argument("--format", choices=["json", "text"], default="json")

    batch_parser = subparsers.add_parser(
        "batch",
        help="Run autolevel over every pano/.pts pair in a folder, collective report",
    )
    batch_parser.add_argument("--folder", required=True, type=Path)
    batch_parser.add_argument(
        "--mode",
        choices=["cp", "angle", "rotate"],
        default="cp",
        help="cp=CPs (PTGui optimiert), angle=roll/pitch ins .pts, rotate=PNG direkt",
    )
    batch_parser.add_argument(
        "--template", type=Path, default=None, help="Settings template for panos without .pts"
    )
    batch_parser.add_argument(
        "--pts-suffix",
        default=DEFAULT_PTS_SUFFIX,
        help="Suffix between pano stem and .pts (default '%(default)s')",
    )
    batch_parser.add_argument(
        "--out-dir", type=Path, default=None, help="Write outputs here instead of beside each pano"
    )
    batch_parser.add_argument("--output-format", default="tiff")
    batch_parser.add_argument("--output-datatype", default="u16")
    batch_parser.add_argument("--overlay-reduce", type=int, default=8, choices=[1, 2, 4, 8])
    batch_parser.add_argument("--band-center", type=float, default=0.40)
    batch_parser.add_argument("--band-height", type=float, default=0.34)
    batch_parser.add_argument("--reduce", type=int, default=4, choices=[1, 2, 4, 8])
    batch_parser.add_argument("--angle-tol-deg", type=float, default=20.0)
    batch_parser.add_argument("--min-len-frac", type=float, default=0.30)
    batch_parser.add_argument("--target-columns", type=int, default=16)
    batch_parser.add_argument("--max-lines", type=int, default=12)
    batch_parser.add_argument("--format", choices=["json", "text"], default="json")

    serve_parser = subparsers.add_parser(
        "serve", help="Local web UI to interactively straighten a pano"
    )
    serve_parser.add_argument(
        "folder_pos",
        nargs="?",
        type=Path,
        default=None,
        metavar="ORDNER",
        help="Ordner mit Panos (Kurzform fuer --folder)",
    )
    serve_parser.add_argument("--image", type=Path, default=None)
    serve_parser.add_argument(
        "--folder",
        type=Path,
        default=None,
        help="Ordner-Modus: alle Bilder nacheinander gerade ziehen",
    )
    serve_parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Ausgabe im Ordner-Modus (Standard: <ORDNER>/../panolevel_gerade)",
    )
    serve_parser.add_argument("--pts", type=Path, default=None)
    serve_parser.add_argument("--template", type=Path, default=None)
    serve_parser.add_argument("--band-center", type=float, default=0.40)
    serve_parser.add_argument("--band-height", type=float, default=0.34)
    serve_parser.add_argument("--reduce", type=int, default=4, choices=[1, 2, 4, 8])
    serve_parser.add_argument("--angle-tol-deg", type=float, default=20.0)
    serve_parser.add_argument("--min-len-frac", type=float, default=0.30)
    serve_parser.add_argument("--target-columns", type=int, default=16)
    serve_parser.add_argument("--max-lines", type=int, default=12)
    serve_parser.add_argument("--port", type=int, default=8765)
    serve_parser.add_argument("--preview-reduce", type=int, default=8, choices=[1, 2, 4, 8])
    serve_parser.add_argument("--no-open", action="store_true", help="Do not open the browser")

    args = parser.parse_args(argv)
    if args.command == "inspect":
        return _inspect(args.pts)
    if args.command == "inject":
        return _inject(args.pts, args.lines, args.out)
    if args.command == "detect":
        return _detect(args)
    if args.command == "autolevel":
        return _autolevel(args, parser)
    if args.command == "batch":
        return _batch(args)
    if args.command == "serve":
        return _serve(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


def _inspect(path: Path) -> int:
    doc = load_pts(path)
    payload = {
        "pts": str(path),
        "image_size": image_size(doc),
        "position_params": position_params(doc),
        "vertical_lines": [
            {"0": [line[0][0], line[0][1]], "1": [line[1][0], line[1][1]]}
            for line in list_vertical_lines(doc)
        ],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _inject(path: Path, line_spec: str, out: Path | None) -> int:
    doc = load_pts(path)
    lines = parse_lines(line_spec)
    inserted = insert_vertical_lines(doc, lines)
    output = save_pts_copy(doc, path, out)
    payload = {
        "input": str(path),
        "output": str(output),
        "default_output": str(default_output_path(path)),
        "requested_lines": len(lines),
        "inserted_lines": inserted,
        "vertical_lines_total": len(list_vertical_lines(doc)),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def _detect(args: argparse.Namespace) -> int:
    report = detect_vertical_report(
        args.image,
        band_center=args.band_center,
        band_height=args.band_height,
        reduce=args.reduce,
        angle_tol_deg=args.angle_tol_deg,
        min_len_frac=args.min_len_frac,
        target_columns=args.target_columns,
        max_lines=args.max_lines,
    )
    if args.format == "json":
        print(report.to_json())
    else:
        state = "unsicher" if report.uncertain else "sicher"
        print(f"image: {report.image_path}")
        print(f"state: {state} confidence={report.confidence:.3f}")
        print(f"lines: {len(report.lines)} columns={report.occupied_columns}")
        print(f"median_angle_deg: {report.median_angle_deg}")
        for idx, line in enumerate(report.lines, 1):
            print(f"{idx}: {line[0][0]},{line[0][1]} -> {line[1][0]},{line[1][1]}")
    return 0


def _detect_kwargs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "band_center": args.band_center,
        "band_height": args.band_height,
        "reduce": args.reduce,
        "angle_tol_deg": args.angle_tol_deg,
        "min_len_frac": args.min_len_frac,
        "target_columns": args.target_columns,
        "max_lines": args.max_lines,
    }


def _autolevel(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.mode in ("cp", "angle") and args.pts is None and args.template is None:
        parser.error(
            f"mode {args.mode} needs either --pts (existing project) or --template "
            "(settings basis for panos without a .pts)"
        )

    payload = autolevel_one(
        args.image,
        mode=args.mode,
        pts=args.pts,
        template=args.template,
        out=args.out,
        overlay=args.overlay,
        output=OutputSettings(fileformat=args.output_format, datatype=args.output_datatype),
        overlay_reduce=args.overlay_reduce,
        detect_kwargs=_detect_kwargs(args),
    )
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        state = "unsicher" if payload["uncertain"] else "sicher"
        print(f"image: {payload['image']}")
        print(
            f"state: {state} confidence={payload['confidence']:.3f} "
            f"lines={payload['lines']} mode={payload['mode']}"
        )
        if payload["roll"] is not None:
            print(f"roll: {payload['roll']:+.2f}  pitch: {payload['pitch']:+.2f}")
        out = payload["outputs"]
        print(f"output: {out.get('image') or out.get('pts')}")
        print(f"overlay: {out['overlay']}")
    return 0


def _batch(args: argparse.Namespace) -> int:
    result = run_batch(
        args.folder,
        mode=args.mode,
        template=args.template,
        pts_suffix=args.pts_suffix,
        output=OutputSettings(fileformat=args.output_format, datatype=args.output_datatype),
        overlay_reduce=args.overlay_reduce,
        detect_kwargs=_detect_kwargs(args),
        out_dir=args.out_dir,
    )
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for entry in result["results"]:
            name = Path(entry["image"]).name
            if entry["status"] in ("fehler", "uebersprungen"):
                print(f"  [{entry['status']}] {name}: {entry['error']}")
            else:
                print(
                    f"  [{entry['status']}] {name}: {entry['lines']} Linien, "
                    f"conf={entry['confidence']:.2f} ({entry['source']})"
                )
        s = result["summary"]
        print(
            f"== {result['total']} Panos | sicher={s['sicher']} unsicher={s['unsicher']} "
            f"fehler={s['fehler']} uebersprungen={s['uebersprungen']} =="
        )
    return 0


def _serve(args: argparse.Namespace) -> int:
    import threading
    import webbrowser

    import uvicorn

    from .server import create_app, create_folder_app

    folder = args.folder or args.folder_pos
    if folder is not None and args.image is not None:
        raise SystemExit("--image und --folder/ORDNER schliessen sich aus")
    if folder is None and args.image is None:
        raise SystemExit("serve braucht ORDNER (bzw. --folder) oder --image")
    if folder is not None:
        try:
            app = create_folder_app(
                folder,
                args.out_dir,
                preview_reduce=args.preview_reduce,
                detect_kwargs=_detect_kwargs(args),
                allowed_hosts=["127.0.0.1", "localhost"],
                template=args.template,
            )
        except (ValueError, FileNotFoundError) as exc:
            raise SystemExit(f"Fehler: {exc}") from exc
        sess = app.state.session
        print(f"{len(sess)} Bilder in {sess.folder}\nErgebnisse: {sess.out_dir}")
    else:
        app = create_app(
            args.image,
            pts=args.pts,
            template=args.template,
            preview_reduce=args.preview_reduce,
            detect_kwargs=_detect_kwargs(args),
        )
    url = f"http://127.0.0.1:{args.port}/"
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print(f"PanoLevel UI: {url}  (Strg+C zum Beenden)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")
    return 0


def parse_lines(raw: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    lines: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for chunk in raw.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = [part.strip() for part in chunk.split(",")]
        if len(parts) != 4:
            raise SystemExit(f"Invalid line {chunk!r}; expected x1,y1,x2,y2")
        try:
            x1, y1, x2, y2 = (int(part) for part in parts)
        except ValueError as exc:
            raise SystemExit(f"Invalid numeric coordinate in line {chunk!r}") from exc
        lines.append(((x1, y1), (x2, y2)))
    if not lines:
        raise SystemExit("No vertical lines supplied")
    return lines


if __name__ == "__main__":
    raise SystemExit(main())
