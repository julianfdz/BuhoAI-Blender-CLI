// Comandos de exportación de buho-blender: optimizar (Unity/web), imprimir (STL/3MF) y visor (HTML con el GLB).
// La lógica de Blender está en lib/buho_export.py; aquí solo se leen las opciones, se llama a Python y,
// en el visor, se escribe el HTML. Sin dependencias.

import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const LIB = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "lib");
const MODEL_VIEWER = "https://unpkg.com/@google/model-viewer@4.0.0/dist/model-viewer.min.js";
const LIMITE_HTML = 16 * 1024 * 1024;

const py = (v) => v === null || v === undefined ? "None" : v === true ? "True" : v === false ? "False"
  : Array.isArray(v) ? `[${v.map(py).join(", ")}]` : JSON.stringify(v);
const str = (v) => (typeof v === "string" ? v : null);
const lista = (v) => (typeof v === "string" ? v.split(",").map((s) => s.trim()).filter(Boolean) : []);
const num = (v, def) => (v === undefined || v === true ? def : Number(v));

// Llama a una función de lib/buho_export.py (recargándola, por si ha cambiado) con argumentos en JSON.
function pyExport(funcion, args) {
  return [
    "import sys as _bs, importlib as _bi, json as _bj",
    `if ${py(LIB)} not in _bs.path:`,
    `    _bs.path.insert(0, ${py(LIB)})`,
    "import buho_export as _bx",
    "_bx = _bi.reload(_bx)",
    `result = _bx.${funcion}(**_bj.loads(${py(JSON.stringify(args))}))`,
  ].join("\n");
}

function abrir(ruta) {
  if (process.platform === "win32") spawn("cmd", ["/c", "start", "", ruta], { detached: true, stdio: "ignore" }).unref();
  else spawn(process.platform === "darwin" ? "open" : "xdg-open", [ruta], { detached: true, stdio: "ignore" }).unref();
}

// Triángulos, vértices, materiales y texturas del GLB (lee el bloque JSON, sin cargar la geometría).
function infoGlb(buf) {
  if (buf.length < 20 || buf.readUInt32LE(0) !== 0x46546c67) throw new Error("no es un GLB (falta la cabecera glTF)");
  const len = buf.readUInt32LE(12);
  const j = JSON.parse(buf.subarray(20, 20 + len).toString("utf8"));
  let tris = 0, verts = 0;
  for (const m of j.meshes || []) for (const p of m.primitives || []) {
    const modo = p.mode ?? 4;
    const pos = j.accessors?.[p.attributes?.POSITION];
    if (pos) verts += pos.count;
    const n = p.indices !== undefined ? j.accessors[p.indices].count : pos?.count || 0;
    if (modo === 4) tris += n / 3;
    else if (modo === 5 || modo === 6) tris += Math.max(0, n - 2);
  }
  return { triangulos: Math.round(tris), vertices: verts, materiales: (j.materials || []).length,
    texturas: (j.images || []).length, animaciones: (j.animations || []).length };
}

