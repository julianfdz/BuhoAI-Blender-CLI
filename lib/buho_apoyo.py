"""buho_apoyo: pies en el suelo y cabeza estable para las animaciones de buho_rig.

- `apoyar(arm, f0, f1, modo)`: ajusta la altura de la cadera fotograma a fotograma para que el punto más bajo
  del personaje toque el suelo («apoyar») o, al menos, no lo atraviese («no_atravesar», para saltos).
  El suelo se detecta solo: lo que haya justo debajo del personaje en reposo (peana, suelo, ciclorama),
  con rayos hacia abajo; si no hay nada, la altura de sus pies en reposo.
- `Estabilizador`: en `importar-animacion`, amortigua la inclinación de cadera, columna y cuello y deja la
  cabeza casi derecha (conserva el giro a los lados). En figuras cabezonas una inclinación normal del cuerpo
  mueve una cabeza enorme y queda exagerada.

Lo llama buho_rig; también se puede usar desde un script:

    import buho_apoyo as ba
    ba.apoyar(bpy.data.objects["Funko_Esqueleto"], 1, 48, "apoyar")
"""

import bpy
from mathutils import Quaternion, Vector

import buho_rig as _br

TIPOS_GEOM = {"MESH", "CURVE", "FONT", "SURFACE", "META"}


# ======================================================================
# Piezas del personaje y punto más bajo
# ======================================================================

def piezas(arm):
    """Objetos con geometría que mueve el esqueleto: hijos (de hueso o de objeto, a cualquier nivel) o con
    modificador Armature que apunta a él. Peanas, bases y suelos fuera."""
    out = []
    for o in bpy.context.scene.objects:
        if o.type not in TIPOS_GEOM or o.name.startswith("_buho") or _br._excluida(o):
            continue
        p, colgado = o.parent, False
        while p is not None:
            if p == arm:
                colgado = True
                break
            p = p.parent
        if not colgado:
            colgado = any(m.type == "ARMATURE" and m.object == arm for m in getattr(o, "modifiers", []))
        if colgado:
            out.append(o)
    return out


class _Medidor:
    """Punto más bajo (z en mundo) de las piezas. Las rígidas guardan sus vértices locales una vez;
    las deformadas por el esqueleto se evalúan en cada fotograma."""

    def __init__(self, objs):
        import numpy as np
        self.np = np
        self.rigidas, self.deformadas = [], []
        dg = bpy.context.evaluated_depsgraph_get()
        for o in objs:
            deforma = any(m.type == "ARMATURE" for m in getattr(o, "modifiers", []))
            if deforma:
                self.deformadas.append(o)
                continue
            co = self._co(o.evaluated_get(dg))
            if co is not None:
                self.rigidas.append((o, co))

    def _co(self, oe):
        np = self.np
        try:
            me = oe.to_mesh()
        except RuntimeError:
            return None
        if me is None:
            return None
        n = len(me.vertices)
        co = None
        if n:
            co = np.empty(n * 3, dtype=np.float64)
            me.vertices.foreach_get("co", co)
            co = co.reshape(n, 3)
        oe.to_mesh_clear()
        return co

    def mas_bajo(self):
        """(z, x, y) del punto más bajo en el estado actual."""
        np = self.np
        dg = bpy.context.evaluated_depsgraph_get()
        mejor = None
        for o, co in self.rigidas:
            m = np.array(o.evaluated_get(dg).matrix_world)
            z = co @ m[2, :3] + m[2, 3]
            i = int(z.argmin())
            if mejor is None or z[i] < mejor[0]:
                p = m[:3, :3] @ co[i] + m[:3, 3]
                mejor = (float(z[i]), float(p[0]), float(p[1]))
        for o in self.deformadas:
            oe = o.evaluated_get(dg)
            co = self._co(oe)
            if co is None:
                continue
            m = np.array(oe.matrix_world)
            w = co @ m[:3, :3].T + m[:3, 3]
            i = int(w[:, 2].argmin())
            if mejor is None or w[i, 2] < mejor[0]:
                mejor = (float(w[i, 2]), float(w[i, 0]), float(w[i, 1]))
        return mejor


def _raiz(arm):
    b = arm.data.bones.get("cadera")
    if b is None:
        b = next((b for b in arm.data.bones if b.parent is None), None)
    while b is not None and b.parent is not None:
        b = b.parent
    return b


