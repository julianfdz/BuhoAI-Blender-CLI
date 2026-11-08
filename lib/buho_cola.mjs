// buho_cola: renders largos en segundo plano para buho-blender.
//
// `buho-blender giro x.mp4 ... --en-cola` vuelve al instante con un id y lanza un proceso aparte (este mismo CLI
// en modo trabajador) que espera su turno (de uno en uno, salvo --paralelo), ejecuta la orden con un Blender sin
// ventana y va escribiendo el progreso (fotogramas hechos, %, tiempo restante) leyendo la salida de Blender.
// Registro: %LOCALAPPDATA%\Programs\BuhoBlender\trabajos\<id>.json (+ <id>.log). Sin dependencias.

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawn, spawnSync } from "node:child_process";

export const DIR = process.env.BUHO_BLENDER_TRABAJOS
  || path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local"), "Programs", "BuhoBlender", "trabajos");
const TURNO = () => path.join(DIR, ".turno");
const FINALES = new Set(["hecho", "error", "cancelado", "perdido"]);
const ENCOLABLES = ["giro", "video", "captura", "run", "comparar"];

let actual = null; // { id, trabajo } en el proceso trabajador

const ruta = (id) => path.join(DIR, `${id}.json`);
const rutaLog = (id) => path.join(DIR, `${id}.log`);
export const activo = () => actual !== null;

function leer(id) {
  try { return JSON.parse(fs.readFileSync(ruta(id), "utf8")); } catch { return null; }
}

function escribir(t) {
  fs.mkdirSync(DIR, { recursive: true });
  const tmp = ruta(t.id) + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(t, null, 2));
  fs.renameSync(tmp, ruta(t.id));
}

function vivo(pid) {
  if (!pid) return false;
  try { process.kill(pid, 0); return true; } catch (e) { return e.code === "EPERM"; }
}