const escaparHtml = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function htmlVisor({ titulo, b64, info, bytes }) {
  const t = escaparHtml(titulo);
  const mb = (bytes / 1048576).toFixed(1).replace(".", ",");
  const datos = `${info.triangulos.toLocaleString("es-ES")} triángulos · ${mb} MB` + (info.animaciones ? ` · ${info.animaciones} animación(es)` : "");
  return `<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${t}</title>
<script type="module" src="${MODEL_VIEWER}"></script>
<style>
:root { --fondo: #ececea; --fondo2: #f7f7f5; --texto: #1d2126; --suave: #5d646d; --borde: #d4d4d0; --boton: #ffffff; --acento: #00a39b; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { --fondo: #17191c; --fondo2: #22252a; --texto: #e9eaec; --suave: #a3a8b0; --borde: #34383e; --boton: #2a2e34; --acento: #2fd3c9; } }
:root[data-theme="dark"] { --fondo: #17191c; --fondo2: #22252a; --texto: #e9eaec; --suave: #a3a8b0; --borde: #34383e; --boton: #2a2e34; --acento: #2fd3c9; }
* { box-sizing: border-box; }
html, body { margin: 0; height: 100%; }
body { background: var(--fondo); color: var(--texto); font: 15px/1.4 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; }
header { display: flex; flex-wrap: wrap; align-items: center; gap: 8px 16px; padding: 12px 16px; border-bottom: 1px solid var(--borde); background: var(--fondo2); }
h1 { font-size: 17px; margin: 0; font-weight: 600; flex: 1 1 auto; min-width: 0; overflow-wrap: anywhere; }
.datos { color: var(--suave); font-size: 13px; }
.botones { display: flex; gap: 8px; }
button { font: inherit; font-size: 13px; color: var(--texto); background: var(--boton); border: 1px solid var(--borde); border-radius: 8px; padding: 6px 10px; cursor: pointer; }
button[aria-pressed="true"] { border-color: var(--acento); color: var(--acento); }
main { flex: 1 1 auto; position: relative; min-height: 300px; }
model-viewer { width: 100%; height: 100%; background: radial-gradient(circle at 50% 40%, var(--fondo2), var(--fondo)); --poster-color: transparent; }
.ayuda { position: absolute; left: 16px; bottom: 12px; color: var(--suave); font-size: 12px; pointer-events: none; }
.error { position: absolute; inset: 0; display: none; align-items: center; justify-content: center; text-align: center; padding: 16px; color: var(--suave); }
</style>
</head>
<body>
<header>
  <h1>${t}</h1>
  <span class="datos">${escaparHtml(datos)}</span>
  <div class="botones">
    <button id="giro" aria-pressed="true" title="Girar solo">Girar solo</button>
    <button id="centrar" title="Volver a la vista inicial">Centrar</button>
    <button id="tema" title="Cambiar entre claro y oscuro">Modo oscuro</button>
  </div>
</header>
<main>
  <model-viewer id="mv" camera-controls auto-rotate auto-rotate-delay="0" rotation-per-second="20deg" shadow-intensity="0.8"
    environment-image="neutral" exposure="1" touch-action="pan-y" interaction-prompt="none" alt="${t}"></model-viewer>
  <div class="ayuda">Arrastra para girar · rueda o pellizco para acercar · botón derecho para mover</div>
  <div class="error" id="error">No se ha podido cargar el visor (hace falta conexión para descargar model-viewer de unpkg.com).</div>
</main>
<script id="modelo" type="application/octet-stream">${b64}</script>
<script>
(function () {
  var mv = document.getElementById("mv");
  var b = atob(document.getElementById("modelo").textContent.trim());
  var u = new Uint8Array(b.length);
  for (var i = 0; i < b.length; i++) u[i] = b.charCodeAt(i);
  mv.src = URL.createObjectURL(new Blob([u], { type: "model/gltf-binary" }));
  var giro = document.getElementById("giro");
  giro.onclick = function () { mv.autoRotate = !mv.autoRotate; giro.setAttribute("aria-pressed", String(mv.autoRotate)); };
  document.getElementById("centrar").onclick = function () { mv.cameraOrbit = "auto auto auto"; mv.cameraTarget = "auto auto auto"; mv.fieldOfView = "auto"; mv.jumpCameraToGoal(); };
  var raiz = document.documentElement, tema = document.getElementById("tema");
  function oscuro() { var t = raiz.getAttribute("data-theme"); return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; }
  function rotulo() { tema.textContent = oscuro() ? "Modo claro" : "Modo oscuro"; }
  try { var g = localStorage.getItem("buho-visor-tema"); if (g) raiz.setAttribute("data-theme", g); } catch (e) {}
  rotulo();
  tema.onclick = function () { var n = oscuro() ? "light" : "dark"; raiz.setAttribute("data-theme", n); try { localStorage.setItem("buho-visor-tema", n); } catch (e) {} rotulo(); };
  setTimeout(function () { if (!customElements.get("model-viewer")) document.getElementById("error").style.display = "flex"; }, 8000);
})();
</script>
</body>
</html>
`;
}

