#!/usr/bin/env node
// Thin launcher: code-graf is a Python package. Try uvx, then pipx, then an installed python module.
// (It deliberately never calls a bare `code-graf`, which would resolve back to this script.)
const { spawnSync } = require("child_process");

const args = process.argv.slice(2);
const attempts = [
  ["uvx", ["--from", "code-graf", "code-graf", ...args]],
  ["pipx", ["run", "--spec", "code-graf", "code-graf", ...args]],
  ["python3", ["-m", "code_graf", ...args]],
  ["python", ["-m", "code_graf", ...args]],
];

for (const [cmd, a] of attempts) {
  const r = spawnSync(cmd, a, { stdio: "inherit" });
  if (r.error && r.error.code === "ENOENT") continue; // tool not installed, try the next
  if (cmd.startsWith("python") && r.status === 1 && r.signal == null) {
    // `python -m code_graf` exits 1 both for "module missing" and for real command errors; the former prints to stderr
    // already via stdio inherit, so just pass the status through.
  }
  process.exit(r.status === null ? 1 : r.status);
}

console.error(
  "code-graf needs Python 3.10+ with uv or pipx.\n" +
    "  Install one of:  pipx install code-graf   |   uv tool install code-graf\n" +
    "  Docs: https://github.com/naubadnithin7/codegraf"
);
process.exit(127);
