"""buho_sdf: escultura por campos de distancia (SDF) para buho-blender.

Las formas se combinan como arcilla (uniones suaves, restas, desplazamientos) y salen como UNA malla orgánica
(numpy evalúa el campo en una rejilla -> openvdb -> «Volume to Mesh» de Blender).

    import buho_sdf as S
    cuerpo = S.elipsoide((0, 0, 1), (0.5, 0.4, 0.8))
    cabeza = S.caja((0, 0, 1.7), (0.35, 0.3, 0.27), redondeo=0.1)
    figura = cuerpo.suave(cabeza, 0.12) - S.esfera((0.2, -0.4, 1.7), 0.1)
    o = S.malla(figura, "Busto", voxel=0.005, mascaras={"cabeza": cabeza})

Todas las funciones de distancia reciben P con forma (N, 3) y devuelven (N,). Medidas en metros.
Las máscaras se guardan como atributos de vértice (0..1) para usarlas en los materiales (nodo Attribute).
"""

import math
import os
import tempfile
import time

import numpy as np

# ======================================================================
# Núcleo
# ======================================================================


class SDF:
    def __init__(self, f, nombre=""):
        self.f = f
        self.nombre = nombre

    def __call__(self, P):
        return self.f(P)

    # --- combinaciones ---
    def __or__(self, o):
        return SDF(lambda P: np.minimum(self(P), o(P)))

    def __sub__(self, o):
        return SDF(lambda P: np.maximum(self(P), -o(P)))

    def __and__(self, o):
        return SDF(lambda P: np.maximum(self(P), o(P)))

    def suave(self, o, k=0.05):
        """Unión suave (como fundir arcilla); k = radio de la transición en metros."""
        return SDF(lambda P: smin(self(P), o(P), k))

    def restar_suave(self, o, k=0.02):
        return SDF(lambda P: -smin(-self(P), o(P), k))

    def cortar_suave(self, o, k=0.02):
        """Intersección suave."""
        return SDF(lambda P: -smin(-self(P), -o(P), k))

    # --- transformaciones del espacio ---
    def mover(self, v):
        v = np.asarray(v, dtype=np.float32)
        return SDF(lambda P: self(P - v))

    def girar(self, grados, eje="Z", centro=(0, 0, 0)):
        R = _rot(eje, -math.radians(grados))
        c = np.asarray(centro, dtype=np.float32)
        return SDF(lambda P: self((P - c) @ R.T + c))

    def escalar(self, s):
        return SDF(lambda P: self(P / s) * s)

    def simetria_x(self):
        """Refleja: lo modelado en x>0 aparece también en x<0."""
        def f(P):
            Q = P.copy()
            Q[:, 0] = np.abs(Q[:, 0])
            return self(Q)
        return SDF(f)

    def redondear(self, r):
        return SDF(lambda P: self(P) - r)

    def cascara(self, grosor):
        return SDF(lambda P: np.abs(self(P)) - grosor / 2)

    def desplazar(self, func):
        """func(P) -> desplazamiento hacia fuera (m). Útil para plumas, escamas, ruido."""
        return SDF(lambda P: self(P) - func(P))

    def torcer(self, vueltas_por_metro, eje="Z"):
        def f(P):
            a = P[:, 2] * vueltas_por_metro * 2 * math.pi
            c, s = np.cos(a), np.sin(a)
            Q = P.copy()
            Q[:, 0] = c * P[:, 0] - s * P[:, 1]
            Q[:, 1] = s * P[:, 0] + c * P[:, 1]
            return self(Q)
        return SDF(f)


def smin(a, b, k):
    if k <= 0:
        return np.minimum(a, b)
    h = np.clip(0.5 + 0.5 * (b - a) / k, 0.0, 1.0)
    return b + (a - b) * h - k * h * (1.0 - h)


def _rot(eje, a):
    c, s = math.cos(a), math.sin(a)
    if eje == "X":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], dtype=np.float32)
    if eje == "Y":
        return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], dtype=np.float32)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], dtype=np.float32)


def _v(x):
    return np.asarray(x, dtype=np.float32)


