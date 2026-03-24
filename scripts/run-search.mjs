// run-search.mjs — Search wrapper (called by skills)
import { execFileSync } from "child_process";
import { existsSync } from "fs";
import { join } from "path";
import { platform } from "os";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PLUGIN_ROOT = join(__dirname, "..");
const PLUGIN_DATA = process.env.CLAUDE_PLUGIN_DATA || null;

function getVenvPython() {
  if (PLUGIN_DATA) {
    const py = platform() === "win32"
      ? join(PLUGIN_DATA, ".venv", "Scripts", "python.exe")
      : join(PLUGIN_DATA, ".venv", "bin", "python");
    if (existsSync(py)) return py;
  }
  const localPy = platform() === "win32"
    ? join(__dirname, ".venv", "Scripts", "python.exe")
    : join(__dirname, ".venv", "bin", "python");
  if (existsSync(localPy)) return localPy;
  // Plugin root venv (setup.mjs defaults PLUGIN_DATA to PLUGIN_ROOT)
  const rootPy = platform() === "win32"
    ? join(PLUGIN_ROOT, ".venv", "Scripts", "python.exe")
    : join(PLUGIN_ROOT, ".venv", "bin", "python");
  if (existsSync(rootPy)) return rootPy;
  return null;
}

const query = process.argv.slice(2).join(" ");
if (!query) {
  process.stderr.write("Usage: node run-search.mjs <query>\n");
  process.exit(1);
}

const py = getVenvPython();
if (!py) {
  process.stderr.write("agent-memory: venv not found\n");
  process.exit(1);
}

try {
  const result = execFileSync(py, [join(__dirname, "search.py"), query], {
    encoding: "utf8",
    timeout: 10000,
    env: PLUGIN_DATA ? { ...process.env, CLAUDE_PLUGIN_DATA: PLUGIN_DATA } : process.env,
  });
  if (result) process.stdout.write(result);
} catch (e) {
  if (e.stderr) process.stderr.write(e.stderr);
  process.exit(1);
}
