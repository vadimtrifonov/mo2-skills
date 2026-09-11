"""Run directly with system Python; this module does not import MO2 or Qt."""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import json
import math
from pathlib import Path
import sys
import time
import uuid

from wire import Error, PROTOCOL, TERMINAL, VERSION, channel, identifier, read_json, write_json


def alive(pid: int) -> bool:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        return False
    try:
        return kernel.WaitForSingleObject(handle, 0) == 258
    finally:
        kernel.CloseHandle(handle)


class Client:
    def __init__(self, instance: Path, session: str | None = None):
        self.root = channel(instance)
        self.instance = str(instance.resolve())
        try:
            self.endpoint = read_json(self.root / "endpoint.json")
        except FileNotFoundError:
            raise Error("NOT_RUNNING", "Open the MO2 instance with Install Mod enabled.") from None
        if self.endpoint.get("protocol") != PROTOCOL:
            raise Error("PROTOCOL_MISMATCH", "Use the client shipped with the installed plugin.")
        self.session = identifier(session or self.endpoint["session"])
        self.folder = self.root / self.session
        self.operation: str | None = None

    def check_version(self, info: dict):
        if info.get("version") != VERSION:
            raise Error("VERSION_MISMATCH", "The loaded Install Mod version differs from this client's package.",
                        expected_version=VERSION, loaded_version=info.get("version"),
                        session=self.session, active=info.get("active"))

    def request(self, command: str, **arguments) -> dict:
        self.operation = arguments.get("operation")
        if self.session != self.endpoint["session"] or not alive(self.endpoint["pid"]):
            raise Error("SESSION_ENDED", "This MO2 session is no longer running; an unfinished installation has an unknown outcome.",
                        operation=arguments.get("operation"), session=self.session)
        if command == "install":
            self.check_version(self.endpoint)
        request_id = uuid.uuid4().hex
        if command == "install":
            self.operation = request_id
        payload = dict(arguments, id=request_id, command=command, protocol=PROTOCOL,
                       session=self.session, instance=self.instance, expires=time.time() + 10)
        context = {"operation": self.operation, "session": self.session}
        try:
            write_json(self.folder / "requests" / f"{request_id}.json", payload)
        except OSError as exc:
            raise Error("CONNECTION_ERROR", str(exc), **context) from exc
        reply = self.folder / "replies" / f"{request_id}.json"
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            try:
                result = read_json(reply, limit=1024 * 1024)
            except FileNotFoundError:
                time.sleep(0.1)
                continue
            except (OSError, ValueError, Error) as exc:
                raise Error("CONNECTION_ERROR", str(exc), **context) from exc
            try:
                reply.unlink(missing_ok=True)
            except OSError:
                pass
            if "error" in result and "operation" not in result:
                raise Error(**result["error"])
            return result
        raise Error("REQUEST_TIMEOUT", "MO2 did not acknowledge the request. Inspect the operation's status before retrying installation.", **context)

    def status(self, operation: str | None) -> dict:
        self.operation = operation
        if operation:
            identifier(operation)
            try:
                return read_json(self.folder / "results" / f"{operation}.json", 1024 * 1024)
            except FileNotFoundError:
                pass
            except (OSError, ValueError, Error) as exc:
                raise Error("CONNECTION_ERROR", str(exc), operation=operation, session=self.session) from exc
        result = self.request("status", operation=operation)
        if operation is None:
            self.check_version(result)
        return result

    def wait(self, result: dict, seconds: float) -> dict:
        deadline = time.monotonic() + seconds
        while result["status"] not in TERMINAL | {"needs_input"}:
            if time.monotonic() >= deadline:
                return dict(result, wait_expired=True,
                            message="MO2 may still be working. Query this operation's status; do not repeat install.")
            time.sleep(0.2)
            result = self.status(result["operation"])
        return result


def wait_seconds(value: str) -> float:
    seconds = float(value)
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("Wait must be a finite, nonnegative number of seconds.")
    return seconds


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Install Nexus archives through a running MO2 instance.")
    root.add_argument("--instance", type=Path, required=True, help="MO2 base directory")
    root.add_argument("--session", help="session returned by a previous request")
    commands = root.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="create or replace a mod")
    install.add_argument("archive", type=Path)
    install.add_argument("--profile", required=True)
    target = install.add_mutually_exclusive_group(required=True)
    target.add_argument("--name", help="exact new mod name")
    target.add_argument("--replace", help="exact existing mod name")
    install.add_argument("--wait", type=wait_seconds, default=120, help="seconds to wait; zero returns after acceptance")
    status = commands.add_parser("status", help="read instance information or an installation result")
    status.add_argument("operation", nargs="?")
    cancel = commands.add_parser("cancel", help="request cancellation of an installation")
    cancel.add_argument("operation")
    cancel.add_argument("--wait", type=wait_seconds, default=30)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    client = None
    try:
        client = Client(args.instance, args.session)
        if args.command == "install":
            result = client.request("install", archive=str(args.archive.resolve()),
                                    profile=args.profile, name=args.name or args.replace,
                                    replace=args.replace is not None)
            if args.wait:
                result = client.wait(result, args.wait)
        elif args.command == "cancel":
            result = client.request("cancel", operation=identifier(args.operation))
            if args.wait:
                result = client.wait(result, args.wait)
        else:
            result = client.status(args.operation)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return {"complete": 0, "ready": 0, "failed": 1, "cancelled": 1,
                "needs_input": 2}.get(result["status"], 3)
    except (Error, OSError, ValueError, KeyError, KeyboardInterrupt) as exc:
        if isinstance(exc, KeyboardInterrupt):
            exc = Error("INTERRUPTED", "Observation interrupted. MO2 may still be working; query this operation's status.",
                        operation=client.operation if client else getattr(args, "operation", None),
                        session=client.session if client else args.session)
        error = exc.json() if isinstance(exc, Error) else {"code": "CLIENT_ERROR", "message": str(exc)}
        unknown = error["code"] in {"REQUEST_TIMEOUT", "SESSION_ENDED", "CONNECTION_ERROR", "INTERRUPTED"}
        result = {"status": "unknown" if unknown else "failed", "error": error}
        if isinstance(exc, Error):
            result.update(exc.context)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3 if unknown else 1


if __name__ == "__main__":
    sys.exit(main())
