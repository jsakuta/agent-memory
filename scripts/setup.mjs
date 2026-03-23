// setup.mjs — First-run venv bootstrap for agent-memory
import { execFileSync, spawn } from "child_process";
import { existsSync, copyFileSync, readFileSync, mkdirSync } from "fs";
import { join } from "path";
import { platform } from "os";
import { fileURLToPath } from "url";
import { dirname } from "path";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);
const PLUGIN_ROOT = join(__dirname, "..");
const PLUGIN_DATA = process.env.CLAUDE_PLUGIN_DATA || PLUGIN_ROOT;

function main() {
  // Ensure PLUGIN_DATA directories exist
  for (const d of ["logs", "inject_cache"]) {
    mkdirSync(join(PLUGIN_DATA, d), { recursive: true });
  }

  // Check if venv needs (re)build by comparing pyproject.toml
  const srcManifest = join(PLUGIN_ROOT, "scripts", "pyproject.toml");
  const dstManifest = join(PLUGIN_DATA, "pyproject.toml");
  const srcLock = join(PLUGIN_ROOT, "scripts", "uv.lock");
  const dstLock = join(PLUGIN_DATA, "uv.lock");

  const venvPy = platform() === "win32"
    ? join(PLUGIN_DATA, ".venv", "Scripts", "python.exe")
    : join(PLUGIN_DATA, ".venv", "bin", "python");

  let needsBuild = false;
  if (!existsSync(venvPy)) {
    needsBuild = true;
  } else if (!existsSync(dstManifest)) {
    needsBuild = true;
  } else {
    try {
      const src = readFileSync(srcManifest, "utf8");
      const dst = readFileSync(dstManifest, "utf8");
      if (src !== dst) needsBuild = true;
    } catch {
      needsBuild = true;
    }
  }

  if (!needsBuild) return; // Already up to date

  // Copy pyproject.toml + uv.lock to PLUGIN_DATA
  copyFileSync(srcManifest, dstManifest);
  if (existsSync(srcLock)) copyFileSync(srcLock, dstLock);

  // Try uv sync first, fall back to python -m venv + pip
  try {
    execFileSync("uv", ["sync", "--project", PLUGIN_DATA], {
      stdio: "pipe",
      timeout: 180000,
    });
    process.stderr.write("agent-memory: venv created via uv sync\n");
  } catch {
    const py = findPython();
    if (!py) {
      process.stderr.write(
        "agent-memory: ERROR - Python 3 not found. Install Python 3.12+.\n"
      );
      // Remove copied manifest so next session retries
      try { require("fs").unlinkSync(dstManifest); } catch {}
      return;
    }
    const venvDir = join(PLUGIN_DATA, ".venv");
    if (!existsSync(venvDir)) {
      execFileSync(py, ["-m", "venv", venvDir], {
        stdio: "pipe",
        timeout: 30000,
      });
    }
    const pipPy = platform() === "win32"
      ? join(venvDir, "Scripts", "python.exe")
      : join(venvDir, "bin", "python");
    // pip install from pyproject.toml is not direct; use requirements
    // For now, install the known deps directly
    const deps = [
      "fugashi[unidic-lite]",
      "sqlite-vec",
      "onnxruntime",
      "tokenizers",
    ];
    execFileSync(pipPy, ["-m", "pip", "install", ...deps], {
      stdio: "pipe",
      timeout: 180000,
    });
    process.stderr.write("agent-memory: venv created via pip\n");
  }

  // Copy ONNX model to PLUGIN_DATA if not present
  const modelSrc = join(PLUGIN_ROOT, "models", "ruri-v3-130m");
  const modelDst = join(PLUGIN_DATA, "models", "ruri-v3-130m");
  if (existsSync(modelSrc) && !existsSync(join(modelDst, "model_int8.onnx"))) {
    mkdirSync(modelDst, { recursive: true });
    for (const f of ["model_int8.onnx", "model.onnx", "tokenizer.json"]) {
      const s = join(modelSrc, f);
      if (existsSync(s)) copyFileSync(s, join(modelDst, f));
    }
    process.stderr.write("agent-memory: ONNX model (ruri-v3-130m) copied to data dir\n");
  }

  // Fire-and-forget backfill safety net
  const venvPyFinal = platform() === "win32"
    ? join(PLUGIN_DATA, ".venv", "Scripts", "python.exe")
    : join(PLUGIN_DATA, ".venv", "bin", "python");
  if (existsSync(venvPyFinal)) {
    const env = { ...process.env };
    if (process.env.CLAUDE_PLUGIN_DATA) env.CLAUDE_PLUGIN_DATA = process.env.CLAUDE_PLUGIN_DATA;
    const child = spawn(venvPyFinal, [join(__dirname, "backfill_vec.py")], {
      detached: true,
      stdio: "ignore",
      env,
    });
    child.unref();
  }
}

function findPython() {
  const candidates = platform() === "win32"
    ? ["py", "python3", "python"]
    : ["python3", "python"];
  for (const cmd of candidates) {
    try {
      const v = execFileSync(cmd, ["--version"], {
        encoding: "utf8",
        timeout: 5000,
      });
      if (v.includes("Python 3")) return cmd;
    } catch {}
  }
  return null;
}

main();