def _len(P):
    return np.sqrt(np.einsum("ij,ij->i", P, P))


# ======================================================================
# Primitivas
# ======================================================================

def esfera(c, r):
    c = _v(c)
    return SDF(lambda P: _len(P - c) - r)


def elipsoide(c, radios):
    c, r = _v(c), _v(radios)

    def f(P):
        Q = P - c
        k0 = _len(Q / r)
        k1 = _len(Q / (r * r))
        return k0 * (k0 - 1.0) / np.maximum(k1, 1e-9)
    return SDF(f)


def caja(c, mitades, redondeo=0.0):
    c, b = _v(c), _v(mitades) - redondeo

    def f(P):
        q = np.abs(P - c) - b
        fuera = _len(np.maximum(q, 0.0))
        dentro = np.minimum(np.max(q, axis=1), 0.0)
        return fuera + dentro - redondeo
    return SDF(f)


def capsula(a, b, r1, r2=None):
    """Segmento a-b con radio r1 en a y r2 en b (cono redondeado si r2 != r1)."""
    a, b = _v(a), _v(b)
    r2 = r1 if r2 is None else r2
    ba = b - a
    l2 = float(ba @ ba)

    def f(P):
        pa = P - a
        h = np.clip((pa @ ba) / l2, 0.0, 1.0)
        return _len(pa - np.outer(h, ba)) - (r1 + (r2 - r1) * h)
    return SDF(f)


def tubo(puntos, radios):
    """Cadena de cápsulas (curva gruesa) que pasa por los puntos con radio por punto."""
    if np.isscalar(radios):
        radios = [radios] * len(puntos)
    piezas = [capsula(puntos[i], puntos[i + 1], radios[i], radios[i + 1]) for i in range(len(puntos) - 1)]
    s = piezas[0]
    for p in piezas[1:]:
        s = s | p
    return s


def toro(c, R, r, eje="Z"):
    c = _v(c)
    idx = {"Z": (0, 1, 2), "Y": (0, 2, 1), "X": (1, 2, 0)}[eje]

    def f(P):
        Q = P - c
        q = np.stack([np.hypot(Q[:, idx[0]], Q[:, idx[1]]) - R, Q[:, idx[2]]], axis=1)
        return _len(q) - r
    return SDF(f)


def cilindro(c, r, media_altura, redondeo=0.0):
    c = _v(c)

    def f(P):
        Q = P - c
        d = np.stack([np.hypot(Q[:, 0], Q[:, 1]) - r + redondeo, np.abs(Q[:, 2]) - media_altura + redondeo], axis=1)
        return np.minimum(np.max(d, axis=1), 0.0) + _len(np.maximum(d, 0.0)) - redondeo
    return SDF(f)


def plano(punto, normal):
    p, n = _v(punto), _v(normal) / np.linalg.norm(normal)
    return SDF(lambda P: (P - p) @ n)


def revolucion(perfil, c=(0, 0, 0)):
    """Sólido de revolución alrededor de Z a partir de [(radio, altura), ...] (polilínea cerrada contra el eje)."""
    c = _v(c)
    pts = np.asarray([(0.0, perfil[0][1])] + list(perfil) + [(0.0, perfil[-1][1])], dtype=np.float32)

    def f(P):
        Q = P - c
        q = np.stack([np.hypot(Q[:, 0], Q[:, 1]), Q[:, 2]], axis=1)
        d = np.full(len(q), 1e9, dtype=np.float32)
        signo = np.ones(len(q), dtype=np.float32)
        for i in range(len(pts)):
            a, b = pts[i - 1], pts[i]
            e = b - a
            w = q - a
            t = np.clip((w @ e) / max(float(e @ e), 1e-12), 0, 1)
            d = np.minimum(d, _len(w - np.outer(t, e)))
            c1 = q[:, 1] >= a[1]
            c2 = q[:, 1] < b[1]
            c3 = e[0] * w[:, 1] > e[1] * w[:, 0]
            cambia = (c1 & c2 & c3) | (~c1 & ~c2 & ~c3)
            signo[cambia] *= -1
        return d * signo
    return SDF(f)


