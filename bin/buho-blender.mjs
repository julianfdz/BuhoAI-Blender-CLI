#!/usr/bin/env node
// buho-blender: CLI para trabajar con Blender desde cualquier agente (Claude, Codex, scripts).
// - Con Blender abierto habla con el complemento oficial "MCP" de Blender Lab por su puerto (9876):
//   lo que hace el agente se ve en directo en la ventana.
// - Con --fondo lanza Blender sin ventana (`blender -b`), para generar y exportar en lote.
// Sin dependencias: solo Node 18+.

import net from "node:net";
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { spawn, spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import * as cola from "../lib/buho_cola.mjs";
import { comandoExport } from "./comandos_export.mjs";

const VERSION = "0.5.0";
const HOST = process.env.BUHO_BLENDER_HOST || "127.0.0.1";
let PORT = Number(process.env.BUHO_BLENDER_PORT || 9876);
const BLENDER = process.env.BUHO_BLENDER_PATH || "D:\\Programas\\Blender\\blender.exe";
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LIB = path.join(REPO, "lib");
const RESULT = "__BUHO_RESULT__";
const ERROR = "__BUHO_ERROR__";
let TIMEOUT_MS = 600000;

// ---------- utilidades ----------

function fail(msg, code = 1) {
  if (cola.activo()) cola.terminarTrabajo({ estado: "error", error: String(msg) });
  process.stderr.write(`buho-blender: ${msg}\n`);
  process.exit(code);
}

// Opciones que nunca llevan valor (así "--en-cola salida.mp4" no se come la salida).
const SIN_VALOR = new Set(["en-cola", "segundo-plano", "paralelo", "fondo", "texto", "bucle", "nla", "transparente",
  "con-niebla", "opaco", "descartar", "vacia", "cabezon", "sin-emparentar", "en-sitio", "conservar", "reposo"]);

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next === undefined || next.startsWith("--") || SIN_VALOR.has(key)) out[key] = true;
      else { out[key] = next; i++; }
    } else out._.push(a);
  }
  return out;
}

// Literal de Python a partir de un valor JS (strings, números, listas, null, booleanos).
const py = (v) => v === null || v === undefined ? "None" : v === true ? "True" : v === false ? "False"
  : Array.isArray(v) ? `[${v.map(py).join(", ")}]` : JSON.stringify(v);

const str = (v) => (typeof v === "string" ? v : null);
const lista = (v) => (typeof v === "string" ? v.split(",").map((s) => s.trim()).filter(Boolean) : []);
const num = (v, def) => (v === undefined || v === true ? def : Number(v));

function readCode(arg) {
  if (arg === undefined) fail("falta el código: un .py, un texto entre comillas o '-' para leer de la entrada");
  if (arg === "-") return { code: fs.readFileSync(0, "utf8"), file: null };
  if (fs.existsSync(arg) && fs.statSync(arg).isFile()) return { code: fs.readFileSync(arg, "utf8"), file: path.resolve(arg) };
  return { code: arg, file: null };
}

// Envuelve el código del usuario: `import buho3d` (lib/ del repo) y la carpeta del script en sys.path,
// recarga buho3d si ya estaba importado (en el Blender abierto) y ejecuta con __file__ y __name__ == "__main__".
function pyUsuario(code, file, vars = {}) {
  const nombre = file || "<codigo>";
  return [
    ...Object.entries(vars).map(([k, v]) => `${k} = __import__("json").loads(${py(JSON.stringify(v))})`),
    "import sys as _bs, os as _bo, importlib as _bi",
    `for _bl in ${py([LIB, file ? path.dirname(file) : null].filter(Boolean))}:`,
    "    if _bl not in _bs.path:",
    "        _bs.path.insert(0, _bl)",
    "for _bn in ('buho3d', 'buho_rig', 'buho_apoyo', 'buho_comparar'):",
    "    _bm = _bs.modules.get(_bn)",
    "    if _bm is not None and getattr(_bm, '__file__', None) and _bo.path.exists(_bm.__file__):",
    "        _bi.reload(_bm)",
    `__file__ = ${py(nombre)}`,
    "__name__ = '__main__'",
    `exec(compile(${py(code)}, ${py(nombre)}, 'exec'), globals())`,
  ].join("\n");
}

function pyGuardarDespues(ruta) {
  return `
import bpy as _bb, os as _bo2
_bo2.makedirs(_bo2.path.dirname(${py(ruta)}) or ".", exist_ok=True)
_bb.ops.wm.save_as_mainfile(filepath=${py(ruta)})
if isinstance(globals().get("result"), dict):
    result["guardado"] = _bb.data.filepath
`;
}

// ---------- transporte: Blender abierto (puerto) ----------

function sendToOpen(code, timeoutMs = TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const sock = net.createConnection({ host: HOST, port: PORT });
    const chunks = [];
    const timer = setTimeout(() => { sock.destroy(); reject(new Error("Blender no contesta (tiempo agotado)")); }, timeoutMs);
    sock.on("connect", () => {
      sock.write(JSON.stringify({ type: "execute", code, strict_json: false }) + "\0");
    });
    sock.on("data", (d) => {
      chunks.push(d);
      if (d.includes(0)) sock.end();
    });
    sock.on("end", () => {
      clearTimeout(timer);
      const buf = Buffer.concat(chunks);
      const end = buf.indexOf(0);
      try { resolve(JSON.parse(buf.subarray(0, end < 0 ? buf.length : end).toString("utf8"))); }
      catch (e) { reject(new Error("respuesta no válida de Blender: " + e.message)); }
    });
    sock.on("error", (e) => {
      clearTimeout(timer);
      if (e.code === "ECONNREFUSED")
        reject(new Error(`no hay ningún Blender escuchando en ${HOST}:${PORT}. Ábrelo con "buho-blender abrir" o usa --fondo`));
      else reject(e);
    });
  });
}

// ---------- transporte: Blender sin ventana ----------

function runBackground(code, blendFile) {
  if (!fs.existsSync(BLENDER)) fail(`no encuentro Blender en ${BLENDER} (define BUHO_BLENDER_PATH)`);
  if (blendFile && !fs.existsSync(blendFile)) return { status: "error", message: `no existe ${blendFile}` };
  const wrapper = [
    "import json, sys, traceback, io, contextlib",
    "_ns = {'result': {}, '__name__': '__buho__'}",
    "_buf = io.StringIO()",
    "try:",
    "    with contextlib.redirect_stdout(_buf):",
    `        exec(compile(${py(code)}, '<buho-blender>', 'exec'), _ns)`,
    `    print(${py(RESULT)} + json.dumps({'result': _ns.get('result', {}), 'stdout': _buf.getvalue()}, default=repr))`,
    "except BaseException as _e:",
    "    _msg = ''.join(traceback.format_exception(type(_e), _e, _e.__traceback__.tb_next))",
    `    print(${py(ERROR)} + json.dumps({'message': _msg, 'stdout': _buf.getvalue()}))`,
    "sys.stdout.flush()",
  ].join("\n");
  // El código va en un archivo temporal (la línea de órdenes de Windows no admite más de 32 KB).
  const tmp = path.join(os.tmpdir(), `buho-blender-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.py`);
  fs.writeFileSync(tmp, wrapper, "utf8");
  const args = ["-b", "--factory-startup"];
  if (blendFile) args.push(blendFile);
  args.push("--python-exit-code", "1", "--python", tmp);
  if (cola.activo()) {
    // Trabajo en segundo plano: Blender asíncrono leyendo el progreso de su salida.
    return cola.blenderConProgreso(BLENDER, args, TIMEOUT_MS).then((r) => {
      fs.rmSync(tmp, { force: true });
      return leerSalidaBlender(r);
    });
  }
  let p;
  try { p = spawnSync(BLENDER, args, { encoding: "utf8", maxBuffer: 512 * 1024 * 1024, timeout: TIMEOUT_MS }); }
  finally { fs.rmSync(tmp, { force: true }); }
  return leerSalidaBlender(p);
}

function leerSalidaBlender(p) {
  if (p.error && p.error.code === "ETIMEDOUT") return { status: "error", message: `Blender sin ventana superó el tiempo máximo (${TIMEOUT_MS / 1000} s); súbelo con --timeout` };
  const lines = (p.stdout || "").split(/\r?\n/);
  for (const l of lines) {
    if (l.startsWith(RESULT)) { const r = JSON.parse(l.slice(RESULT.length)); return { status: "ok", result: r.result, stdout: r.stdout }; }
    if (l.startsWith(ERROR)) { const r = JSON.parse(l.slice(ERROR.length)); return { status: "error", message: r.message, stdout: r.stdout }; }
  }
  return { status: "error", message: `Blender terminó sin resultado (código ${p.status}).\n${(p.stderr || "").slice(-2000)}${(p.stdout || "").slice(-2000)}` };
}

async function execute(code, opts, { guardar = true } = {}) {
  if (guardar && typeof opts.guardar === "string") code += "\n" + pyGuardarDespues(path.resolve(opts.guardar));
  if (opts.fondo) {
    const file = typeof opts.archivo === "string" ? path.resolve(opts.archivo) : null;
    return runBackground(code, file);
  }
  return sendToOpen(code);
}

const salidaJSON = (v, opts) => JSON.stringify(v ?? {}, null, opts.json ? 0 : 2);