def _rayo_abajo(x, y, z, ignorar, dg):
    """Primer objeto ajeno al personaje bajo (x, y, z): (z del impacto, nombre) o None."""
    sc = bpy.context.scene
    origen = Vector((x, y, z))
    abajo = Vector((0, 0, -1))
    for _ in range(64):
        ok, loc, _n, _i, ob, _m = sc.ray_cast(dg, origen, abajo)
        if not ok:
            return None
        if ob is not None and (ob.original in ignorar or ob.original.name.startswith("_buho")):
            origen = loc + abajo * 1e-4
            continue
        return loc.z, ob.original.name if ob is not None else None
    return None


def detectar_suelo(arm, objs=None, ignorar=None):
    """Altura del suelo bajo el personaje: lo que haya justo debajo de sus pies en reposo (peana, suelo,
    ciclorama) o, si no hay nada, la altura de sus pies en reposo."""
    objs = objs if objs is not None else piezas(arm)
    if not objs:
        raise RuntimeError("el esqueleto %s no mueve ninguna pieza" % arm.name)
    H = float(arm.get("buho_altura", 1.0))
    viejo = arm.data.pose_position
    arm.data.pose_position = "REST"
    try:
        bpy.context.view_layer.update()
        med = _Medidor(objs)
        z0, x0, y0 = med.mas_bajo()
        mn, mx = _br._caja(objs)
        dg = bpy.context.evaluated_depsgraph_get()
        ignorar = set(objs) | {arm} | set(ignorar or [])
        mejor = None
        alto = z0 + 0.05 * H
        for x, y in ((x0, y0), ((mn.x + mx.x) / 2, (mn.y + mx.y) / 2)):
            r = _rayo_abajo(x, y, alto, ignorar, dg)
            if r is not None and r[0] >= z0 - 0.3 * H and (mejor is None or r[0] > mejor[0]):
                mejor = r
    finally:
        arm.data.pose_position = viejo
        bpy.context.view_layer.update()
    if mejor is not None:
        return {"z": mejor[0], "de": mejor[1], "pies_en_reposo": z0}
    return {"z": z0, "de": "pies en reposo", "pies_en_reposo": z0}


def apoyar(arm, f0, f1, modo="apoyar", objetivo=None, suelo=None, ignorar=None):
    """Ajusta la altura de la cadera en cada fotograma de [f0, f1] de la acción activa.

    modo «apoyar»: el punto más bajo del personaje queda en el suelo (+ objetivo[f], la altura que lleve la
    animación de origen, para no aplastar un salto importado). «no_atravesar»: solo sube lo que se hunde.
    """
    if modo in (None, "no", "ninguno", False):
        return None
    if modo not in ("apoyar", "no_atravesar"):
        raise RuntimeError("--pies apoyar|no_atravesar|no")
    sc = bpy.context.scene
    ad = arm.animation_data
    if ad is None or ad.action is None:
        raise RuntimeError("el esqueleto no tiene acción activa que ajustar")
    objs = piezas(arm)
    if not objs:
        return {"modo": modo, "aviso": "el esqueleto no mueve ninguna pieza: no se ajustan los pies"}
    s = suelo or detectar_suelo(arm, objs, ignorar)
    raiz = _raiz(arm)
    if raiz is None:
        return {"modo": modo, "aviso": "el esqueleto no tiene hueso raíz"}
    pb = arm.pose.bones[raiz.name]
    med = _Medidor(objs)
    # Mundo -> espacio del hueso raíz (su location va en los ejes del hueso en reposo).
    a_local = raiz.matrix_local.to_3x3().inverted() @ arm.matrix_world.to_3x3().inverted()
    frame_viejo = sc.frame_current
    datos = []
    for f in range(int(f0), int(f1) + 1):
        sc.frame_set(f)
        bajo = med.mas_bajo()
        if bajo is None:
            continue
        meta = s["z"] + (objetivo.get(f, 0.0) if objetivo else 0.0)
        d = meta - bajo[0]
        if modo == "no_atravesar":
            d = max(0.0, d)
        datos.append((f, pb.location.copy(), d))
    # Segunda pasada: claves en todos los fotogramas (si se insertan al medir, cambian los siguientes).
    for f, loc, d in datos:
        pb.location = loc + a_local @ Vector((0, 0, d))
        pb.keyframe_insert("location", frame=f, group=raiz.name)
    act = ad.action
    if act.get("buho_bucle"):
        for fc in _br._canal(act, ad.action_slot):
            if fc.data_path.endswith('"].location') and not any(m.type == "CYCLES" for m in fc.modifiers):
                fc.modifiers.new("CYCLES")
    sc.frame_set(frame_viejo)
    ds = [d for _, _, d in datos] or [0.0]
    return {"modo": modo, "suelo_z": round(s["z"], 4), "suelo": s["de"], "hueso": raiz.name,
            "fotogramas": len(datos), "ajuste_cm": [round(min(ds) * 100, 2), round(max(ds) * 100, 2)]}


