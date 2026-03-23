// _run.mjs — Cross-platform Python runner for agent-memory hooks
import { execFileSync } from "child_process";
import { existsSync } from "fs";
import { join } from "path";
import { platform } from "os";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

export const PLUGIN_ROOT = join(__dirname, "..");
// CLAUDE_PLUGIN_DATA is set by Claude Code for marketplace installs.
// For local dev, it is unset — Python falls back to PLUGIN_ROOT via _common.py.
export const PLUGIN_DATA = process.env.CLAUDE_PLUGIN_DATA || null;

export function getVenvPython() {
  // 1. PLUGIN_DATA venv (marketplace install)
  if (PLUGIN_DATA) {
    const py = platform() === "win32"
      ? join(PLUGIN_DATA, ".venv", "Scripts", "python.exe")
      : join(PLUGIN_DATA, ".venv", "bin", "python");
    if (existsSync(py)) return py;
  }
  // 2. Local dev venv (scripts/.venv/)
  const localPy = platform() === "win32"
    ? join(__dirname, ".venv", "Scripts", "python.exe")
    : join(__dirname, ".venv", "bin", "python");
  if (existsSync(localPy)) return localPy;
  return null;
}

export function runPython(scriptName, { stdin, timeout } = {}) {
  const py = getVenvPython();
  if (!py) {
    process.stderr.write(
      "agent-memory: venv not found. Run setup or 'uv sync' in scripts/.\n"
    );
    process.exit(0); // Never block Claude
  }
  const script = join(__dirname, scriptName);
  const env = { ...process.env };
  if (PLUGIN_DATA) env.CLAUDE_PLUGIN_DATA = PLUGIN_DATA;
  try {
    const result = execFileSync(py, [script], {
      input: stdin || "",
      timeout: timeout || 10000,
      encoding: "utf8",
      stdio: ["pipe", "pipe", "pipe"],
      env,
    });
    if (result) process.stdout.write(result);
  } catch (e) {
    if (e.stderr) process.stderr.write(e.stderr);
    process.exit(0); // Never block Claude
  }
}
