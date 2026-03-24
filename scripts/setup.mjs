// setup.mjs — First-run venv bootstrap for agent-memory
import { execFileSync, spawn } from "child_process";
import { existsSync, copyFileSync, readFileSync, mkdirSync, unlinkSync } from "fs";
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
  for (const d of ["data", "logs", "inject_cache"]) {
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

  // Copy uv.lock early (needed by uv sync), but defer pyproject.toml
  // copy until after successful install so failures trigger retry.
  if (existsSync(srcLock)) copyFileSync(srcLock, dstLock);

  // Try uv sync first, fall back to python -m venv + pip
  try {
    // uv sync needs pyproject.toml in place
    copyFileSync(srcManifest, dstManifest);
    execFileSync("uv", ["sync", "--project", PLUGIN_DATA], {
      stdio: "pipe",
      timeout: 180000,
    });
    process.stderr.write("agent-memory: venv created via uv sync\n");
  } catch {
    // Remove manifest so pip path failure also triggers retry
    try { unlinkSync(dstManifest); } catch {}
    const py = findPython();
    if (!py) {
      process.stderr.write(
        "agent-memory: ERROR - Python 3 not found. Install Python 3.12+.\n"
      );
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
    // Ensure pip is available (Homebrew Python may create venv without pip)
    try {
      execFileSync(pipPy, ["-m", "pip", "--version"], {
        stdio: "pipe",
        timeout: 5000,
      });
    } catch {
      execFileSync(pipPy, ["-m", "ensurepip", "--default-pip"], {
        stdio: "pipe",
        timeout: 30000,
      });
    }
    // Install the known deps directly
    const deps = [
      "sqlite-vec",
      "onnxruntime",
      "tokenizers",
    ];
    execFileSync(pipPy, ["-m", "pip", "install", ...deps], {
      stdio: "pipe",
      timeout: 180000,
    });
    process.stderr.write("agent-memory: venv created via pip\n");
    // Mark build complete only after successful install
    copyFileSync(srcManifest, dstManifest);
  }

  // Fire-and-forget: model download + session import + vec embeddings
  // Model download moved here from sync path to avoid hook timeout (120s)
  // killing the process mid-download. FTS5 search works without the model.
  const venvPyFinal = platform() === "win32"
    ? join(PLUGIN_DATA, ".venv", "Scripts", "python.exe")
    : join(PLUGIN_DATA, ".venv", "bin", "python");
  if (existsSync(venvPyFinal)) {
    const env = { ...process.env };
    if (process.env.CLAUDE_PLUGIN_DATA) env.CLAUDE_PLUGIN_DATA = process.env.CLAUDE_PLUGIN_DATA;
    const child = spawn(venvPyFinal, [join(__dirname, "backfill_all.py")], {
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