def altura_minima_huesos(arm):
    """z en mundo del punto más bajo de los huesos (cabezas y colas) en la pose actual."""
    mw = arm.matrix_world
    return min(min((mw @ pb.head).z, (mw @ pb.tail).z) for pb in arm.pose.bones)


def objetivo_desde_origen(alturas, escala, umbral):
    """Altura sobre el suelo que debe llevar el personaje en cada fotograma, a partir de la del esqueleto de
    origen: 0 mientras apoya (por debajo de `umbral`), y lo que suba por encima en saltos."""
    if not alturas:
        return {}
    base = min(alturas.values())
    return {f: max(0.0, h - base - umbral) * escala for f, h in alturas.items()}


# ======================================================================
# Cabeza estable en figuras cabezonas
# ======================================================================

def es_cabezon(arm):
    if "buho_cabezon" in arm:
        return bool(arm["buho_cabezon"])
    b = arm.data.bones.get("cabeza")
    H = float(arm.get("buho_altura", 0) or 0)
    if b is None or H <= 0:
        return False
    return b.length > 0.25 * H


def _giro_alrededor(q, eje):
    """Descompone q = swing @ twist, con twist el giro alrededor de `eje`."""
    v = Vector((q.x, q.y, q.z))
    p = eje * v.dot(eje)
    tw = Quaternion((q.w, p.x, p.y, p.z))
    if tw.magnitude < 1e-9:
        tw = Quaternion()
    else:
        tw.normalize()
    return q @ tw.inverted(), tw


class Estabilizador:
    """Amortigua la inclinación del tronco y nivela la cabeza en el retarget.

    fuerza 0 = como viene; 1 = tronco casi quieto y cabeza derecha. Por defecto 0,7 en cabezones.
    """

    def __init__(self, arm, cabeza="auto", fuerza=None):
        self.cabezon = es_cabezon(arm)
        self.activo = cabeza == "estable" or (cabeza in (None, "auto") and self.cabezon)
        f = 0.7 if fuerza is None else max(0.0, min(1.0, float(fuerza)))
        self.fuerza = f
        # Lo que se conserva de cada giro (1 = todo). Con 0,7: tronco al ~40 %, cabeza casi derecha (~10 %).
        self.k = {"cadera": 1 - 0.6 * f, "columna": 1 - 0.9 * f, "pecho": 1 - 0.9 * f, "cuello": 1 - 0.9 * f}
        self.k_cabeza = max(0.0, 1 - 1.3 * f)
        self.Z = Vector((0, 0, 1))

    def info(self):
        return {"cabeza": "estable" if self.activo else "libre", "cabezon": self.cabezon,
                "fuerza": round(self.fuerza, 2) if self.activo else 0}

    def ajustar(self, nombre, basis, P, rel, rest_cabeza):
        """basis: giro local del hueso; P @ rel: su orientación de reposo en el esqueleto ya posado."""
        if not self.activo:
            return basis
        ident = Quaternion()
        if nombre == "cadera":
            # Se conserva el giro a los lados (alrededor del eje del hueso, que apunta arriba) y se amortigua
            # la inclinación.
            sw, tw = _giro_alrededor(basis, Vector((0, 1, 0)))
            return ident.slerp(sw, self.k["cadera"]) @ tw
        if nombre in self.k:
            return ident.slerp(basis, self.k[nombre])
        if nombre == "cabeza":
            q_arm = P @ rel @ basis
            delta = q_arm @ rest_cabeza.inverted()
            sw, tw = _giro_alrededor(delta, self.Z)
            nuevo = ident.slerp(sw, self.k_cabeza) @ tw
            return (P @ rel).inverted() @ (nuevo @ rest_cabeza)
        return basis