# ======================================================================
# Ruido y patrones (para desplazar)
# ======================================================================

def _hash3(i, j, k):
    h = (i * 73856093) ^ (j * 19349663) ^ (k * 83492791)
    h = (h ^ (h >> 13)) * 1274126177
    return ((h & 0xFFFF) / 65535.0).astype(np.float32)


def ruido(P, escala=10.0, semilla=0):
    """Ruido de valor suave en [-1, 1]."""
    X = P * escala + semilla * 17.13
    i = np.floor(X).astype(np.int64)
    f = X - i
    u = f * f * (3 - 2 * f)
    acc = 0.0
    for dx in (0, 1):
        for dy in (0, 1):
            for dz in (0, 1):
                w = (u[:, 0] if dx else 1 - u[:, 0]) * (u[:, 1] if dy else 1 - u[:, 1]) * (u[:, 2] if dz else 1 - u[:, 2])
                acc = acc + w * _hash3(i[:, 0] + dx, i[:, 1] + dy, i[:, 2] + dz)
    return acc * 2 - 1


def fbm(P, escala=4.0, octavas=4, semilla=0):
    a, s, total = 0.5, escala, 0.0
    for o in range(octavas):
        total = total + a * ruido(P, s, semilla + o)
        a *= 0.5
        s *= 2.03
    return total


def plumas(P, eje_u, eje_v, ancho=0.07, largo=0.1, filas=0.065, tip_abajo=True):
    """Altura 0..1 de un patrón de plumas/escamas superpuestas en filas al tresbolillo.

    eje_u: función P -> coordenada a lo ancho (m). eje_v: función P -> coordenada a lo alto (m, crece hacia arriba).
    Cada pluma: base arriba, punta redondeada abajo; la fila de arriba monta sobre la de abajo.
    """
    u, v = eje_u(P), eje_v(P)
    r = np.floor(v / filas)
    h = np.zeros(len(P), dtype=np.float32)
    for dr in (0, 1, 2):
        fila = r + dr                      # filas cuya punta está por debajo o en el punto
        v_punta = (fila - 1) * filas
        desfase = np.where(np.mod(fila, 2) == 0, 0.0, 0.5 * ancho)
        for dc in (-1, 0, 1):
            cu = (np.floor((u - desfase) / ancho) + dc) * ancho + desfase + ancho / 2
            a = (u - cu) / (ancho / 2)
            t = (v - v_punta) / largo      # 0 en la punta, 1 en la base
            borde = 1 - a * a - np.clip(1 - t / 0.35, 0, 1) ** 2   # punta redondeada
            dentro = (t >= 0) & (t <= 1) & (borde > 0)
            alto = np.where(dentro, (1 - t) ** 0.7 * np.sqrt(np.clip(borde, 0, 1)), 0.0)
            # la pluma de más abajo (dr menor) queda debajo: gana la más alta en ese punto
            h = np.maximum(h, alto.astype(np.float32))
    return h


def mascara(sdf, P, ancho=0.03):
    """1 dentro de la forma, 0 a 'ancho' metros fuera, suave."""
    d = sdf(P)
    return np.clip(1 - d / ancho, 0, 1).astype(np.float32)


# ======================================================================
# Malla
# ======================================================================