// Quita del traceback los marcos del puerto MCP y del envoltorio del CLI, que no le dicen nada al agente.
function limpiarTraceback(msg) {
  const out = [];
  let saltando = false;
  for (const l of String(msg).split(/\r?\n/)) {
    if (/^\s+File "/.test(l)) {
      saltando = /mcp_to_blender_server\.py"|File "<string>"|File "<buho-blender>"/.test(l);
      if (saltando) continue;
    } else if (saltando && /^\s{4,}/.test(l)) continue;
    else saltando = false;
    out.push(l);
  }
  // Si no queda ningún marco, sobra la cabecera "Traceback".
  if (!out.some((l) => /^\s+File "/.test(l))) return out.filter((l) => !l.startsWith("Traceback (most recent call last):")).join("\n");
  return out.join("\n");
}

function show(resp, opts) {
  if (cola.activo()) {
    // En un trabajo de la cola el resultado va a su registro (buho-blender cola ver <id>).
    if (resp.status === "ok") cola.terminarTrabajo({ estado: "hecho", resultado: resp.result });
    else cola.terminarTrabajo({ estado: "error", error: limpiarTraceback(resp.message || JSON.stringify(resp)) });
    process.exit(resp.status === "ok" ? 0 : 1);
  }
  if (resp.status !== "ok") {
    if (resp.stdout) process.stderr.write(String(resp.stdout));
    process.stderr.write(limpiarTraceback(resp.message || JSON.stringify(resp)).trimEnd() + "\n");
    if (resp.stderr) process.stderr.write(String(resp.stderr) + "\n");
    process.exit(1);
  }
  if (opts.texto && resp.stdout) process.stdout.write(String(resp.stdout));
  process.stdout.write(salidaJSON(resp.result, opts) + "\n");
}

// ---------- código Python de cada comando ----------

const PY_ESTADO = `
import bpy
result = {
    "blender": bpy.app.version_string,
    "archivo": bpy.data.filepath or None,
    "sin_guardar": bpy.data.is_dirty,
    "escena": bpy.context.scene.name,
    "objetos": len(bpy.data.objects),
    "modo": bpy.context.mode,
    "motor": bpy.context.scene.render.engine,
    "con_ventana": not bpy.app.background,
}
`;

const PY_ESCENA = `
import bpy
def obj(o):
    d = {"nombre": o.name, "tipo": o.type, "padre": o.parent.name if o.parent else None,
         "pos": [round(v, 4) for v in o.location], "rot": [round(v, 4) for v in o.rotation_euler],
         "escala": [round(v, 4) for v in o.scale], "visible": o.visible_get()}
    if o.type == "MESH":
        d["dimensiones"] = [round(v, 4) for v in o.dimensions]
        d["caras"] = len(o.data.polygons)
        d["materiales"] = [m.name for m in o.data.materials if m]
        d["modificadores"] = [m.type for m in o.modifiers]
    if o.type == "EMPTY" and o.instance_type == "COLLECTION" and o.instance_collection:
        d["instancia_de"] = o.instance_collection.name
    if o.library:
        d["enlazado_de"] = o.library.filepath
    return d
result = {"escena": bpy.context.scene.name, "unidades": bpy.context.scene.unit_settings.system,
          "objetos": [obj(o) for o in bpy.context.scene.objects]}
`;

// Piezas comunes de captura y giro: qué se fotografía, encuadre, cámara y luces temporales,
// motor (Cycles con GPU si hay) y cómo dejarlo todo como estaba.
function pyKitFoto({ objetos, coleccion, frame = null, conNiebla = false, encuadrar = [] }) {
  return `
import bpy, math, mathutils, os, tempfile, shutil
sc = bpy.context.scene
_TIPOS = {"MESH", "CURVE", "FONT", "SURFACE", "META", "CURVES", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}
nombres = ${py(objetos)}
coleccion = ${py(coleccion)}
if coleccion and coleccion not in bpy.data.collections:
    raise RuntimeError("no existe la colección " + repr(coleccion))
dentro = set(o.name for o in bpy.data.collections[coleccion].all_objects) if coleccion else None
def _es_instanciador(o):
    return o.type == "EMPTY" and o.instance_type == "COLLECTION" and o.instance_collection is not None
def _elegible(o):
    return ((o.type in _TIPOS or _es_instanciador(o)) and o.visible_get() and not o.name.startswith("_buho")
            and (not nombres or o.name in nombres) and (dentro is None or o.name in dentro))
objs = [o for o in sc.objects if _elegible(o)]
if not objs:
    raise RuntimeError("no hay objetos visibles que fotografiar")

_guardados = []
def _poner(obj, attr, val):
    viejo = getattr(obj, attr)
    setattr(obj, attr, val)
    _guardados.append((obj, attr, viejo))
def _restaurar():
    for obj, attr, val in reversed(_guardados):
        try:
            setattr(obj, attr, val)
        except Exception:
            pass
    _guardados.clear()
# Lo primero que se guarda es lo último que se restaura: que las preferencias no queden "con cambios".
_guardados.append((bpy.context.preferences, "is_dirty", bpy.context.preferences.is_dirty))

def _buho_total(n):
    # Para la cola de renders: cuántos fotogramas va a renderizar (lo lee el CLI de la salida de Blender).
    import sys as _s
    try:
        _s.__stdout__.write("__BUHO_TOTAL__ %d\\n" % int(n))
        _s.__stdout__.flush()
    except Exception:
        pass

_temp = []
def _limpiar_temp():
    for o in reversed(_temp):
        data, tipo = o.data, o.type
        act = o.animation_data.action if o.animation_data else None
        bpy.data.objects.remove(o, do_unlink=True)
        if data is not None and data.users == 0:
            (bpy.data.cameras if tipo == "CAMERA" else bpy.data.lights).remove(data)
        if act is not None and act.users == 0:
            bpy.data.actions.remove(act)
    _temp.clear()

# Lo que no sale en la foto se oculta solo durante el render.
for o in sc.objects:
    if (o.type in _TIPOS or _es_instanciador(o)) and o not in objs and not o.hide_render and not o.name.startswith("_buho"):
        _poner(o, "hide_render", True)

# Niebla, humo y cajas de volumen: salen en la foto pero no cuentan para el encuadre (si no, la figura queda diminuta).
_PAL_NIEBLA = ("humo", "niebla", "neblina", "bruma", "vapor", "nube", "smoke", "fog", "mist", "haze", "cloud", "volumen", "volume")
def _palabras_n(n):
    t = ""
    for i, ch in enumerate(n):
        if ch.isupper() and i and n[i - 1].islower():
            t += " "
        t += ch.lower() if ch.isalnum() else " "
    return t.split()
def _es_niebla(o):
    if any(p.startswith(_PAL_NIEBLA) for p in _palabras_n(o.name)):
        return True
    if o.type == "VOLUME":
        return True
    mats = [s.material for s in getattr(o, "material_slots", []) if s.material is not None]
    if not mats:
        return False
    for m in mats:
        nt = m.node_tree if m.use_nodes else None
        if nt is None:
            return False
        sal = [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output] or [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
        if not sal or sal[0].inputs["Surface"].is_linked or not sal[0].inputs["Volume"].is_linked:
            return False
    return True
_niebla = [o.name for o in objs if _es_niebla(o)]
if not ${py(!!conNiebla)}:
    # Por defecto también se ocultan: una caja de niebla delante de la cámara tapa la figura.
    for _n in _niebla:
        if not bpy.data.objects[_n].hide_render:
            _poner(bpy.data.objects[_n], "hide_render", True)

def _pts_inst(inst):
    # Esquinas en mundo de una instancia del depsgraph (con modificadores).
    m = inst.matrix_world.copy()
    ob = inst.object
    if ob.type != "MESH":
        # En curvas y textos el bound_box puede venir sin calcular (±1): se mide la malla evaluada.
        try:
            me = ob.to_mesh()
        except RuntimeError:
            me = None
        if me is not None and len(me.vertices):
            vs = [v.co for v in me.vertices]
            lo = [min(v[i] for v in vs) for i in range(3)]
            hi = [max(v[i] for v in vs) for i in range(3)]
            ob.to_mesh_clear()
            return [m @ mathutils.Vector((x, y, z)) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]
        if me is not None:
            ob.to_mesh_clear()
    return [m @ mathutils.Vector(c) for c in ob.bound_box]

def _cajas_por_obj(nombres):
    # Caja (min, max) en mundo de cada objeto (con sus instancias), en una sola pasada.
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    cajas = {}
    for inst in dg.object_instances:
        raiz = inst.parent.original if (inst.is_instance and inst.parent) else inst.object.original
        if raiz.name not in nombres or inst.object.type not in _TIPOS:
            continue
        ps = _pts_inst(inst)
        lo = mathutils.Vector((min(p.x for p in ps), min(p.y for p in ps), min(p.z for p in ps)))
        hi = mathutils.Vector((max(p.x for p in ps), max(p.y for p in ps), max(p.z for p in ps)))
        if raiz.name in cajas:
            a, b = cajas[raiz.name]
            lo = mathutils.Vector(map(min, a, lo))
            hi = mathutils.Vector(map(max, b, hi))
        cajas[raiz.name] = (lo, hi)
    return cajas

# Fondos, suelos y estudios: salen en la foto pero no cuentan para el encuadre (un ciclorama de 40 m deja el
# modelo diminuto). Se detectan por la propiedad buho_fondo, por el nombre o por la forma (plano grande,
# caja que lo envuelve todo, losa grande por debajo). --encuadrar "A,B" lo fuerza.
_PAL_FONDO = ("fondo", "suelo", "floor", "ground", "backdrop", "background", "estudio", "studio", "ciclorama",
              "cyclorama", "cyc", "telon", "infinito", "piso")
_encuadrar = ${py(encuadrar || [])}
_fondo = {}
def _marca_fondo(o):
    for d in (o, getattr(o, "data", None)):
        try:
            if d is not None and d.get("buho_fondo"):
                return "propiedad buho_fondo"
        except Exception:
            pass
    if any(p in _PAL_FONDO or p.rstrip("s") in _PAL_FONDO for p in _palabras_n(o.name)):
        return "nombre"
    return None
if True:
    _cand = [o for o in objs if o.name not in _niebla]
    for o in _cand:
        _m = _marca_fondo(o)
        if _m:
            _fondo[o.name] = _m
    _resto = [o for o in _cand if o.name not in _fondo]
    _cj = _cajas_por_obj(set(o.name for o in _resto))
    def _tam(o):
        return max(_cj[o.name][1] - _cj[o.name][0])
    def _motivo_fondo(g):
        # g frente a todo lo que es más pequeño que él: plano grande, caja o cúpula que lo envuelve, losa debajo.
        Dg_ = _tam(g)
        otros = [o for o in _resto if o is not g and o.name in _cj and _tam(o) < Dg_]
        if not otros:
            return None
        omn = mathutils.Vector([min(_cj[o.name][0][i] for o in otros) for i in range(3)])
        omx = mathutils.Vector([max(_cj[o.name][1][i] for o in otros) for i in range(3)])
        So = max(max(omx - omn), 1e-6)
        gmn, gmx = _cj[g.name]
        dmin = min(gmx - gmn)
        tol = 0.02 * So
        envuelve = all(gmn[i] <= omn[i] + tol and gmx[i] >= omx[i] - tol for i in range(3))
        if dmin <= 0.02 * Dg_ and Dg_ >= 1.5 * So:
            return "plano grande"
        if envuelve and Dg_ >= 4 * So:
            return "lo envuelve todo (estudio, ciclorama o cúpula)"
        if gmx.z <= omn.z + 0.05 * So and Dg_ >= 3 * So:
            return "suelo grande por debajo"
        return None
    _cambio = True
    while _cambio and len(_resto) > 1:
        _cambio = False
        for _g in sorted([o for o in _resto if o.type in ("MESH", "SURFACE") and o.name in _cj], key=lambda o: -_tam(o)):
            _m = _motivo_fondo(_g)
            if _m:
                _fondo[_g.name] = _m
                _resto.remove(_g)
                _cambio = True
                break
    _nset = set(o.name for o in _resto)
    if not _nset:
        # Si todo parece fondo, se encuadra todo (un plano solo es el modelo).
        _fondo = {}
        _nset = set(o.name for o in _cand) or set(o.name for o in objs)
if _encuadrar:
    # --encuadrar manda: solo esos cuentan para el encuadre (y no se tratan como fondo).
    _falta = [n for n in _encuadrar if n not in bpy.data.objects]
    if _falta:
        raise RuntimeError("--encuadrar: no existen " + ", ".join(_falta))
    _nset = set(_encuadrar)
    for _n in _encuadrar:
        _fondo.pop(_n, None)
def _info_encuadre():
    d = {}
    if _fondo:
        d["fondo_fuera_del_encuadre"] = _fondo
    if _encuadrar:
        d["encuadrados"] = sorted(_nset)
    if _ajuste_fondo:
        d["camara_dentro_del_fondo"] = dict(_ajuste_fondo)
    return d

# Caja de lo que se ve, con modificadores e instancias (y con la pose del fotograma actual).
def _calc_pts():
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for inst in dg.object_instances:
        raiz = inst.parent.original if (inst.is_instance and inst.parent) else inst.object.original
        if raiz.name not in _nset or inst.object.type not in _TIPOS:
            continue
        pts.extend(_pts_inst(inst))
    if not pts:
        pts = [o.matrix_world @ mathutils.Vector(c) for o in objs if o.name in _nset for c in o.bound_box]
    return pts
def _fijar_caja(nuevos):
    global pts, mn, mx, centro, radio
    pts = nuevos
    mn = mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    mx = mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    centro = (mn + mx) / 2
    radio = max((mx - mn).length / 2, 0.01)
_frame_kit = ${frame === null || frame === undefined ? "None" : Number(frame)}
_frame_viejo_kit = sc.frame_current
if _frame_kit is not None:
    sc.frame_set(int(_frame_kit))
_fijar_caja(_calc_pts())

_VISTAS = {"frente": (0, -1, 0), "atras": (0, 1, 0), "derecha": (1, 0, 0), "izquierda": (-1, 0, 0),
           "arriba": (0, 0.0001, 1), "abajo": (0, 0.0001, -1), "iso": (1, -1, 0.8), "tres_cuartos": (-0.9, -1, 0.45)}
def _dir(v):
    if v not in _VISTAS:
        raise RuntimeError("vista desconocida: %s (usa %s o camara)" % (v, ", ".join(_VISTAS)))
    return mathutils.Vector(_VISTAS[v]).normalized()

def _tangentes(cd, rx, ry):
    t = cd.sensor_width / (2 * cd.lens)
    return (t, t * ry / rx) if rx >= ry else (t * rx / ry, t)

def _encuadre(d, cd, rx, ry):
    # Distancia mínima para que quepan todos los puntos de la caja (con margen) mirando en la dirección -d.
    rot = (-d).to_track_quat("-Z", "Y")
    der, arr = rot @ mathutils.Vector((1, 0, 0)), rot @ mathutils.Vector((0, 1, 0))
    th, tv = _tangentes(cd, rx, ry)
    th, tv = th * 0.9, tv * 0.9
    dist = 0.0
    for p in pts:
        q = p - centro
        prof = q.dot(d)
        dist = max(dist, prof + abs(q.dot(der)) / th, prof + abs(q.dot(arr)) / tv)
    return max(dist, radio * 0.5, 0.01), rot

def _camara():
    cd = bpy.data.cameras.new("_buho_cam")
    cd.lens = 50
    cam = bpy.data.objects.new("_buho_cam", cd)
    sc.collection.objects.link(cam)
    _temp.append(cam)
    return cam

def _tope_fondo(d):
    # Distancia desde el centro, en la dirección d, a la primera pared de un fondo (y cuáles corta).
    dg = bpy.context.evaluated_depsgraph_get()
    mejor, cortan = None, []
    for n in _fondo:
        ob = bpy.data.objects.get(n)
        if ob is None or ob.type != "MESH" or ob.hide_render:
            continue
        oe = ob.evaluated_get(dg)
        mw = oe.matrix_world
        inv = mw.inverted()
        try:
            ok, loc, _nor, _i = oe.ray_cast(inv @ centro, (inv.to_3x3() @ d).normalized())
        except Exception:
            continue
        if ok:
            t = (mw @ loc - centro).length
            cortan.append(n)
            mejor = t if mejor is None else min(mejor, t)
    return mejor, cortan

_ajuste_fondo = {}
def _distancia(cam, dirs, rx, ry):
    # Distancia que vale para todas las direcciones. Si la cámara quedaría al otro lado de la pared de un
    # fondo (estudio, ciclorama), se acerca y abre la lente; si ni así cabe, oculta esa pieza en el render.
    cam.data.lens = 50
    dist = max(_encuadre(dk, cam.data, rx, ry)[0] for dk in dirs)
    if not _fondo:
        return dist
    topes = [_tope_fondo(dk) for dk in dirs]
    hits = [t for t in topes if t[0] is not None]
    if not hits:
        return dist
    tope = min(t[0] for t in hits) * 0.95
    if dist <= tope:
        return dist
    lente = 50.0
    for _k in range(10):
        lente *= tope / dist
        if lente < 14:
            break
        cam.data.lens = lente
        dist = max(_encuadre(dk, cam.data, rx, ry)[0] for dk in dirs)
        if dist <= tope:
            _ajuste_fondo["lente_mm"] = round(lente, 1)
            return dist
    cam.data.lens = 50
    for n in sorted(set(n for t in hits for n in t[1])):
        _poner(bpy.data.objects[n], "hide_render", True)
        _ajuste_fondo.setdefault("ocultos", []).append(n)
    return max(_encuadre(dk, cam.data, rx, ry)[0] for dk in dirs)

def _colocar(cam, d, rx, ry):
    dist = _distancia(cam, [d], rx, ry)
    rot = (-d).to_track_quat("-Z", "Y")
    cam.location = centro + d * dist
    cam.rotation_euler = rot.to_euler()
    cam.data.clip_start = max(dist / 1000, 0.001)
    cam.data.clip_end = dist * 10 + radio * 4

def _luces(padre=None):
    # Luces de estudio temporales para que se vea bien aunque la escena no tenga.
    luces = []
    for nombre, energia, rot in (("_buho_key", 4.0, (0.9, 0.2, 0.8)), ("_buho_fill", 1.5, (1.1, 0, -2.4)), ("_buho_rim", 2.5, (-0.6, 0, 3.0))):
        ld = bpy.data.lights.new(nombre, "SUN")
        ld.energy = energia
        lo = bpy.data.objects.new(nombre, ld)
        lo.rotation_euler = rot
        if padre is not None:
            lo.parent = padre
        sc.collection.objects.link(lo)
        _temp.append(lo)
        luces.append(lo)
    return luces

def _gpu():
    # Cycles en GPU si hay alguna; si no, CPU. Si hay que tocar las preferencias se dejan como estaban.
    try:
        prefs = bpy.context.preferences.addons["cycles"].preferences
    except Exception:
        _poner(sc.cycles, "device", "CPU")
        return "CPU"
    if prefs.compute_device_type != "NONE":
        try:
            prefs.get_devices()
        except Exception:
            pass
        if any(d.use and d.type != "CPU" for d in prefs.devices):
            _poner(sc.cycles, "device", "GPU")
            return prefs.compute_device_type
    for t in ("OPTIX", "CUDA", "HIP", "ONEAPI", "METAL"):
        try:
            _poner(prefs, "compute_device_type", t)
        except Exception:
            continue
        try:
            prefs.get_devices_for_type(t)
        except Exception:
            pass
        gpus = [d for d in prefs.devices if d.type == t]
        if gpus:
            for d in gpus:
                _poner(d, "use", True)
            _poner(sc.cycles, "device", "GPU")
            return t + " (" + ", ".join(d.name for d in gpus) + ")"
    _poner(sc.cycles, "device", "CPU")
    return "CPU"

def _motor(motor, muestras):
    _poner(sc.render, "engine", motor)
    info = {"motor": sc.render.engine}
    if sc.render.engine == "BLENDER_EEVEE":
        try:
            _poner(sc.eevee, "taa_render_samples", muestras or 32)
        except Exception:
            pass
    elif sc.render.engine == "CYCLES":
        _poner(sc.cycles, "samples", muestras or 64)
        info["muestras"] = sc.cycles.samples
        info["dispositivo"] = _gpu()
    return info

def _formato_png(transparente):
    im = sc.render.image_settings
    if hasattr(im, "media_type"):
        _poner(im, "media_type", "IMAGE")
    _poner(im, "file_format", "PNG")
    _poner(im, "color_mode", "RGBA" if transparente else "RGB")
    _poner(sc.render, "film_transparent", bool(transparente))

def _resolucion(rx, ry):
    _poner(sc.render, "resolution_x", rx)
    _poner(sc.render, "resolution_y", ry)
    _poner(sc.render, "resolution_percentage", 100)

def _pixeles(ruta):
    import numpy as np
    im = bpy.data.images.load(ruta, check_existing=False)
    w, h = im.size
    a = np.empty(w * h * 4, dtype=np.float32)
    im.pixels.foreach_get(a)
    bpy.data.images.remove(im)
    return a.reshape(h, w, 4)

def _hoja(rutas, salida, rx, ry, cols=None, transparente=False):
    # Hoja de contactos: una sola imagen con todas en rejilla (de izquierda a derecha y de arriba abajo).
    import numpy as np
    cols = cols or (len(rutas) if len(rutas) <= 3 else math.ceil(math.sqrt(len(rutas))))
    filas = math.ceil(len(rutas) / cols)
    hueco = 6
    W, H = cols * rx + (cols + 1) * hueco, filas * ry + (filas + 1) * hueco
    hoja = np.zeros((H, W, 4), dtype=np.float32)
    if not transparente:
        hoja[...] = (0.16, 0.16, 0.16, 1.0)
    for i, r in enumerate(rutas):
        a = _pixeles(r)
        c, f = i % cols, i // cols
        x = hueco + c * (rx + hueco)
        y0 = H - (hueco + f * (ry + hueco)) - ry
        if transparente:
            hoja[y0:y0 + ry, x:x + rx, :] = a
        else:
            al = a[..., 3:4]
            hoja[y0:y0 + ry, x:x + rx, :3] = a[..., :3] * al + hoja[y0:y0 + ry, x:x + rx, :3] * (1 - al)
    img = bpy.data.images.new("_buho_hoja", W, H, alpha=True)
    img.pixels.foreach_set(hoja.ravel())
    os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
    img.filepath_raw = salida
    img.file_format = "PNG"
    img.save()
    bpy.data.images.remove(img)
    return cols, filas
`;
}

function pyCaptura({ salida, vistas, ancho, alto, motor, muestras, objetos, coleccion, fondoTransparente, columnas, frame, conNiebla, encuadrar }) {
  return pyKitFoto({ objetos, coleccion, frame, encuadrar, conNiebla: conNiebla || vistas.every((v) => v === "camara") }) + `
import numpy as np
salida = ${py(salida)}
vistas = ${py(vistas)}
rx, ry = ${ancho}, ${alto}
transparente = ${py(!!fondoTransparente)}
aviso = None
cam_escena = sc.camera
if "camara" in vistas and cam_escena is None:
    aviso = "la escena no tiene cámara: se usa la vista tres_cuartos"
    vistas = ["tres_cuartos" if v == "camara" else v for v in vistas]
tmp = tempfile.mkdtemp(prefix="buho_captura_")
info = {}
try:
    _resolucion(rx, ry)
    _formato_png(transparente)
    info = _motor(${py(motor)}, ${muestras ? Number(muestras) : "None"})
    cam = None
    luces = []
    if any(v != "camara" for v in vistas):
        cam = _camara()
        luces = _luces()
    _poner(sc, "camera", sc.camera)
    rutas = []
    _ajustes_vistas = {}
    _buho_total(len(vistas))
    for i, v in enumerate(vistas):
        ruta = salida if len(vistas) == 1 else os.path.join(tmp, "%02d_%s.png" % (i, v))
        if v == "camara":
            sc.camera = cam_escena
            for l in luces:
                l.hide_render = True
        else:
            for l in luces:
                l.hide_render = False
            _colocar(cam, _dir(v), rx, ry)
            sc.camera = cam
        sc.render.filepath = ruta
        bpy.ops.render.render(write_still=True)
        rutas.append(ruta)
        if _ajuste_fondo:
            # El ajuste al fondo es de cada vista: se apunta y se deshace para la siguiente.
            for n in _ajuste_fondo.get("ocultos", []):
                bpy.data.objects[n].hide_render = False
            _ajustes_vistas[v] = dict(_ajuste_fondo)
            _ajuste_fondo.clear()
    if len(rutas) > 1:
        cols, filas = _hoja(rutas, salida, rx, ry, ${columnas ? Number(columnas) : "None"}, transparente)
finally:
    _restaurar()
    _limpiar_temp()
    shutil.rmtree(tmp, ignore_errors=True)
    sc.frame_set(_frame_viejo_kit)
result = {"frame": sc.frame_current if _frame_kit is None else int(_frame_kit), "captura": salida, "vistas": vistas, "objetos": [o.name for o in objs],
          "tamano": [round(v, 3) for v in (mx - mn)], **info, **_info_encuadre()}
if _niebla:
    result["fuera_del_encuadre"] = _niebla
if _ajustes_vistas:
    result["camara_dentro_del_fondo"] = _ajustes_vistas
if len(vistas) > 1:
    result["hoja"] = {"columnas": cols, "filas": filas, "orden": "de izquierda a derecha y de arriba abajo"}
if aviso:
    result["aviso"] = aviso
`;
}

function pyGiro({ salida, gif, tmpdir, segundos, fps, ancho, alto, motor, muestras, vista, objetos, coleccion, conNiebla, encuadrar }) {
  return pyKitFoto({ objetos, coleccion, conNiebla, encuadrar }) + `
import numpy as np
salida = ${py(salida)}
es_gif = ${py(gif)}
fps = ${fps}
fotogramas = max(2, int(round(fps * ${segundos})))
rx, ry = ${ancho}, ${alto}
tmp = ${py(tmpdir)}
os.makedirs(tmp, exist_ok=True)
info = {}
try:
    _resolucion(rx, ry)
    if es_gif:
        _formato_png(False)
    else:
        im = sc.render.image_settings
        if hasattr(im, "media_type"):
            _poner(im, "media_type", "VIDEO")
        _poner(im, "file_format", "FFMPEG")
        _poner(sc.render, "film_transparent", False)
        ff = sc.render.ffmpeg
        _poner(ff, "format", "MPEG4")
        _poner(ff, "codec", "H264")
        _poner(ff, "constant_rate_factor", "HIGH")
        _poner(ff, "audio_codec", "NONE")
    info = _motor(${py(motor)}, ${muestras ? Number(muestras) : "None"})
    # Pivote en el centro con la cámara y las luces colgando: al girar el pivote la luz acompaña a la cámara.
    pivote = bpy.data.objects.new("_buho_giro", None)
    sc.collection.objects.link(pivote)
    _temp.insert(0, pivote)
    pivote.location = centro
    cam = _camara()
    cam.parent = pivote
    d = _dir(${py(vista)})
    dist = _distancia(cam, [mathutils.Matrix.Rotation(2 * math.pi * k / 24, 3, "Z") @ d for k in range(24)], rx, ry)
    cam.location = d * dist
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    cam.data.clip_start = max(dist / 1000, 0.001)
    cam.data.clip_end = dist * 10 + radio * 4
    _luces(pivote)
    _poner(sc, "camera", cam)
    _poner(bpy.context.preferences.edit, "keyframe_new_interpolation_type", "LINEAR")
    pivote.rotation_euler = (0, 0, 0)
    pivote.keyframe_insert("rotation_euler", index=2, frame=1)
    pivote.rotation_euler = (0, 0, 2 * math.pi)
    pivote.keyframe_insert("rotation_euler", index=2, frame=fotogramas + 1)
    frame_viejo = sc.frame_current
    _poner(sc, "frame_start", 1)
    _poner(sc, "frame_end", fotogramas)
    _poner(sc, "frame_step", 1)
    _poner(sc.render, "fps", fps)
    _poner(sc.render, "fps_base", 1.0)
    _poner(sc.render, "use_file_extension", True)
    _poner(sc.render, "filepath", os.path.join(tmp, "f_####" if es_gif else "giro_"))
    _buho_total(fotogramas)
    try:
        bpy.ops.render.render(animation=True)
    finally:
        sc.frame_set(frame_viejo)
finally:
    _restaurar()
    _limpiar_temp()
result = {"objetos": [o.name for o in objs], "fotogramas": fotogramas, "fps": fps, "tamano_px": [rx, ry], **info, **_info_encuadre()}
if es_gif:
    # Fotogramas en bruto (RGB de arriba abajo) para que el CLI los codifique en GIF.
    pngs = sorted(f for f in os.listdir(tmp) if f.startswith("f_") and f.endswith(".png"))
    crudo = os.path.join(tmp, "fotogramas.rgb")
    with open(crudo, "wb") as fh:
        for f in pngs:
            a = _pixeles(os.path.join(tmp, f))[::-1, :, :3]
            fh.write((np.clip(a, 0, 1) * 255 + 0.5).astype(np.uint8).tobytes())
            os.remove(os.path.join(tmp, f))
    result.update({"crudo": crudo, "n": len(pngs)})
else:
    hechos = [f for f in os.listdir(tmp) if f.startswith("giro_")]
    if not hechos:
        raise RuntimeError("Blender no ha generado el vídeo")
    os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
    if os.path.exists(salida):
        os.remove(salida)
    shutil.move(os.path.join(tmp, hechos[0]), salida)
    shutil.rmtree(tmp, ignore_errors=True)
    result.update({"giro": salida, "bytes": os.path.getsize(salida)})
`;
}

// Vídeo de la animación de la escena: cámara de la escena (fija), frontal automática u orbitando alrededor.
function pyVideo({ salida, tmpdir, camara, vueltas, segundos, fps, ancho, alto, motor, muestras, vista, objetos, coleccion, luces, hoja, nHoja, conNiebla, encuadrar }) {
  return pyKitFoto({ objetos, coleccion, encuadrar, conNiebla: conNiebla || camara === "fija" }) + `
salida = ${py(salida)}
modo = ${py(camara)}
rx, ry = ${ancho}, ${alto}
tmp = ${py(tmpdir)}
os.makedirs(tmp, exist_ok=True)
info = {}
frame_viejo = sc.frame_current
hoja = ${py(hoja)}
fps = ${fps ? Number(fps) : "None"} or sc.render.fps
f0 = sc.frame_start
f1 = (f0 + max(2, int(round(${segundos ? Number(segundos) : "0"} * fps))) - 1) if ${segundos ? "True" : "False"} else sc.frame_end
if f1 <= f0:
    raise RuntimeError("la escena no tiene fotogramas que renderizar (frame_start >= frame_end); usa --segundos")
aviso = None
hoja_info = None
try:
    _poner(sc, "frame_start", f0)
    _poner(sc, "frame_end", f1)
    _poner(sc, "frame_step", 1)
    _poner(sc.render, "fps", int(fps))
    _poner(sc.render, "fps_base", 1.0)
    # Encuadre que valga para toda la animación: caja de 16 fotogramas repartidos.
    todos = []
    for k in range(16):
        sc.frame_set(int(round(f0 + (f1 - f0) * k / 15)))
        todos += _calc_pts()
    _fijar_caja(todos)
    _resolucion(rx, ry)
    im = sc.render.image_settings
    if hasattr(im, "media_type"):
        _poner(im, "media_type", "VIDEO")
    _poner(im, "file_format", "FFMPEG")
    _poner(sc.render, "film_transparent", False)
    ff = sc.render.ffmpeg
    _poner(ff, "format", "MPEG4")
    _poner(ff, "codec", "H264")
    _poner(ff, "constant_rate_factor", "HIGH")
    _poner(ff, "audio_codec", "NONE")
    info = _motor(${py(motor)}, ${muestras ? Number(muestras) : "None"})
    luces_escena = any(o.type == "LIGHT" and not o.hide_render and o.visible_get() for o in sc.objects)
    estudio = ${py(luces)} == "estudio" or (${py(luces)} != "escena" and not luces_escena)
    if modo == "fija" and sc.camera is not None:
        camara_usada = sc.camera.name
        if estudio:
            _luces()
    else:
        if modo == "fija":
            aviso = "la escena no tiene cámara: se usa una cámara fija automática"
        d = _dir("frente" if modo == "frontal" else ${py(vista)})
        pivote = bpy.data.objects.new("_buho_video", None)
        sc.collection.objects.link(pivote)
        _temp.insert(0, pivote)
        pivote.location = centro
        cam = _camara()
        cam.parent = pivote
        pasos = 24 if modo == "orbita" else 1
        dist = _distancia(cam, [mathutils.Matrix.Rotation(2 * math.pi * k / pasos, 3, "Z") @ d for k in range(pasos)], rx, ry)
        cam.location = d * dist
        cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
        cam.data.clip_start = max(dist / 1000, 0.001)
        cam.data.clip_end = dist * 10 + radio * 4
        if estudio:
            _luces(pivote)
        _poner(sc, "camera", cam)
        camara_usada = "automática (" + modo + ")"
        if modo == "orbita":
            _poner(bpy.context.preferences.edit, "keyframe_new_interpolation_type", "LINEAR")
            pivote.rotation_euler = (0, 0, 0)
            pivote.keyframe_insert("rotation_euler", index=2, frame=f0)
            pivote.rotation_euler = (0, 0, 2 * math.pi * ${Number(vueltas)})
            pivote.keyframe_insert("rotation_euler", index=2, frame=f1 + 1)
    _poner(sc.render, "use_file_extension", True)
    _poner(sc.render, "filepath", os.path.join(tmp, "video_"))
    _buho_total((f1 - f0 + 1) + (${Number(nHoja) || 6} if hoja else 0))
    bpy.ops.render.render(animation=True)
    if hoja:
        # Unos fotogramas sueltos en una hoja de contactos, para revisar el vídeo sin abrirlo.
        if hasattr(im, "media_type"):
            im.media_type = "IMAGE"
        im.file_format = "PNG"
        n = ${Number(nHoja) || 6}
        frames = sorted(set(int(round(f0 + (f1 - f0) * k / max(n - 1, 1))) for k in range(n)))
        rutas = []
        for fr in frames:
            sc.frame_set(fr)
            sc.render.filepath = os.path.join(tmp, "hoja_%05d.png" % fr)
            bpy.ops.render.render(write_still=True)
            rutas.append(sc.render.filepath)
        cols, filas = _hoja(rutas, hoja, rx, ry, 3 if len(rutas) > 4 else None)
        hoja_info = {"hoja": hoja, "fotogramas": frames, "columnas": cols}
finally:
    sc.frame_set(frame_viejo)
    _restaurar()
    _limpiar_temp()
hechos = [f for f in os.listdir(tmp) if f.startswith("video_")]
if not hechos:
    raise RuntimeError("Blender no ha generado el vídeo")
os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
if os.path.exists(salida):
    os.remove(salida)
shutil.move(os.path.join(tmp, hechos[0]), salida)
shutil.rmtree(tmp, ignore_errors=True)
result = {"video": salida, "bytes": os.path.getsize(salida), "camara": camara_usada, "fotogramas": [f0, f1],
          "fps": int(fps), "segundos": round((f1 - f0 + 1) / fps, 2), "tamano_px": [rx, ry],
          "luces": "estudio" if estudio else "escena", "objetos": len(objs), **info, **_info_encuadre()}
if modo == "orbita":
    result["vueltas"] = ${Number(vueltas)}
if hoja_info:
    result["revision"] = hoja_info
if aviso:
    result["aviso"] = aviso
`;
}

// Llama a una función de lib/buho_rig.py (recargándola, por si ha cambiado) con argumentos en JSON.
function pyRig(funcion, args) {
  return [
    "import sys as _bs, importlib as _bi, json as _bj",
    `if ${py(LIB)} not in _bs.path:`,
    `    _bs.path.insert(0, ${py(LIB)})`,
    "import buho_rig as _br",
    "_br = _bi.reload(_br)",
    `result = _br.${funcion}(**_bj.loads(${py(JSON.stringify(args))}))`,
  ].join("\n");
}

// Llama a una función de un módulo de lib/ (recargándolo) con argumentos en JSON; deja la respuesta en `result`.
function pyLib(modulo, funcion, args, variable = "result") {
  return [
    "import sys as _bs, importlib as _bi, json as _bj",
    `if ${py(LIB)} not in _bs.path:`,
    `    _bs.path.insert(0, ${py(LIB)})`,
    `import ${modulo} as _bmod`,
    "_bmod = _bi.reload(_bmod)",
    `${variable} = _bmod.${funcion}(**_bj.loads(${py(JSON.stringify(args))}))`,
  ].join("\n");
}

// comparar: render con el encuadre y el aspecto de la referencia y comparación lado a lado (lib/buho_comparar.py).
function pyComparar({ referencia, salida, renderRuta, alto, vista, motor, muestras, objetos, coleccion, frame, conNiebla, encuadrar, opaco, umbral }) {
  const pre = `
import bpy as _b
_im = _b.data.images.load(${py(referencia)}, check_existing=False)
_rw, _rh = _im.size
_b.data.images.remove(_im)
if not _rw or not _rh:
    raise RuntimeError("Blender no puede leer la referencia")
_cmp_ry = ${Number(alto)}
_cmp_rx = max(16, int(round(_cmp_ry * _rw / _rh / 2)) * 2)
`;
  const cap = pyCaptura({
    salida: renderRuta, vistas: [vista], ancho: "_cmp_rx", alto: "_cmp_ry", motor, muestras, objetos, coleccion,
    fondoTransparente: !opaco, columnas: null, frame, conNiebla, encuadrar,
  });
  return pre + cap + "\n_cap = result\n" + pyLib("buho_comparar", "comparar", { referencia, render: renderRuta, salida, umbral }) + `
result["referencia"] = ${py(referencia)}
result["referencia_px"] = [_rw, _rh]
result["render"] = ${py(renderRuta)}
result["captura"] = {k: _cap[k] for k in ("vistas", "objetos", "motor", "aviso", "fondo_fuera_del_encuadre", "frame") if k in _cap}
`;
}

function pyExportar({ salida, objetos, coleccion, unir }) {
  const ext = path.extname(salida).toLowerCase();
  return `
import bpy, os
nombres = ${py(objetos)}
coleccion = ${py(coleccion)}
sc = bpy.context.scene
if bpy.context.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
if coleccion:
    objs = [o for o in bpy.data.collections[coleccion].all_objects]
elif nombres:
    objs = [bpy.data.objects[n] for n in nombres]
else:
    objs = [o for o in sc.objects if o.visible_get() and o.type in {"MESH", "CURVE", "EMPTY", "ARMATURE"} and not o.name.startswith("_buho")]
unido = None
if ${unir ? "True" : "False"}:
    # Una sola malla (copias con modificadores aplicados): lo que esperan muchos motores de juego.
    dg = bpy.context.evaluated_depsgraph_get()
    import bmesh
    bm = bmesh.new()
    mats = []
    for o in objs:
        if o.type != "MESH":
            continue
        me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
        me.transform(o.matrix_world)
        idx = []
        for m in me.materials:
            if m not in mats:
                mats.append(m)
            idx.append(mats.index(m))
        for p in me.polygons:
            p.material_index = idx[p.material_index] if idx else 0
        bm.from_mesh(me)
        bpy.data.meshes.remove(me)
    me = bpy.data.meshes.new("_buho_unido")
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    nombre = coleccion or (objs[0].name if objs else "Unido")
    unido = bpy.data.objects.new(nombre, me)
    sc.collection.objects.link(unido)
    exportados = [o.name for o in objs]
    objs = [unido]
bpy.ops.object.select_all(action="DESELECT")
for o in objs:
    o.select_set(True)
if objs:
    bpy.context.view_layer.objects.active = objs[0]
os.makedirs(os.path.dirname(${py(salida)}) or ".", exist_ok=True)
ext = ${py(ext)}
if ext in (".glb", ".gltf"):
    bpy.ops.export_scene.gltf(filepath=${py(salida)}, export_format="GLB" if ext == ".glb" else "GLTF_SEPARATE",
                              use_selection=True, export_apply=True, export_yup=True)
elif ext == ".fbx":
    bpy.ops.export_scene.fbx(filepath=${py(salida)}, use_selection=True, apply_scale_options="FBX_SCALE_ALL",
                             use_mesh_modifiers=True, bake_space_transform=True, axis_forward="-Z", axis_up="Y")
elif ext == ".obj":
    bpy.ops.wm.obj_export(filepath=${py(salida)}, export_selected_objects=True, apply_modifiers=True)
else:
    raise RuntimeError("formato no soportado: " + ext + " (usa .glb, .gltf, .fbx u .obj)")
nombres_fin = exportados if unido else [o.name for o in objs]
if unido:
    bpy.data.objects.remove(unido, do_unlink=True)
    bpy.data.meshes.remove(me)
result = {"exportado": ${py(salida)}, "objetos": nombres_fin, "unido": bool(unido), "bytes": os.path.getsize(${py(salida)})}
`;
}

function pyStats({ coleccion, top }) {
  return `
import bpy, mathutils
import numpy as np
sc = bpy.context.scene
coleccion = ${py(coleccion)}
if coleccion and coleccion not in bpy.data.collections:
    raise RuntimeError("no existe la colección " + repr(coleccion))
dentro = set(o.name for o in bpy.data.collections[coleccion].all_objects) if coleccion else None
originales = [o for o in sc.objects if dentro is None or o.name in dentro]
dg = bpy.context.evaluated_depsgraph_get()
GEOM = {"MESH", "CURVE", "FONT", "SURFACE", "META"}
cache = {}
tot = {"vertices": 0, "aristas": 0, "caras": 0, "triangulos": 0}
por_obj = {}
mats = set()
pts = []
n_inst = 0
nset = set(o.name for o in originales)
for inst in dg.object_instances:
    ob = inst.object
    raiz = inst.parent.original if (inst.is_instance and inst.parent) else ob.original
    if raiz.name not in nset:
        continue
    if inst.is_instance:
        n_inst += 1
    if ob.type not in GEOM:
        continue
    clave = (ob.name, ob.data.name if ob.data else "")
    if clave not in cache:
        me = ob.to_mesh()
        if me is None:
            cache[clave] = None
        else:
            lt = np.empty(len(me.polygons), dtype=np.int32)
            me.polygons.foreach_get("loop_total", lt)
            cache[clave] = {"vertices": len(me.vertices), "aristas": len(me.edges), "caras": len(me.polygons),
                            "triangulos": int((lt - 2).sum()) if len(lt) else 0,
                            "materiales": [m.name for m in me.materials if m]}
            ob.to_mesh_clear()
    c = cache[clave]
    if c is None:
        continue
    for k in tot:
        tot[k] += c[k]
    mats.update(c["materiales"])
    d = por_obj.setdefault(raiz.name, {"nombre": raiz.name, "tipo": raiz.type, "vertices": 0, "caras": 0, "triangulos": 0, "instancias": 0})
    d["vertices"] += c["vertices"]
    d["caras"] += c["caras"]
    d["triangulos"] += c["triangulos"]
    if inst.is_instance:
        d["instancias"] += 1
    m = inst.matrix_world.copy()
    pts.extend(m @ mathutils.Vector(v) for v in ob.bound_box)
tipos = {}
for o in originales:
    tipos[o.type] = tipos.get(o.type, 0) + 1
escala = sc.unit_settings.scale_length or 1.0
tam = None
if pts:
    mn = [min(p[i] for p in pts) for i in range(3)]
    mx = [max(p[i] for p in pts) for i in range(3)]
    tam = {"min": [round(v * escala, 4) for v in mn], "max": [round(v * escala, 4) for v in mx],
           "tamano_m": [round((b - a) * escala, 4) for a, b in zip(mn, mx)]}
datos_compartidos = sorted(set(o.data.name for o in originales if o.data is not None and o.type in GEOM and o.data.users > 1))
result = {
    "ambito": coleccion or sc.name,
    "objetos": len(originales),
    "por_tipo": tipos,
    **tot,
    "materiales": sorted(mats),
    "n_materiales": len(mats),
    "medidas": tam,
    "unidades": {"sistema": sc.unit_settings.system, "escala": escala},
    "instancias": n_inst,
    "instanciadores_de_coleccion": [o.name for o in originales if o.type == "EMPTY" and o.instance_type == "COLLECTION" and o.instance_collection],
    "enlazados": [o.name for o in originales if o.library],
    "con_override": [o.name for o in originales if o.override_library],
    "datos_compartidos": datos_compartidos,
    "bibliotecas": [l.filepath for l in bpy.data.libraries],
    "mas_pesados": sorted(por_obj.values(), key=lambda d: -d["triangulos"])[:${top}],
}
`;
}

function pyImportar({ archivo, objetos, colecciones, destino }) {
  const ext = path.extname(archivo).toLowerCase();
  return `
import bpy, os
archivo = ${py(archivo)}
ext = ${py(ext)}
sc = bpy.context.scene
if not os.path.exists(archivo):
    raise RuntimeError("no existe " + archivo)
if bpy.context.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
antes = set(bpy.data.objects)
destino = ${py(destino)} or os.path.splitext(os.path.basename(archivo))[0]
col = bpy.data.collections.new(destino)
sc.collection.children.link(col)
nuevas_cols = []
try:
    if ext == ".blend":
        quiero_obj = ${py(objetos)}
        quiero_col = ${py(colecciones)}
        with bpy.data.libraries.load(archivo, link=False) as (src, dst):
            if quiero_col:
                falta = [c for c in quiero_col if c not in src.collections]
                if falta:
                    raise RuntimeError("no están en el .blend las colecciones: " + ", ".join(falta) + ". Hay: " + ", ".join(src.collections))
                dst.collections = list(quiero_col)
            else:
                if quiero_obj:
                    falta = [o for o in quiero_obj if o not in src.objects]
                    if falta:
                        raise RuntimeError("no están en el .blend los objetos: " + ", ".join(falta))
                dst.objects = list(quiero_obj) if quiero_obj else list(src.objects)
        if quiero_col:
            for c in dst.collections:
                if c is not None:
                    col.children.link(c)
                    nuevas_cols.append(c.name)
        else:
            for o in dst.objects:
                if o is not None and not o.users_collection:
                    col.objects.link(o)
    else:
        # Los importadores meten lo nuevo en la colección activa: la nuestra.
        lc = next((l for l in bpy.context.view_layer.layer_collection.children if l.collection == col), None)
        viejo_activo = bpy.context.view_layer.active_layer_collection
        if lc is not None:
            bpy.context.view_layer.active_layer_collection = lc
        try:
            if ext in (".glb", ".gltf"):
                bpy.ops.import_scene.gltf(filepath=archivo)
            elif ext == ".fbx":
                bpy.ops.import_scene.fbx(filepath=archivo)
            elif ext == ".obj":
                bpy.ops.wm.obj_import(filepath=archivo)
            elif ext == ".stl":
                bpy.ops.wm.stl_import(filepath=archivo)
            elif ext == ".ply":
                bpy.ops.wm.ply_import(filepath=archivo)
            elif ext in (".usd", ".usda", ".usdc", ".usdz"):
                bpy.ops.wm.usd_import(filepath=archivo)
            else:
                raise RuntimeError("formato no soportado: " + ext + " (usa .glb, .gltf, .fbx, .obj, .stl, .ply, .usd o .blend)")
        finally:
            try:
                bpy.context.view_layer.active_layer_collection = viejo_activo
            except Exception:
                pass
        for o in bpy.data.objects:
            if o not in antes and col not in o.users_collection:
                for c in list(o.users_collection):
                    c.objects.unlink(o)
                col.objects.link(o)
except BaseException:
    # Si algo falla no se deja la colección vacía.
    for o in [o for o in bpy.data.objects if o not in antes]:
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.collections.remove(col)
    raise
nuevos = [o for o in bpy.data.objects if o not in antes]
result = {"importado": archivo, "coleccion": col.name, "colecciones": nuevas_cols,
          "objetos": [{"nombre": o.name, "tipo": o.type, "dimensiones": [round(v, 4) for v in o.dimensions]} for o in nuevos]}
`;
}

const PY_LIMPIAR = `
import bpy
tipos = ("meshes", "materials", "textures", "images", "node_groups", "curves", "lights", "cameras", "actions", "collections", "objects", "worlds")
antes = {t: len(getattr(bpy.data, t)) for t in tipos}
n = bpy.data.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)
despues = {t: len(getattr(bpy.data, t)) for t in tipos}
result = {"purgados": n, "por_tipo": {t: antes[t] - despues[t] for t in tipos if antes[t] != despues[t]}}
`;

// Antes de cambiar de archivo: guarda el actual si tiene ruta y cambios; si tiene cambios y no tiene ruta,
// solo sigue con --descartar (o si la escena no tiene más que cámara y luces).
function pyAntesDeCambiar(descartar, marca) {
  return `
import bpy
guardado_antes = None
_de_fabrica = not bpy.data.is_dirty and set(o.name for o in bpy.data.objects) <= {"Cube", "Camera", "Light"}
# Blender no marca como "cambiado" lo que se hace por script (is_dirty no es fiable aquí):
# si el archivo tiene ruta se guarda siempre; si no la tiene y hay algo más que cámara y luces, se para.
if bpy.data.filepath:
    if not ${py(!!descartar)}:
        bpy.ops.wm.save_mainfile()
        guardado_antes = bpy.data.filepath
elif not ${py(!!descartar)} and not _de_fabrica and any(o.type not in {"CAMERA", "LIGHT"} for o in bpy.data.objects):
    raise RuntimeError("la escena abierta no tiene archivo y tiene objetos: guárdala con 'buho-blender guardar x.blend' o repite con --descartar para perderla")
def _diferir(fn):
    # Con ventana se cambia de archivo justo después de contestar (desde un temporizador), que es lo seguro.
    # Al acabar deja una marca en driver_namespace (sobrevive al cambio de archivo) para que el CLI sepa que ya está.
    if bpy.app.background:
        fn()
        return False
    def _envuelto():
        try:
            fn()
            bpy.app.driver_namespace["_buho_hecho"] = {"marca": ${py(marca)}, "error": None}
        except Exception as ex:
            bpy.app.driver_namespace["_buho_hecho"] = {"marca": ${py(marca)}, "error": str(ex)}
        return None
    bpy.app.timers.register(_envuelto, first_interval=0.3, persistent=True)
    return True
`;
}

function pyNuevo({ vacia, guardarEn, descartar, marca }) {
  return pyAntesDeCambiar(descartar, marca) + `
def _nuevo():
    # Sin use_factory_startup: eso recarga también las preferencias, apaga el complemento MCP y cierra Blender.
    bpy.ops.wm.read_homefile()
    for o in list(bpy.data.objects):
        if ${py(!!vacia)} or o.type == "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.orphans_purge(do_local_ids=True, do_recursive=True)
    ruta = ${py(guardarEn)}
    if ruta:
        import os
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        bpy.ops.wm.save_as_mainfile(filepath=ruta)
    return None
diferido = _diferir(_nuevo)
result = {"nuevo": True, "guardado_antes": guardado_antes, "diferido": diferido}
if not diferido:
    result["objetos"] = [o.name for o in bpy.data.objects]
    result["archivo"] = bpy.data.filepath or None
`;
}

function pyCargar({ archivo, descartar, marca }) {
  return pyAntesDeCambiar(descartar, marca) + `
import os
archivo = ${py(archivo)}
if not os.path.exists(archivo):
    raise RuntimeError("no existe " + archivo)
def _cargar():
    bpy.ops.wm.open_mainfile(filepath=archivo)
    return None
diferido = _diferir(_cargar)
result = {"cargando": archivo, "guardado_antes": guardado_antes, "diferido": diferido}
if not diferido:
    result["archivo"] = bpy.data.filepath
    result["objetos"] = len(bpy.data.objects)
`;
}

function pyPantalla({ salida, render }) {
  return `
import bpy, os
if bpy.app.background:
    raise RuntimeError("pantalla necesita un Blender con ventana (no vale con --fondo ni con blender -b); usa captura")
salida = ${py(salida)}
mejor = None
for w in bpy.context.window_manager.windows:
    for a in w.screen.areas:
        if a.type == "VIEW_3D" and (mejor is None or a.width * a.height > mejor[1].width * mejor[1].height):
            mejor = (w, a)
if mejor is None:
    raise RuntimeError("no hay ninguna vista 3D en la ventana de Blender")
w, a = mejor
reg = next(r for r in a.regions if r.type == "WINDOW")
os.makedirs(os.path.dirname(salida) or ".", exist_ok=True)
if ${py(!!render)}:
    # Render de la vista (OpenGL) tal como se ve, sin la interfaz, al tamaño de la vista.
    sc = bpy.context.scene
    rd = sc.render
    viejo = (rd.filepath, rd.resolution_x, rd.resolution_y, rd.resolution_percentage, rd.image_settings.file_format)
    try:
        rd.resolution_x, rd.resolution_y, rd.resolution_percentage = reg.width, reg.height, 100
        rd.image_settings.file_format = "PNG"
        rd.filepath = salida
        with bpy.context.temp_override(window=w, area=a, region=reg):
            bpy.ops.render.opengl(write_still=True, view_context=True)
    finally:
        rd.filepath, rd.resolution_x, rd.resolution_y, rd.resolution_percentage, rd.image_settings.file_format = viejo
else:
    with bpy.context.temp_override(window=w, area=a, region=reg):
        bpy.ops.screen.screenshot_area(filepath=salida)
if not os.path.exists(salida):
    raise RuntimeError("Blender no ha escrito la captura")
v3d = a.spaces.active
result = {"pantalla": salida, "modo": "render_vista" if ${py(!!render)} else "captura_area", "ancho": a.width, "alto": a.height,
          "sombreado": v3d.shading.type, "vista_camara": v3d.region_3d.view_perspective == "CAMERA",
          "bytes": os.path.getsize(salida)}
`;
}

// ---------- GIF (sin dependencias): paleta por corte de mediana + LZW ----------

function paletaMediana(frames, nColores = 256) {
  const hist = new Uint32Array(32768);
  for (const f of frames)
    for (let i = 0; i < f.length; i += 6) hist[((f[i] >> 3) << 10) | ((f[i + 1] >> 3) << 5) | (f[i + 2] >> 3)]++;
  const colores = [];
  for (let c = 0; c < 32768; c++) if (hist[c]) colores.push(c);
  const comp = (c, k) => (k === 0 ? c >> 10 : k === 1 ? (c >> 5) & 31 : c & 31);
  let cajas = [colores];
  while (cajas.length < nColores) {
    let mejor = -1, rango = 0, eje = 0;
    cajas.forEach((b, i) => {
      if (b.length < 2) return;
      for (let k = 0; k < 3; k++) {
        let lo = 31, hi = 0;
        for (const c of b) { const v = comp(c, k); if (v < lo) lo = v; if (v > hi) hi = v; }
        if (hi - lo > rango) { rango = hi - lo; mejor = i; eje = k; }
      }
    });
    if (mejor < 0) break;
    const b = cajas[mejor].sort((x, y) => comp(x, eje) - comp(y, eje));
    let total = 0; for (const c of b) total += hist[c];
    let acc = 0, corte = 1;
    for (let i = 0; i < b.length - 1; i++) { acc += hist[b[i]]; if (acc >= total / 2) { corte = i + 1; break; } }
    cajas.splice(mejor, 1, b.slice(0, corte), b.slice(corte));
  }
  const pal = cajas.map((b) => {
    let r = 0, g = 0, bl = 0, n = 0;
    for (const c of b) { const w = hist[c]; r += comp(c, 0) * w; g += comp(c, 1) * w; bl += comp(c, 2) * w; n += w; }
    return [Math.round((r / n) * 8.23), Math.round((g / n) * 8.23), Math.round((bl / n) * 8.23)].map((v) => Math.min(255, v));
  });
  while (pal.length < 256) pal.push([0, 0, 0]);
  const lut = new Uint8Array(32768);
  for (let c = 0; c < 32768; c++) {
    const r = comp(c, 0) * 8.23, g = comp(c, 1) * 8.23, b = comp(c, 2) * 8.23;
    let best = 0, bd = Infinity;
    for (let i = 0; i < cajas.length; i++) {
      const p = pal[i]; const d = (p[0] - r) ** 2 + (p[1] - g) ** 2 + (p[2] - b) ** 2;
      if (d < bd) { bd = d; best = i; }
    }
    lut[c] = best;
  }
  return { pal, lut };
}

function lzwGif(indices) {
  const MIN = 8, CLEAR = 256, EOI = 257;
  const out = [];
  let cur = 0, bits = 0, size = MIN + 1, next = EOI + 1;
  const tabla = new Int16Array(4096 << 8).fill(-1);
  const emit = (code) => { cur |= code << bits; bits += size; while (bits >= 8) { out.push(cur & 255); cur >>= 8; bits -= 8; } };
  emit(CLEAR);
  let pref = indices[0];
  for (let i = 1; i < indices.length; i++) {
    const k = indices[i];
    const clave = (pref << 8) | k;
    const t = tabla[clave];
    if (t !== -1) { pref = t; continue; }
    emit(pref);
    if (next === 4096) { emit(CLEAR); tabla.fill(-1); size = MIN + 1; next = EOI + 1; }
    else { if (next >= 1 << size) size++; tabla[clave] = next++; }
    pref = k;
  }
  emit(pref);
  emit(EOI);
  if (bits > 0) out.push(cur & 255);
  return Buffer.from(out);
}

function escribirGif(crudo, n, w, h, fps, salida) {
  const datos = fs.readFileSync(crudo);
  const tam = w * h * 3;
  const frames = [];
  for (let i = 0; i < n; i++) frames.push(datos.subarray(i * tam, (i + 1) * tam));
  const { pal, lut } = paletaMediana(frames);
  const partes = [];
  const u16 = (v) => Buffer.from([v & 255, (v >> 8) & 255]);
  partes.push(Buffer.from("GIF89a", "ascii"), u16(w), u16(h), Buffer.from([0xf7, 0, 0]), Buffer.from(pal.flat()));
  partes.push(Buffer.from([0x21, 0xff, 0x0b]), Buffer.from("NETSCAPE2.0", "ascii"), Buffer.from([3, 1, 0, 0, 0]));
  const retardo = Math.max(2, Math.round(100 / fps));
  const idx = new Uint8Array(w * h);
  for (const f of frames) {
    for (let p = 0, i = 0; p < idx.length; p++, i += 3) idx[p] = lut[((f[i] >> 3) << 10) | ((f[i + 1] >> 3) << 5) | (f[i + 2] >> 3)];
    partes.push(Buffer.from([0x21, 0xf9, 4, 0x04]), u16(retardo), Buffer.from([0, 0]));
    partes.push(Buffer.from([0x2c]), u16(0), u16(0), u16(w), u16(h), Buffer.from([0, 8]));
    const lz = lzwGif(idx);
    for (let o = 0; o < lz.length; o += 255) { const b = lz.subarray(o, o + 255); partes.push(Buffer.from([b.length]), b); }
    partes.push(Buffer.from([0]));
  }
  partes.push(Buffer.from([0x3b]));
  fs.mkdirSync(path.dirname(salida), { recursive: true });
  fs.writeFileSync(salida, Buffer.concat(partes));
  return fs.statSync(salida).size;
}

// ---------- documentación local de la API y del manual ----------

function carpetaDocs() {
  const sub = path.join("Claude Extensions", "ant.dir.gh.blender.blender-mcp", "blmcp", "data");
  const cand = [];
  if (process.env.BUHO_BLENDER_DOCS) cand.push(process.env.BUHO_BLENDER_DOCS);
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  const roaming = process.env.APPDATA || path.join(os.homedir(), "AppData", "Roaming");
  for (const base of [path.join(local, "Claude-Data"), path.join(os.homedir(), "AppData", "Local", "Claude-Data")]) {
    try { for (const d of fs.readdirSync(base)) cand.push(path.join(base, d, sub)); } catch { /* no existe */ }
  }
  cand.push(path.join(roaming, "Claude", sub), path.join(local, "Claude", sub));
  return cand.find((c) => fs.existsSync(path.join(c, "api")) || fs.existsSync(path.join(c, "manual"))) || null;
}

function* archivosRst(dir) {
  let ents;
  try { ents = fs.readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of ents) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) yield* archivosRst(p);
    else if (e.name.endsWith(".rst")) yield p;
  }
}

