// run-capture.mjs — Stop hook wrapper
import { runPython } from "./_run.mjs";
let stdin = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (d) => (stdin += d));
process.stdin.on("end", () => runPython("capture.py", { stdin, timeout: 4500 }));