def malla(sdf, nombre, limites=None, voxel=0.005, mascaras=None, material=None, col=None, suave=True, banda=4,
          trozos=24, adaptividad=0.0):
    """Convierte el campo en una malla de Blender. limites: ((xmin,ymin,zmin),(xmax,ymax,zmax)); si falta, se estima."""
    import bpy
    import openvdb as vdb

    t0 = time.time()
    if limites is None:
        limites = estimar_limites(sdf)
    lo, hi = _v(limites[0]) - voxel * 3, _v(limites[1]) + voxel * 3
    n = np.ceil((hi - lo) / voxel).astype(int) + 1
    xs = lo[0] + np.arange(n[0], dtype=np.float32) * voxel
    ys = lo[1] + np.arange(n[1], dtype=np.float32) * voxel
    zs = lo[2] + np.arange(n[2], dtype=np.float32) * voxel
    dens = np.empty((n[0], n[1], n[2]), dtype=np.float32)
    lim = banda * voxel
    paso = max(1, n[2] // trozos)
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    for k0 in range(0, n[2], paso):
        k1 = min(n[2], k0 + paso)
        Z = zs[k0:k1]
        P = np.stack([np.repeat(X[:, :, None], len(Z), 2).ravel(), np.repeat(Y[:, :, None], len(Z), 2).ravel(),
                      np.broadcast_to(Z, (n[0], n[1], len(Z))).ravel()], axis=1).astype(np.float32)
        d = sdf(P).reshape(n[0], n[1], len(Z))
        dens[:, :, k0:k1] = np.clip(-d, -lim, lim)
    t1 = time.time()

    g = vdb.FloatGrid(-lim)
    g.copyFromArray(dens, ijk=(0, 0, 0), tolerance=0.0)
    g.name = "density"
    g.transform = vdb.createLinearTransform(matrix=[[voxel, 0, 0, 0], [0, voxel, 0, 0], [0, 0, voxel, 0],
                                                   [float(lo[0]), float(lo[1]), float(lo[2]), 1]])
    ruta = os.path.join(tempfile.gettempdir(), f"buho_sdf_{nombre}_{os.getpid()}.vdb")
    vdb.write(ruta, grids=[g])
    del dens

    vol_data = bpy.data.volumes.new("_sdf_" + nombre)
    vol_data.filepath = ruta
    vol = bpy.data.objects.new("_sdf_" + nombre, vol_data)
    bpy.context.scene.collection.objects.link(vol)
    tmp_me = bpy.data.meshes.new("_sdf_tmp")
    tmp = bpy.data.objects.new("_sdf_tmp", tmp_me)
    bpy.context.scene.collection.objects.link(tmp)
    md = tmp.modifiers.new("v2m", "VOLUME_TO_MESH")
    md.object = vol
    md.threshold = 0.0
    md.grid_name = "density"
    md.resolution_mode = "GRID"
    md.adaptivity = adaptividad
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(tmp.evaluated_get(dg))
    me.name = nombre
    bpy.data.objects.remove(tmp, do_unlink=True)
    bpy.data.meshes.remove(tmp_me)
    bpy.data.objects.remove(vol, do_unlink=True)
    bpy.data.volumes.remove(vol_data)
    try:
        os.remove(ruta)
    except OSError:
        pass

    if suave:
        me.shade_smooth()
    V = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", V)
    V = V.reshape(-1, 3)
    for clave, forma in (mascaras or {}).items():
        valores = forma(V) if not isinstance(forma, SDF) else mascara(forma, V)
        at = me.attributes.new(clave, "FLOAT", "POINT")
        at.data.foreach_set("value", np.asarray(valores, dtype=np.float32))
    if material is not None:
        me.materials.append(material)
    o = bpy.data.objects.new(nombre, me)
    (col or bpy.context.scene.collection).objects.link(o)
    o["sdf_info"] = f"voxel {voxel} m, rejilla {tuple(int(x) for x in n)}, campo {t1 - t0:.1f}s, total {time.time() - t0:.1f}s"
    return o


def estimar_limites(sdf, desde=(-3, -3, -3), hasta=(3, 3, 3), paso=0.04):
    xs = np.arange(desde[0], hasta[0], paso, dtype=np.float32)
    ys = np.arange(desde[1], hasta[1], paso, dtype=np.float32)
    zs = np.arange(desde[2], hasta[2], paso, dtype=np.float32)
    X, Y, Z = np.meshgrid(xs, ys, zs, indexing="ij")
    P = np.stack([X.ravel(), Y.ravel(), Z.ravel()], axis=1)
    d = sdf(P)
    dentro = P[d < paso]
    if len(dentro) == 0:
        raise ValueError("la forma está vacía o fuera de ±3 m")
    return dentro.min(axis=0) - paso, dentro.max(axis=0) + paso