// Trozos de un RST: un trozo por párrafo de nivel superior o por directiva (función, método, atributo…),
// con la ruta de secciones en la que está.
function trozosRst(texto) {
  const lineas = texto.split(/\r?\n/);
  const trozos = [];
  const niveles = [];
  const migas = [];
  let actual = [];
  const cerrar = () => {
    const t = actual.join("\n").trim();
    if (t) trozos.push({ texto: t, seccion: migas.filter(Boolean).join(" > ") });
    actual = [];
  };
  const subrayado = /^([=\-~^"'`#*+_:.])\1{2,}\s*$/;
  for (let i = 0; i < lineas.length; i++) {
    const l = lineas[i];
    const sig = lineas[i + 1];
    if (sig !== undefined && l.trim() && !subrayado.test(l) && subrayado.test(sig) && sig.trim().length >= l.trim().length - 2 && !/^\s/.test(l)) {
      cerrar();
      const sobre = i > 0 && subrayado.test(lineas[i - 1]) ? "o" : "";
      const marca = sig.trim()[0] + sobre;
      let nivel = niveles.indexOf(marca);
      if (nivel < 0) { niveles.push(marca); nivel = niveles.length - 1; }
      migas.length = nivel;
      migas[nivel] = l.trim();
      i++;
      continue;
    }
    if (subrayado.test(l) && sig !== undefined && lineas[i + 2] !== undefined && subrayado.test(lineas[i + 2])) continue;
    const sangria = l.length - l.trimStart().length;
    if (/^\s*\.\. [\w:-]+::/.test(l) && sangria <= 3) cerrar();
    else if (l.trim() && sangria <= 3 && i > 0 && !lineas[i - 1].trim() && actual.length && !/^\s*\.\. [\w:-]+::/.test(actual[0])) cerrar();
    else if (l.trim() && sangria === 0 && i > 0 && !lineas[i - 1].trim()) cerrar();
    actual.push(l);
  }
  cerrar();
  return trozos;
}

const VACIAS = new Set("the a an of to in on for and or is are how what with by from be as at it this that de la el los las un una y o en con para por que como del al se es".split(" "));

function buscarDocs(termino, { ambito, max, largo }) {
  const dir = carpetaDocs();
  if (!dir) fail("no encuentro la documentación local (data/api y data/manual de la extensión de Blender de Claude). Define BUHO_BLENDER_DOCS");
  const tokens = termino.toLowerCase().split(/\s+/).filter((t) => t && !VACIAS.has(t));
  if (!tokens.length) fail("falta el término de búsqueda");
  const frase = termino.toLowerCase().trim();
  const carpetas = ambito === "api" ? ["api"] : ambito === "manual" ? ["manual"] : ["api", "manual"];
  const hits = [];
  for (const c of carpetas) {
    for (const f of archivosRst(path.join(dir, c))) {
      const rel = path.relative(dir, f).replace(/\\/g, "/");
      const relL = rel.toLowerCase();
      const texto = fs.readFileSync(f, "utf8");
      const tl = texto.toLowerCase();
      if (!tokens.every((t) => tl.includes(t) || relL.includes(t))) continue;
      for (const tr of trozosRst(texto)) {
        const bl = tr.texto.toLowerCase();
        const sl = tr.seccion.toLowerCase();
        if (!tokens.every((t) => bl.includes(t) || sl.includes(t) || relL.includes(t))) continue;
        let puntos = 0;
        for (const t of tokens) {
          let n = 0, p = -1;
          while ((p = bl.indexOf(t, p + 1)) >= 0 && n < 8) n++;
          puntos += Math.log2(1 + n) + (sl.includes(t) ? 2 : 0) + (relL.includes(t) ? 3 : 0);
        }
        if (tokens.length > 1 && bl.includes(frase)) puntos += 4;
        const cabeza = tr.texto.split("\n")[0].toLowerCase();
        const firma = cabeza.match(/^\s*\.\. (?:function|method|class|attribute|data|classmethod|staticmethod)::\s*([\w.]+)/);
        if (firma) {
          const nombre = firma[1].split(".").pop();
          if (tokens.some((t) => nombre === t || firma[1] === t)) puntos += 10;
          else if (tokens.some((t) => cabeza.includes(t))) puntos += 3;
        }
        if (/^\s*\.\. (figure|image)::/.test(cabeza)) puntos *= 0.3;
        puntos /= 1 + 0.5 * Math.log10(1 + tr.texto.length / 1500);
        hits.push({ ruta: f, tipo: c, seccion: tr.seccion, puntos: Math.round(puntos * 100) / 100, texto: tr.texto });
      }
    }
  }
  hits.sort((a, b) => b.puntos - a.puntos);
  return {
    termino, carpeta: dir, encontrados: hits.length,
    resultados: hits.slice(0, max).map((h) => ({ ...h, texto: h.texto.length > largo ? h.texto.slice(0, largo) + " […]" : h.texto })),
  };
}

// ---------- comandos ----------

const AYUDA = `buho-blender ${VERSION} — Blender para agentes (Claude, Codex, scripts). Salida en JSON.

Con Blender abierto (complemento "MCP" de Blender Lab activo, puerto ${PORT}) lo que haces se ve en directo.
Con --fondo se lanza Blender sin ventana (${BLENDER}); --archivo <x.blend> abre ese archivo antes.

Mirar
  estado                                    versión, archivo abierto, cambios sin guardar, nº de objetos
  escena                                    objetos con posición, tamaño, caras, materiales
  stats [--coleccion X] [--top 15]          vértices, caras, triángulos (con modificadores e instancias),
                                            materiales, medidas en metros, instancias y enlazados
  captura <salida.png> [--vista tres_cuartos|frente|atras|derecha|izquierda|arriba|abajo|iso|camara]
          [--vistas frente,derecha,tres_cuartos,arriba]  varias vistas en UNA hoja de contactos [--columnas N]
          [--ancho 1280 --alto 720] [--motor BLENDER_EEVEE|BLENDER_WORKBENCH|CYCLES] [--muestras N]
          [--objetos "A,B" | --coleccion X] [--transparente] [--frame N] [--con-niebla]
                                            (Cycles usa la GPU si hay; humo/niebla/volúmenes no cuentan para el
                                            encuadre y se ocultan salvo --con-niebla o --vista camara;
                                            fondos, suelos y estudios salen pero no cuentan para el encuadre:
                                            nombre Fondo/Suelo/Floor/Backdrop/Estudio/Ciclorama, propiedad
                                            buho_fondo=True, o plano grande / caja que lo envuelve todo.
                                            --encuadrar "A,B" fuerza qué se encuadra)
  giro <salida.mp4|.gif> [--segundos 4] [--fps 24] [--vista tres_cuartos] [--ancho 720 --alto 720]
          [--motor ...] [--muestras N] [--objetos "A,B" | --coleccion X]   vuelta de 360°, no deja nada
  pantalla <salida.png> [--render]          la vista 3D del Blender abierto tal como se ve
                                            (--render: render OpenGL de la vista, sin interfaz)
  video <salida.mp4> [--camara fija|orbita|frontal] [--vueltas 1] [--segundos 10] [--fps 24]
          [--motor EEVEE|CYCLES|WORKBENCH] [--muestras N] [--ancho 1280 --alto 720] [--vista tres_cuartos]
          [--luces auto|escena|estudio] [--hoja revision.png [--n 6]] [--objetos "A,B" | --coleccion X]
                                            renderiza la animación de la escena; no deja nada en la escena
  comparar <referencia.png|jpg> [--blend x.blend | --fondo --archivo x.blend] [--vista camara] [--salida c.png]
          [--alto 540] [--opaco] [--umbral 0.12] [--frame N] [--objetos ...]
                                            render con el aspecto de la referencia y una imagen referencia | render |
                                            superposición | siluetas, con métricas de silueta y color por zonas

Esqueleto y animación
  esqueleto [objeto] [--coleccion X] [--tipo humanoide|cuadrupedo|cadena] [--auto] [--huesos huesos.json]
          [--cabezon] [--n 4] [--nombre X] [--sin-emparentar]
                                            crea un Armature ajustado al modelo; una malla = pesos automáticos,
                                            piezas sueltas = cada pieza a su hueso (por nombre y posición)
  animar <andar|punetazos|saludar|saltar|bailar|idle|asentir|girar_cabeza> [--armature X] [--segundos 4]
          [--fps 24] [--bucle] [--nla] [--lado L|R] [--intensidad 1] [--nombre X] [--inicio F]
          [--pies apoyar|no_atravesar|no]   pies en el suelo (por defecto apoyar; saltar y --json: no_atravesar)
  animar --json poses.json [...]            animación a medida: [{frame, huesos: {nombre: {rot:[x,y,z]°, loc:[m]}},
                                            interp, easing}]; --nla encadena acciones en una pista NLA
  animar --reposo [--armature X]            quita la acción activa y deja la pose de reposo
  importar-animacion <archivo.fbx> [--armature X] [--nombre X] [--en-sitio] [--nla] [--conservar]
          [--pies apoyar|no_atravesar|no] [--cabeza auto|estable|libre] [--estabilidad 0.7]
                                            p. ej. de Mixamo: la pasa al esqueleto por nombres de hueso; en figuras
                                            cabezonas la cabeza se queda derecha y el tronco se inclina menos

Renders en segundo plano
  <giro|video|captura|run|comparar> ... --en-cola [--paralelo]
                                            vuelve al instante con un id; se renderiza en un Blender aparte, de uno
                                            en uno (en directo, sobre una copia de la escena abierta)
  cola [lista] | cola ver <id> | cola esperar <id> | cola cancelar <id> | cola limpiar
                                            estado, fotogramas hechos/total, %, tiempo restante y resultado

Hacer
  run <script.py | "código" | ->            ejecuta Python (bpy). Para devolver datos: result = {...}
                                            Se puede "import buho3d" (lib/ del repo) y lo de la carpeta del script
                                            --var "VOXEL=0.009,X=1": variables globales para el script
  vigilar <script.py> [--captura salida.png --vista ... | --vistas ...] [--veces N]
                                            lo vuelve a ejecutar cada vez que se guarda (o cambia lib/buho3d.py).
                                            Ctrl+C para salir
  importar <archivo.glb|.gltf|.fbx|.obj|.stl|.ply|.usd|.blend> [--en Coleccion]
                                            .blend: append de todos sus objetos, o --objetos "A,B" / --colecciones "X,Y"
  exportar <salida.glb|.gltf|.fbx|.obj> [--objetos "A,B" | --coleccion X] [--unir]
  limpiar                                   purga datos huérfanos (mallas, materiales… sin usuarios)
  optimizar <salida.glb|.fbx> [--caras 30000] [--texturas 2048] [--metodo auto|decimate|remallado] [--pngs] [--jpg]   baja + bake PBR (Unity/web)
  imprimir <salida.stl|.3mf> [--altura-mm 120] [--hueco 2] [--unir] [--voxel-mm X] [--grosor-min 1] [--ambos]   malla cerrada en mm + informe
  visor <salida.html> [--glb x.glb] [--titulo "..."] [--abrir]   HTML autocontenido con visor 3D (sin --glb exporta la escena)

Archivos
  nuevo [salida.blend] [--vacia] [--descartar]   escena de fábrica sin el cubo (--vacia: sin cámara ni luz)
  cargar <archivo.blend> [--descartar]      abre un .blend en el Blender abierto
                                            (antes guarda el actual si tiene ruta y cambios)
  guardar <archivo.blend>                   guarda la escena
  abrir [archivo.blend]                     abre Blender con ventana (el puerto se activa solo)

Ayuda
  docs <término> [--api | --manual] [--max 6] [--largo 1500]   busca en la documentación local de Blender
  skill                                     ruta de la skill (SKILL.md)

Opciones comunes: --fondo, --archivo <x.blend>, --guardar <x.blend> (guarda al acabar), --texto (muestra lo que
imprima el script), --puerto ${PORT}, --json (JSON compacto en una línea), --timeout <segundos> (600)
Variables: BUHO_BLENDER_PATH, BUHO_BLENDER_PORT, BUHO_BLENDER_HOST, BUHO_BLENDER_DOCS`;

// --var "VOXEL=0.009,NOMBRE=x": variables globales para el script (números y JSON se convierten; si no, texto).
function variables(opts) {
  const out = {};
  if (typeof opts.var !== "string") return out;
  for (const par of opts.var.split(",")) {
    const i = par.indexOf("=");
    if (i < 1) fail(`--var mal escrito: ${par} (usa NOMBRE=valor)`);
    const k = par.slice(0, i).trim(), v = par.slice(i + 1).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(k)) fail(`--var: nombre no válido: ${k}`);
    try { out[k] = JSON.parse(v); } catch { out[k] = v; }
  }
  return out;
}

function opcionesCaptura(opts, salida) {
  const vistas = lista(opts.vistas).length ? lista(opts.vistas) : [str(opts.vista) || "tres_cuartos"];
  const varias = vistas.length > 1;
  return {
    salida, vistas,
    ancho: num(opts.ancho, varias ? 800 : 1280), alto: num(opts.alto, varias ? 600 : 720),
    motor: str(opts.motor) || "BLENDER_EEVEE", muestras: opts.muestras ? num(opts.muestras) : null,
    objetos: lista(opts.objetos), coleccion: str(opts.coleccion), fondoTransparente: !!opts.transparente,
    columnas: opts.columnas ? num(opts.columnas) : null, conNiebla: !!opts["con-niebla"], frame: opts.frame !== undefined ? num(opts.frame) : null,
    encuadrar: lista(opts.encuadrar),
  };
}

// Espera a que el Blender abierto termine de cambiar de archivo (nuevo/cargar diferidos) y devuelve su estado.
async function esperarCambio(marca, segundos = 60) {
  const code = PY_ESTADO + `\nh = bpy.app.driver_namespace.get("_buho_hecho") or {}\nresult["_hecho"] = h if h.get("marca") == ${py(marca)} else None\n`;
  for (let i = 0; i < segundos * 4; i++) {
    await new Promise((r) => setTimeout(r, 250));
    try {
      const r = await sendToOpen(code, 3000);
      if (r.status === "ok" && r.result._hecho) {
        if (r.result._hecho.error) fail("Blender no ha podido cambiar de archivo: " + r.result._hecho.error);
        delete r.result._hecho;
        return r.result;
      }
    } catch { /* Blender ocupado cargando */ }
  }
  fail("Blender no ha terminado de cambiar de archivo en " + segundos + " s (mira la ventana)");
}

async function vigilar(arg, opts) {
  if (!arg) fail("falta el script .py que vigilar");
  const file = path.resolve(arg);
  if (!fs.existsSync(file)) fail(`no existe ${file}`);
  const extra = [path.join(LIB, "buho3d.py"), ...lista(opts.tambien).map((f) => path.resolve(f))];
  const veces = opts.veces ? num(opts.veces) : Infinity;
  let corriendo = false, pendiente = false, hechas = 0, espera = null;
  const vigilados = [file, ...extra];
  const terminar = (codigo = 0) => {
    for (const f of vigilados) fs.unwatchFile(f);
    process.exit(codigo);
  };
  const linea = (obj) => process.stdout.write(JSON.stringify(obj, null, opts.json ? 0 : 2) + "\n");
  const ejecutar = async (motivo) => {
    if (corriendo) { pendiente = true; return; }
    corriendo = true;
    const t0 = Date.now();
    const out = { hora: new Date().toLocaleTimeString("es-ES"), motivo, script: file };
    try {
      const resp = await execute(pyUsuario(fs.readFileSync(file, "utf8"), file, variables(opts)), opts);
      out.ok = resp.status === "ok";
      if (out.ok) out.result = resp.result;
      else out.error = limpiarTraceback(resp.message || "").trim();
      if (resp.stdout) out.stdout = String(resp.stdout);
      if (out.ok && typeof opts.captura === "string") {
        const cap = await execute(pyCaptura(opcionesCaptura(opts, path.resolve(opts.captura))), opts, { guardar: false });
        out.captura = cap.status === "ok" ? cap.result.captura : { error: limpiarTraceback(cap.message || "").trim() };
      }
    } catch (e) { out.ok = false; out.error = e.message; }
    out.segundos = Math.round((Date.now() - t0) / 100) / 10;
    linea(out);
    hechas++;
    corriendo = false;
    if (hechas >= veces) terminar(0);
    if (pendiente) { pendiente = false; ejecutar("cambio durante la ejecución"); }
  };
  for (const f of vigilados) {
    fs.watchFile(f, { interval: 300 }, (cur, prev) => {
      if (cur.mtimeMs === prev.mtimeMs || !cur.mtimeMs) return;
      clearTimeout(espera);
      espera = setTimeout(() => ejecutar("guardado " + path.basename(f)), 150);
    });
  }
  process.on("SIGINT", () => { process.stderr.write("buho-blender: fin de la vigilancia\n"); terminar(0); });
  process.stderr.write(`buho-blender: vigilando ${file}${fs.existsSync(extra[0]) ? " y lib/buho3d.py" : ""} (Ctrl+C para salir)\n`);
  await ejecutar("inicio");
  await new Promise(() => {}); // hasta Ctrl+C o --veces
}

process.stdout.on("error", (e) => { if (e.code === "EPIPE") process.exit(0); throw e; });

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const [cmd, arg] = opts._;
  if (opts.puerto) PORT = num(opts.puerto, PORT);
  if (opts.timeout) TIMEOUT_MS = num(opts.timeout, 600) * 1000;

  // Cola de renders (lib/buho_cola.mjs): --en-cola lanza la orden aparte y vuelve con un id.
  if (opts["en-cola"] || opts["segundo-plano"]) {
    const r = await cola.encolar({
      cmd, argv: process.argv.slice(2), opts, script: fileURLToPath(import.meta.url),
      guardarCopia: async (copia) => {
        const resp = await sendToOpen(`import bpy, os\nos.makedirs(os.path.dirname(${py(copia)}), exist_ok=True)\nbpy.ops.wm.save_as_mainfile(filepath=${py(copia)}, copy=True)\nresult = {"copia": ${py(copia)}}`);
        if (resp.status !== "ok") throw new Error("no he podido copiar la escena abierta: " + resp.message);
      },
    });
    process.stdout.write(salidaJSON(r, opts) + "\n");
    return;
  }
  if (typeof opts._trabajo === "string") {
    if (!opts.timeout) TIMEOUT_MS = 6 * 3600 * 1000;
    await cola.iniciarTrabajo(opts._trabajo);
  }

  switch (cmd) {
    case "cola":
    case "trabajos": {
      const sub = arg || "lista";
      const id = opts._[2];
      const linea = (o) => process.stdout.write(JSON.stringify(o, null, opts.json ? 0 : 2) + "\n");
      if (sub === "lista" || sub === "ls") return linea(cola.listar());
      if (sub === "limpiar") return linea(cola.limpiar());
      if (!id) fail(`falta el id: buho-blender cola ${sub} <id>`);
      if (sub === "ver") return linea(cola.ver(id, num(opts.lineas, 15)));
      if (sub === "cancelar") return linea(cola.cancelar(id));
      if (sub === "esperar") {
        const fin = await cola.esperar(id, (o) => process.stdout.write(JSON.stringify(o) + "\n"), num(opts.cada, 2) * 1000);
        linea(fin);
        process.exit(fin.estado === "hecho" ? 0 : 1);
      }
      fail("cola [lista] | ver <id> | esperar <id> | cancelar <id> | limpiar");
      return;
    }
    case undefined:
    case "ayuda":
    case "help":
    case "--help":
      console.log(AYUDA);
      return;
    case "version":
      console.log(VERSION);
      return;
    case "skill": {
      for (const c of [path.join(REPO, "SKILL.md"), path.join(REPO, "skills", "buho-blender", "SKILL.md")])
        if (fs.existsSync(c)) { console.log(path.resolve(c)); return; }
      fail("no encuentro SKILL.md");
      return;
    }
    case "abrir": {
      if (!fs.existsSync(BLENDER)) fail(`no encuentro Blender en ${BLENDER}`);
      const args = arg ? [path.resolve(arg)] : [];
      spawn(BLENDER, args, { detached: true, stdio: "ignore" }).unref();
      for (let i = 0; i < 60; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try { await sendToOpen("result = {'ok': True}", 3000); console.log(salidaJSON({ abierto: true, puerto: PORT }, opts)); return; }
        catch { /* sigue esperando */ }
      }
      fail("Blender se ha abierto pero el puerto no responde: activa 'Permitir acceso online' y el complemento MCP");
      return;
    }
    case "estado": {
      const r = await execute(PY_ESTADO, opts, { guardar: false });
      if (r.status === "ok") r.result.puerto = opts.fondo ? null : PORT;
      return show(r, opts);
    }
    case "escena":
      return show(await execute(PY_ESCENA, opts, { guardar: false }), opts);
    case "stats":
      return show(await execute(pyStats({ coleccion: str(opts.coleccion), top: num(opts.top, 15) }), opts, { guardar: false }), opts);
    case "run": {
      const { code, file } = readCode(arg);
      return show(await execute(pyUsuario(code, file, variables(opts)), opts), opts);
    }
    case "vigilar":
      return vigilar(arg, opts);
    case "captura": {
      const salida = path.resolve(arg || path.join(os.tmpdir(), `buho-blender-${Date.now()}.png`));
      return show(await execute(pyCaptura(opcionesCaptura(opts, salida)), opts, { guardar: false }), opts);
    }
    case "giro": {
      if (!arg) fail("falta la salida (.mp4 o .gif)");
      const salida = path.resolve(arg);
      const ext = path.extname(salida).toLowerCase();
      if (![".mp4", ".gif"].includes(ext)) fail("el giro sale en .mp4 o .gif");
      const gif = ext === ".gif";
      const par = (v) => Math.max(16, Math.round(v / 2) * 2); // H.264 pide medidas pares
      const tmpdir = path.join(os.tmpdir(), `buho_giro_${process.pid}_${Date.now()}`);
      const fps = num(opts.fps, gif ? 20 : 24);
      const code = pyGiro({
        salida, gif, tmpdir, segundos: num(opts.segundos, 4), fps,
        ancho: par(num(opts.ancho, gif ? 480 : 720)), alto: par(num(opts.alto, gif ? 480 : 720)),
        motor: str(opts.motor) || "BLENDER_EEVEE", muestras: opts.muestras ? num(opts.muestras) : null,
        vista: str(opts.vista) || "tres_cuartos", objetos: lista(opts.objetos), coleccion: str(opts.coleccion),
        conNiebla: !!opts["con-niebla"], encuadrar: lista(opts.encuadrar),
      });
      const r = await execute(code, opts, { guardar: false });
      if (r.status === "ok" && gif) {
        try {
          const { crudo, n, tamano_px: [w, h] } = r.result;
          r.result.bytes = escribirGif(crudo, n, w, h, fps, salida);
          r.result.giro = salida;
          delete r.result.crudo; delete r.result.n;
        } finally { fs.rmSync(tmpdir, { recursive: true, force: true }); }
      } else fs.rmSync(tmpdir, { recursive: true, force: true });
      return show(r, opts);
    }
    case "esqueleto": {
      const args = {
        objeto: arg || null, coleccion: str(opts.coleccion), tipo: str(opts.tipo) || "humanoide", auto: !!opts.auto,
        huesos: typeof opts.huesos === "string" ? path.resolve(opts.huesos) : null, nombre: str(opts.nombre),
        cabezon: !!opts.cabezon, n: num(opts.n, 4), sin_emparentar: !!opts["sin-emparentar"],
      };
      if (args.huesos && !fs.existsSync(args.huesos)) fail(`no existe ${args.huesos}`);
      return show(await execute(pyRig("esqueleto", args), opts), opts);
    }
    case "animar": {
      if (opts.reposo) return show(await execute(pyRig("reposo", { armature_nombre: str(opts.armature) }), opts), opts);
      const poses = typeof opts.json === "string" ? path.resolve(opts.json) : null;
      if (poses && !fs.existsSync(poses)) fail(`no existe ${poses}`);
      if (!poses && !arg) fail("falta la acción (andar, punetazos, saludar, saltar, bailar, idle, asentir, girar_cabeza) o --json poses.json");
      const args = {
        armature_nombre: str(opts.armature), accion: arg || null, poses, segundos: opts.segundos ? num(opts.segundos) : null,
        fps: num(opts.fps, 24), bucle: !!opts.bucle, nla: !!opts.nla, lado: str(opts.lado) || "R",
        nombre: str(opts.nombre), intensidad: num(opts.intensidad, 1), inicio: opts.inicio ? num(opts.inicio) : null,
        pies: str(opts.pies),
      };
      return show(await execute(pyRig("animar", args), opts), opts);
    }
    case "importar-animacion": {
      if (!arg) fail("falta el .fbx con la animación");
      const archivo = path.resolve(arg);
      if (!fs.existsSync(archivo)) fail(`no existe ${archivo}`);
      const args = {
        archivo, armature_nombre: str(opts.armature), nombre: str(opts.nombre), en_sitio: !!opts["en-sitio"], conservar: !!opts.conservar, nla: !!opts.nla,
        pies: str(opts.pies) || "apoyar", cabeza: str(opts.cabeza) || "auto", estabilidad: opts.estabilidad !== undefined ? num(opts.estabilidad) : null,
      };
      return show(await execute(pyRig("importar_animacion", args), opts), opts);
    }
    case "video": {
      if (!arg) fail("falta la salida .mp4");
      const salida = path.resolve(arg);
      if (path.extname(salida).toLowerCase() !== ".mp4") fail("el vídeo sale en .mp4");
      const camara = str(opts.camara) || "fija";
      if (!["fija", "orbita", "frontal"].includes(camara)) fail("--camara fija|orbita|frontal");
      const motores = { EEVEE: "BLENDER_EEVEE", CYCLES: "CYCLES", WORKBENCH: "BLENDER_WORKBENCH" };
      const m = (str(opts.motor) || "EEVEE").toUpperCase();
      const par = (v) => Math.max(16, Math.round(v / 2) * 2);
      const tmpdir = path.join(os.tmpdir(), `buho_video_${process.pid}_${Date.now()}`);
      const code = pyVideo({
        salida, tmpdir, camara, vueltas: num(opts.vueltas, 1), segundos: opts.segundos ? num(opts.segundos) : null,
        fps: opts.fps ? num(opts.fps) : null, ancho: par(num(opts.ancho, 1280)), alto: par(num(opts.alto, 720)),
        motor: motores[m] || m, muestras: opts.muestras ? num(opts.muestras) : null, vista: str(opts.vista) || "tres_cuartos",
        objetos: lista(opts.objetos), coleccion: str(opts.coleccion), luces: str(opts.luces) || "auto",
        hoja: typeof opts.hoja === "string" ? path.resolve(opts.hoja) : null, nHoja: num(opts.n, 6), conNiebla: !!opts["con-niebla"],
        encuadrar: lista(opts.encuadrar),
      });
      const r = await execute(code, opts, { guardar: false });
      fs.rmSync(tmpdir, { recursive: true, force: true });
      return show(r, opts);
    }
    case "comparar": {
      if (!arg) fail("falta la imagen de referencia: buho-blender comparar foto.png [--blend x.blend] [--vista camara]");
      const referencia = path.resolve(arg);
      if (!fs.existsSync(referencia)) fail(`no existe ${referencia}`);
      if (typeof opts.blend === "string") { opts.fondo = true; opts.archivo = opts.blend; }
      const base = path.basename(referencia, path.extname(referencia));
      const salida = path.resolve(str(opts.salida) || `comparacion_${base}.png`);
      const renderRuta = salida.replace(/\.png$/i, "") + "_render.png";
      const code = pyComparar({
        referencia, salida, renderRuta, alto: num(opts.alto, 540), vista: str(opts.vista) || "camara",
        motor: str(opts.motor) || "BLENDER_EEVEE", muestras: opts.muestras ? num(opts.muestras) : null,
        objetos: lista(opts.objetos), coleccion: str(opts.coleccion), frame: opts.frame !== undefined ? num(opts.frame) : null,
        conNiebla: !!opts["con-niebla"], encuadrar: lista(opts.encuadrar), opaco: !!opts.opaco, umbral: num(opts.umbral, 0.12),
      });
      return show(await execute(code, opts, { guardar: false }), opts);
    }
    case "optimizar":
    case "imprimir":
    case "visor":
      return comandoExport(cmd, arg, opts, { execute, show, fail });
    case "pantalla": {
      if (opts.fondo) fail("pantalla necesita el Blender abierto con ventana (sin --fondo); usa captura");
      const salida = path.resolve(arg || path.join(os.tmpdir(), `buho-blender-pantalla-${Date.now()}.png`));
      return show(await execute(pyPantalla({ salida, render: !!opts.render }), opts, { guardar: false }), opts);
    }
    case "importar": {
      if (!arg) fail("falta el archivo que importar");
      const code = pyImportar({ archivo: path.resolve(arg), objetos: lista(opts.objetos), colecciones: lista(opts.colecciones), destino: str(opts.en) });
      return show(await execute(code, opts), opts);
    }
    case "exportar": {
      if (!arg) fail("falta la ruta de salida (.glb, .fbx u .obj)");
      const code = pyExportar({ salida: path.resolve(arg), objetos: lista(opts.objetos), coleccion: str(opts.coleccion), unir: !!opts.unir });
      return show(await execute(code, opts), opts);
    }
    case "limpiar":
      return show(await execute(PY_LIMPIAR, opts), opts);
    case "nuevo": {
      const guardarEn = arg ? path.resolve(arg) : (typeof opts.guardar === "string" ? path.resolve(opts.guardar) : null);
      if (opts.fondo && !guardarEn) fail("con --fondo, nuevo necesita dónde guardar: buho-blender nuevo salida.blend --fondo");
      const marca = `${process.pid}-${Date.now()}`;
      const r = await execute(pyNuevo({ vacia: !!opts.vacia, guardarEn, descartar: !!opts.descartar, marca }), opts, { guardar: false });
      if (r.status === "ok" && r.result.diferido) {
        const est = await esperarCambio(marca);
        Object.assign(r.result, { archivo: est.archivo, objetos: est.objetos });
      }
      return show(r, opts);
    }
    case "cargar": {
      if (!arg) fail("falta el .blend que cargar");
      if (opts.fondo) fail("cargar es para el Blender abierto; sin ventana usa --archivo x.blend en cada comando");
      const archivo = path.resolve(arg);
      if (!fs.existsSync(archivo)) fail(`no existe ${archivo}`);
      const marca = `${process.pid}-${Date.now()}`;
      const r = await execute(pyCargar({ archivo, descartar: !!opts.descartar, marca }), opts, { guardar: false });
      if (r.status === "ok" && r.result.diferido) {
        const est = await esperarCambio(marca);
        Object.assign(r.result, { archivo: est.archivo, objetos: est.objetos });
      }
      return show(r, opts);
    }
    case "guardar": {
      if (!arg) fail("falta la ruta del .blend");
      const code = `import bpy, os\nos.makedirs(os.path.dirname(${py(path.resolve(arg))}) or ".", exist_ok=True)\nbpy.ops.wm.save_as_mainfile(filepath=${py(path.resolve(arg))})\nresult = {"guardado": bpy.data.filepath}`;
      return show(await execute(code, opts, { guardar: false }), opts);
    }
    case "docs": {
      const termino = opts._.slice(1).join(" ");
      if (!termino) fail("falta el término: buho-blender docs \"bmesh bevel\"");
      const r = buscarDocs(termino, { ambito: opts.api ? "api" : opts.manual ? "manual" : null, max: num(opts.max, 6), largo: num(opts.largo, 1500) });
      process.stdout.write(salidaJSON(r, opts) + "\n");
      return;
    }
    default:
      fail(`comando desconocido: ${cmd}\n\n${AYUDA}`);
  }
}

main().catch((e) => fail(e.message));
