// run-capture.mjs — Stop hook wrapper
// 1. Capture session (FTS5 only) via capture.py
// 2. Fire-and-forget backfill_vec.py (detached, non-blocking)
import { spawn } from "child_process";
import { join } from "path";
import { fileURLToPath } from "url";
import { dirname } from "path";
import { platform } from "os";
import { existsSync } from "fs";
import { runPython, getVenvPython, PLUGIN_DATA } from "./_run.mjs";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

let stdin = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (stdin += d));
process.stdin.on("end", () => {
  runPython("capture.py", { stdin, timeout: 4500 });

  // fire-and-forget backfill
  const py = getVenvPython();
  if (py) {
    const env = { ...process.env };
    if (PLUGIN_DATA) env.CLAUDE_PLUGIN_DATA = PLUGIN_DATA;
    // On Windows, use pythonw.exe for detached spawn to avoid console window.
    // python.exe + detached: true creates a visible console window that persists.
    let detachedPy = py;
    if (platform() === "win32") {
      const pyw = py.replace(/python\.exe$/i, "pythonw.exe");
      if (existsSync(pyw)) detachedPy = pyw;
    }
    const child = spawn(detachedPy, [join(__dirname, "backfill_vec.py")], {
      detached: true,
      stdio: "ignore",
      windowsHide: true,
      env,
    });
    child.unref();
  }
});
