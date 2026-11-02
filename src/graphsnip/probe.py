"""Start an MCP server exactly as an assistant would and check that it answers, so `doctor` can say why tools are missing."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time


def probe_mcp(command: str, args: list[str], cwd: str, timeout: float = 25.0) -> dict:
    t0 = time.time()
    try:
        proc = subprocess.Popen([command, *args], cwd=cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)
    except FileNotFoundError:
        return {"ok": False, "error": f"command not found: {command}", "stderr": "", "tools": [], "seconds": 0.0}
    except OSError as e:
        return {"ok": False, "error": f"could not start {command}: {e}", "stderr": "", "tools": [], "seconds": 0.0}

    out: queue.Queue = queue.Queue()
    err_lines: list[str] = []

    def pump(stream, sink):
        for ln in stream:
            sink(ln)

    threading.Thread(target=pump, args=(proc.stdout, out.put), daemon=True).start()
    threading.Thread(target=pump, args=(proc.stderr, err_lines.append), daemon=True).start()

    def send(obj):
        try:
            proc.stdin.write(json.dumps(obj) + "\n")
            proc.stdin.flush()
        except OSError:
            pass

    def wait_for(mid):
        end = t0 + timeout
        while time.time() < end:
            try:
                ln = out.get(timeout=0.2)
            except queue.Empty:
                if proc.poll() is not None and out.empty():
                    return None
                continue
            try:
                msg = json.loads(ln)
            except Exception:
                continue
            if isinstance(msg, dict) and msg.get("id") == mid:
                return msg
        return None

    res = {"ok": False, "error": "", "stderr": "", "tools": [], "seconds": 0.0}
    try:
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "graphsnip-doctor", "version": "0"}}})
        init = wait_for(1)
        if init is None:
            res["error"] = ("the server exited before answering" if proc.poll() is not None else f"no answer within {timeout:.0f}s")
        else:
            send({"jsonrpc": "2.0", "method": "notifications/initialized"})
            send({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tl = wait_for(2)
            names = [t.get("name") for t in ((tl or {}).get("result") or {}).get("tools", [])]
            if names:
                res.update(ok=True, tools=names)
            else:
                res["error"] = "the server started but listed no tools"
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            proc.kill()
    res["seconds"] = round(time.time() - t0, 1)
    res["stderr"] = "".join(err_lines)[-800:].strip()
    return res
