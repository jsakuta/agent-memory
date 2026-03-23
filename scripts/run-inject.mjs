// run-inject.mjs — SessionStart hook wrapper
import { runPython } from "./_run.mjs";
let stdin = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (stdin += d));
process.stdin.on("end", () => runPython("inject.py", { stdin, timeout: 4500 }));
