// run-capture.mjs — Stop hook wrapper
// 1. Capture session (FTS5 only) via capture.py
// 2. Fire-and-forget backfill_vec.py (detached, non-blocking)
import { spawn } from "child_process";
import { join } from "path";
import { fileURLToPath } from "url";
import { dirname } from "path";
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
    const child = spawn(py, [join(__dirname, "backfill_vec.py")], {
      detached: true,
      stdio: "ignore",
      env,
    });
    child.unref();
  }
});