function nuevoId() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${Math.random().toString(36).slice(2, 5)}`;
}

// ---------- encolar (proceso que lanza el agente) ----------

// argv: los argumentos originales; guardarCopia: async (ruta) => guarda una copia del Blender abierto.
export async function encolar({ cmd, argv, opts, script, guardarCopia }) {
  if (!ENCOLABLES.includes(cmd)) throw new Error(`--en-cola vale para ${ENCOLABLES.join(", ")}`);
  fs.mkdirSync(DIR, { recursive: true });
  const id = nuevoId();
  let args = argv.filter((a) => a !== "--en-cola" && a !== "--segundo-plano");
  let copia = null;
  if (!opts.fondo && typeof opts.blend !== "string") {
    // En directo: se trabaja sobre una copia de lo que hay abierto, así el Blender del usuario queda libre.
    copia = path.join(DIR, `${id}.blend`);
    await guardarCopia(copia);
    args = args.filter((a, i) => !(a === "--puerto" || args[i - 1] === "--puerto"));
    args.push("--fondo", "--archivo", copia);
  }
  const t = {
    id, estado: "en_cola", comando: cmd, args, cwd: process.cwd(), creado: new Date().toISOString(),
    salida: opts._[1] ? path.resolve(opts._[1]) : null, copia, paralelo: !!opts.paralelo,
    pid: null, total: null, hechos: 0, porcentaje: 0, restante_s: null, log: rutaLog(id),
  };
  escribir(t);
  const hijo = spawn(process.execPath, [script, ...args, "--_trabajo", id], {
    cwd: process.cwd(), detached: true, stdio: "ignore", windowsHide: true,
  });
  hijo.unref();
  t.pid = hijo.pid;
  escribir(t);
  return {
    trabajo: id, estado: "en_cola", comando: cmd, salida: t.salida, copia_de_la_escena: copia,
    ver: `buho-blender cola ver ${id}`, esperar: `buho-blender cola esperar ${id}`, cancelar: `buho-blender cola cancelar ${id}`,
  };
}

// ---------- trabajador ----------

export async function iniciarTrabajo(id) {
  const t = leer(id);
  if (!t) throw new Error("no existe el trabajo " + id);
  actual = { id, t };
  t.pid = process.pid;
  escribir(t);
  process.on("exit", () => {
    soltarTurno();
    const fin = leer(id);
    if (fin && !FINALES.has(fin.estado)) { fin.estado = "error"; fin.error = fin.error || "el proceso terminó sin resultado"; escribir(fin); }
    if (fin && fin.copia) fs.rmSync(fin.copia, { force: true });
  });
  if (!t.paralelo) await esperarTurno(id);
  const ahora = leer(id) || t;
  if (ahora.estado === "cancelado") process.exit(0);
  Object.assign(ahora, { estado: "corriendo", empezado: new Date().toISOString() });
  escribir(ahora);
  actual.t = ahora;
}

async function esperarTurno(id) {
  for (;;) {
    try {
      fs.writeFileSync(TURNO(), JSON.stringify({ id, pid: process.pid }), { flag: "wx" });
      actual.turno = true;
      return;
    } catch {
      let h = null;
      try { h = JSON.parse(fs.readFileSync(TURNO(), "utf8")); } catch { /* a medio escribir */ }
      const otro = h && leer(h.id);
      if (h && (!vivo(h.pid) || !otro || FINALES.has(otro.estado))) { fs.rmSync(TURNO(), { force: true }); continue; }
      const t = leer(id);
      if (t && t.estado === "cancelado") process.exit(0);
      if (t && t.espera_a !== (h && h.id)) { t.espera_a = h && h.id; escribir(t); }
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
}

function soltarTurno() {
  if (!actual || !actual.turno) return;
  try {
    const h = JSON.parse(fs.readFileSync(TURNO(), "utf8"));
    if (h.id === actual.id) fs.rmSync(TURNO(), { force: true });
  } catch { /* nada */ }
  actual.turno = false;
}

export function terminarTrabajo({ estado, resultado, error }) {
  if (!actual) return;
  const t = leer(actual.id) || actual.t;
  if (t.estado === "cancelado") return;
  Object.assign(t, { estado, acabado: new Date().toISOString() });
  if (estado === "hecho") Object.assign(t, { porcentaje: 100, restante_s: 0, hechos: t.total ?? t.hechos });
  if (resultado !== undefined) t.resultado = resultado;
  if (error) t.error = String(error).slice(-4000);
  t.segundos = t.empezado ? Math.round((Date.now() - Date.parse(t.empezado)) / 100) / 10 : null;
  escribir(t);
  soltarTurno();
}

// Lanza Blender sin ventana leyendo su salida en vivo para el progreso. Devuelve {status, stdout, stderr}.
export function blenderConProgreso(blender, args, timeoutMs) {
  const t = actual.t;
  const log = fs.createWriteStream(rutaLog(t.id), { flags: "a" });
  return new Promise((resolve) => {
    const p = spawn(blender, args, { windowsHide: true });
    t.blender_pid = p.pid;
    escribir(t);
    let out = "", err = "", resto = "";
    let t0 = null, frac = 0, ultimo = 0;
    const guardar = (forzar = false) => {
      const ahora = Date.now();
      if (!forzar && ahora - ultimo < 1000) return;
      ultimo = ahora;
      const hecho = t.hechos + frac;
      if (t.total) {
        t.porcentaje = Math.min(99.9, Math.round((hecho / t.total) * 1000) / 10);
        if (t0 && hecho > 0.05) t.restante_s = Math.max(0, Math.round(((ahora - t0) / 1000 / hecho) * (t.total - hecho)));
      }
      t.transcurrido_s = Math.round((ahora - Date.parse(t.empezado || t.creado)) / 1000);
      const disco = leer(t.id);
      if (disco && disco.estado === "cancelado") { try { p.kill(); } catch { /* ya */ } return; }
      escribir(t);
    };
    const linea = (l) => {
      let m;
      if ((m = l.match(/^__BUHO_TOTAL__ (\d+)/))) { t.total = Number(m[1]); t0 = Date.now(); guardar(true); return; }
      if (l.startsWith("__BUHO_RESULT__") || l.startsWith("__BUHO_ERROR__")) return;
      log.write(l + "\n");
      if (/^Fra:\d+/.test(l) && t0 === null) t0 = Date.now();
      if ((m = l.match(/(?:Sample|Rendering)\s+(\d+)\s*\/\s*(\d+)/))) frac = Math.min(0.99, Number(m[1]) / Math.max(1, Number(m[2])));
      // Blender 5.x: "00:24.266  render | Video append frame 93" o "| Saved: '...png'" (antes sin hora delante).
      if (/append frame \d+|(^|\|\s*)Saved: '/i.test(l)) { t.hechos++; frac = 0; guardar(true); return; }
      t.ultima_linea = l.slice(0, 200);
      guardar();
    };
    p.stdout.on("data", (d) => {
      const s = d.toString("utf8");
      out += s;
      const partes = (resto + s).split(/\r?\n|\r/);
      resto = partes.pop();
      partes.forEach(linea);
    });
    p.stderr.on("data", (d) => { err += d.toString("utf8"); log.write(d); });
    const timer = setTimeout(() => { try { p.kill(); } catch { /* ya */ } }, timeoutMs);
    p.on("close", (code) => {
      clearTimeout(timer);
      if (resto) linea(resto);
      log.end();
      guardar(true);
      resolve({ status: code, stdout: out, stderr: err, timedOut: false });
    });
  });
}

// ---------- consultar ----------

function resumen(t) {
  if (!t) return null;
  if (!FINALES.has(t.estado) && !vivo(t.pid)) {
    t.estado = "perdido";
    t.error = t.error || "el proceso ya no existe (¿se apagó el equipo o se mató a mano?)";
    escribir(t);
  }
  const r = {
    id: t.id, estado: t.estado, comando: t.comando, salida: t.salida, creado: t.creado,
    fotogramas: t.total ? `${t.hechos}/${t.total}` : (t.hechos ? `${t.hechos}/?` : null),
    porcentaje: t.porcentaje ?? 0, transcurrido_s: t.segundos ?? t.transcurrido_s ?? null,
    restante_s: FINALES.has(t.estado) ? 0 : (t.restante_s ?? null),
  };
  if (t.espera_a && t.estado === "en_cola") r.esperando_a = t.espera_a;
  if (t.error) r.error = t.error.split("\n").slice(-6).join("\n");
  return r;
}

export function listar() {
  let ids = [];
  try { ids = fs.readdirSync(DIR).filter((f) => f.endsWith(".json")).map((f) => f.slice(0, -5)); } catch { /* vacío */ }
  const ts = ids.map(leer).filter(Boolean).sort((a, b) => String(b.creado).localeCompare(String(a.creado)));
  const lista = ts.map(resumen);
  return {
    carpeta: DIR,
    corriendo: lista.filter((t) => t.estado === "corriendo").length,
    en_cola: lista.filter((t) => t.estado === "en_cola").length,
    trabajos: lista,
  };
}

export function ver(id, lineas = 15) {
  const t = leer(id);
  if (!t) throw new Error("no existe el trabajo " + id);
  const r = { ...resumen(t), args: t.args, cwd: t.cwd, log: t.log };
  if (t.resultado !== undefined) r.resultado = t.resultado;
  try {
    const log = fs.readFileSync(t.log, "utf8").split(/\r?\n/).filter(Boolean);
    r.ultimas_lineas = log.slice(-lineas);
  } catch { /* sin log aún */ }
  return r;
}

export function cancelar(id) {
  const t = leer(id);
  if (!t) throw new Error("no existe el trabajo " + id);
  if (FINALES.has(t.estado)) return { id, estado: t.estado, aviso: "ya había terminado" };
  const antes = t.estado;
  t.estado = "cancelado";
  t.acabado = new Date().toISOString();
  escribir(t);
  // El trabajador y su Blender (árbol de procesos).
  for (const pid of [t.pid, t.blender_pid]) {
    if (vivo(pid)) spawnSync("taskkill", ["/PID", String(pid), "/T", "/F"], { windowsHide: true });
  }
  if (t.copia) fs.rmSync(t.copia, { force: true });
  try {
    const h = JSON.parse(fs.readFileSync(TURNO(), "utf8"));
    if (h.id === id) fs.rmSync(TURNO(), { force: true });
  } catch { /* nada */ }
  return { id, estado: "cancelado", estaba: antes };
}

export async function esperar(id, escribirLinea, cadaMs = 2000) {
  let previo = "";
  for (;;) {
    const t = leer(id);
    if (!t) throw new Error("no existe el trabajo " + id);
    const r = resumen(t);
    const clave = `${r.estado}|${r.fotogramas}|${Math.floor(r.porcentaje)}`;
    if (clave !== previo) { escribirLinea(r); previo = clave; }
    if (FINALES.has(r.estado)) return ver(id, 5);
    await new Promise((res) => setTimeout(res, cadaMs));
  }
}

export function limpiar() {
  const borrados = [];
  for (const t of listar().trabajos) {
    if (!FINALES.has(t.estado)) continue;
    for (const f of [ruta(t.id), rutaLog(t.id), path.join(DIR, `${t.id}.blend`)]) fs.rmSync(f, { force: true });
    borrados.push(t.id);
  }
  return { borrados };
}