async function visor(arg, opts, { execute, fail }) {
  if (!arg) fail("falta la salida .html");
  const salida = path.resolve(arg);
  if (![".html", ".htm"].includes(path.extname(salida).toLowerCase())) fail("el visor sale en .html");
  let glb = str(opts.glb) ? path.resolve(opts.glb) : null;
  let temporal = null, exportado = null, avisoExport = null;
  if (glb) {
    if (!fs.existsSync(glb)) fail(`no existe ${glb}`);
  } else {
    temporal = path.join(os.tmpdir(), `buho_visor_${process.pid}_${Date.now()}.glb`);
    const r = await execute(pyExport("exportar_glb", { salida: temporal, objetos: lista(opts.objetos), coleccion: str(opts.coleccion) }), opts, { guardar: false });
    if (r.status !== "ok") return r;
    glb = temporal;
    exportado = r.result.objetos;
    if (r.result.aviso) avisoExport = r.result.aviso;
  }
  try {
    const buf = fs.readFileSync(glb);
    const info = infoGlb(buf);
    const titulo = str(opts.titulo) || path.basename(str(opts.glb) || salida).replace(/\.(glb|html?)$/i, "");
    const html = htmlVisor({ titulo, b64: buf.toString("base64"), info, bytes: buf.length });
    fs.mkdirSync(path.dirname(salida), { recursive: true });
    fs.writeFileSync(salida, html, "utf8");
    const bytes = fs.statSync(salida).size;
    const result = { visor: salida, bytes, mb: Math.round(bytes / 10485.76) / 100, glb: temporal ? "escena exportada" : glb,
      glb_bytes: buf.length, ...info, visor_3d: MODEL_VIEWER };
    if (exportado) result.objetos = exportado;
    if (avisoExport) result.aviso_materiales = avisoExport;
    if (bytes > LIMITE_HTML) result.aviso = `el HTML ocupa ${result.mb} MB (> 16 MB): no cabe como Artifact; reduce con "optimizar --caras 15000 --texturas 1024 --jpg"`;
    if (opts.abrir) { abrir(salida); result.abierto = true; }
    return { status: "ok", result };
  } finally {
    if (temporal) fs.rmSync(temporal, { force: true });
  }
}

export async function comandoExport(cmd, arg, opts, ctx) {
  const { execute, show, fail } = ctx;
  if (cmd === "visor") return show(await visor(arg, opts, ctx), opts);
  if (cmd === "optimizar") {
    if (!arg) fail("falta la salida (.glb o .fbx)");
    const salida = path.resolve(arg);
    const ext = path.extname(salida).toLowerCase();
    if (![".glb", ".fbx"].includes(ext)) fail("optimizar exporta a .glb o .fbx");
    const metodo = str(opts.metodo) || "auto";
    if (!["auto", "decimate", "remallado"].includes(metodo)) fail("--metodo auto|decimate|remallado");
    const tex = num(opts.texturas, 2048);
    if (![256, 512, 1024, 2048, 4096, 8192].includes(tex)) fail("--texturas 256|512|1024|2048|4096|8192");
    const comparar = opts["sin-comparar"] ? null : (str(opts.comparar) ? path.resolve(opts.comparar) : salida.replace(/\.(glb|fbx)$/i, "_comparacion.png"));
    const args = {
      salida, caras: num(opts.caras, 30000), texturas: tex, objetos: lista(opts.objetos), coleccion: str(opts.coleccion),
      metodo, muestras_ao: num(opts["muestras-ao"], 64), pngs: !!opts.pngs, comparar,
      formato_imagen: opts.jpg ? "jpg" : opts.webp ? "webp" : "png", dejar: !!opts.dejar, nombre: str(opts.nombre),
    };
    return show(await execute(pyExport("optimizar", args), opts, { guardar: !!opts.dejar }), opts);
  }
  if (cmd === "imprimir") {
    if (!arg) fail("falta la salida (.stl o .3mf)");
    const salida = path.resolve(arg);
    if (![".stl", ".3mf"].includes(path.extname(salida).toLowerCase())) fail("imprimir exporta a .stl o .3mf");
    const args = {
      salida, altura_mm: num(opts["altura-mm"], 120), hueco: num(opts.hueco, 0), unir: !!opts.unir,
      objetos: lista(opts.objetos), coleccion: str(opts.coleccion), voxel_mm: opts["voxel-mm"] ? num(opts["voxel-mm"]) : null,
      caras_max: num(opts["caras-max"], 600000), grosor_min: num(opts["grosor-min"], 1), ambos: !!opts.ambos,
      captura: opts["sin-captura"] ? null : (str(opts.captura) ? path.resolve(opts.captura) : salida.replace(/\.(stl|3mf)$/i, "_vista.png")),
      dejar: !!opts.dejar, cerrar_mm: opts["cerrar-mm"] ? num(opts["cerrar-mm"]) : null,
      adaptividad: opts.adaptividad === undefined ? null : num(opts.adaptividad, null), decimar_s: num(opts["decimar-s"], 90),
    };
    if (args.adaptividad !== null && !(args.adaptividad >= 0 && args.adaptividad <= 1)) fail("--adaptividad entre 0 y 1");
    if (!(args.altura_mm > 0)) fail("--altura-mm tiene que ser un número > 0");
    return show(await execute(pyExport("imprimir", args), opts, { guardar: !!opts.dejar }), opts);
  }
  fail(`comando de exportación desconocido: ${cmd}`);
}
