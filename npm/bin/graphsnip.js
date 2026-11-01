#!/usr/bin/env node
// Thin launcher: graphsnip is a Python package. Try uvx, then pipx, then an installed python module.
// (It deliberately never calls a bare `graphsnip`, which would resolve back to this script.)
const { spawnSync } = require("child_process");

const args = process.argv.slice(2);
const attempts = [
  ["uvx", ["--from", "graphsnip", "graphsnip", ...args]],
  ["pipx", ["run", "--spec", "graphsnip", "graphsnip", ...args]],
  ["python3", ["-m", "graphsnip", ...args]],
  ["python", ["-m", "graphsnip", ...args]],
];

for (const [cmd, a] of attempts) {
  const r = spawnSync(cmd, a, { stdio: "inherit" });
  if (r.error && r.error.code === "ENOENT") continue; // tool not installed, try the next
  if (cmd.startsWith("python") && r.status === 1 && r.signal == null) {
    // `python -m graphsnip` exits 1 both for "module missing" and for real command errors; the former prints to stderr
    // already via stdio inherit, so just pass the status through.
  }
  process.exit(r.status === null ? 1 : r.status);
}

console.error(
  "graphsnip needs Python 3.10+ with uv or pipx.\n" +
    "  Install one of:  pipx install graphsnip   |   uv tool install graphsnip\n" +
    "  Docs: https://github.com/naubadnithin7/codegraf"
);
process.exit(127);
