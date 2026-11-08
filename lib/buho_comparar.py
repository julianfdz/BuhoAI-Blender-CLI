"""buho_comparar: compara un render con una foto o imagen de referencia.

Genera una imagen lado a lado (referencia | render | superposición al 50 % | siluetas) con rótulos y devuelve
métricas sencillas para que un agente sepa si su modelo se parece a la referencia antes de enseñarlo:

- silueta: si la referencia tiene fondo uniforme (o transparencia), IoU directa (mismo encuadre) y de forma
  (cada silueta recortada a su caja y llevada al mismo tamaño), proporción de las cajas, desplazamiento y
  escala relativa;
- color medio por zonas (rejilla 3×3 sobre la caja de cada silueta, o sobre toda la imagen) y su diferencia.

Sin dependencias fuera de Blender: numpy y bpy.data.images (PNG, JPG, WEBP…). Los rótulos van con una fuente
de mapa de bits propia (en -b no hay forma fiable de escribir texto).
"""

import os

import bpy
import numpy as np

# ----------------------------------------------------------------------
# Fuente 5×7 (filas de arriba abajo, 5 bits)
# ----------------------------------------------------------------------

_F = {
    "A": "01110 10001 10001 11111 10001 10001 10001", "B": "11110 10001 10001 11110 10001 10001 11110",
    "C": "01110 10001 10000 10000 10000 10001 01110", "D": "11110 10001 10001 10001 10001 10001 11110",
    "E": "11111 10000 10000 11110 10000 10000 11111", "F": "11111 10000 10000 11110 10000 10000 10000",
    "G": "01110 10001 10000 10111 10001 10001 01111", "H": "10001 10001 10001 11111 10001 10001 10001",
    "I": "01110 00100 00100 00100 00100 00100 01110", "J": "00111 00010 00010 00010 00010 10010 01100",
    "K": "10001 10010 10100 11000 10100 10010 10001", "L": "10000 10000 10000 10000 10000 10000 11111",
    "M": "10001 11011 10101 10101 10001 10001 10001", "N": "10001 10001 11001 10101 10011 10001 10001",
    "O": "01110 10001 10001 10001 10001 10001 01110", "P": "11110 10001 10001 11110 10000 10000 10000",
    "Q": "01110 10001 10001 10001 10101 10010 01101", "R": "11110 10001 10001 11110 10100 10010 10001",
    "S": "01111 10000 10000 01110 00001 00001 11110", "T": "11111 00100 00100 00100 00100 00100 00100",
    "U": "10001 10001 10001 10001 10001 10001 01110", "V": "10001 10001 10001 10001 10001 01010 00100",
    "W": "10001 10001 10001 10101 10101 10101 01010", "X": "10001 10001 01010 00100 01010 10001 10001",
    "Y": "10001 10001 01010 00100 00100 00100 00100", "Z": "11111 00001 00010 00100 01000 10000 11111",
    "0": "01110 10001 10011 10101 11001 10001 01110", "1": "00100 01100 00100 00100 00100 00100 01110",
    "2": "01110 10001 00001 00010 00100 01000 11111", "3": "11111 00010 00100 00010 00001 10001 01110",
    "4": "00010 00110 01010 10010 11111 00010 00010", "5": "11111 10000 11110 00001 00001 10001 01110",
    "6": "00110 01000 10000 11110 10001 10001 01110", "7": "11111 00001 00010 00100 01000 01000 01000",
    "8": "01110 10001 10001 01110 10001 10001 01110", "9": "01110 10001 10001 01111 00001 00010 01100",
    " ": "00000 00000 00000 00000 00000 00000 00000", "%": "11000 11001 00010 00100 01000 10011 00011",
    ".": "00000 00000 00000 00000 00000 01100 01100", ",": "00000 00000 00000 00000 01100 00100 01000",
    ":": "00000 01100 01100 00000 01100 01100 00000", "-": "00000 00000 00000 11111 00000 00000 00000",
    "=": "00000 00000 11111 00000 11111 00000 00000", "/": "00001 00001 00010 00100 01000 10000 10000",
    "(": "00010 00100 01000 01000 01000 00100 00010", ")": "01000 00100 00010 00010 00010 00100 01000",
    "|": "00100 00100 00100 00100 00100 00100 00100", "+": "00000 00100 00100 11111 00100 00100 00000",
}
_TILDES = str.maketrans("ÁÉÍÓÚÜÑáéíóúüñ·", "AEIOUUNAEIOUUN|")


def _texto(img, x, y, texto, escala=2, color=(1, 1, 1)):
    """Escribe en img (alto×ancho×3, fila 0 arriba) con la esquina superior izquierda en (x, y)."""
    texto = str(texto).translate(_TILDES).upper()
    h, w = img.shape[:2]
    for ch in texto:
        g = _F.get(ch, _F[" "])
        for r, fila in enumerate(g.split()):
            for c, bit in enumerate(fila):
                if bit == "1":
                    y0, x0 = y + r * escala, x + c * escala
                    if 0 <= y0 < h and 0 <= x0 < w:
                        img[y0:y0 + escala, x0:x0 + escala, :3] = color
        x += 6 * escala
        if x >= w:
            break
    return x


# ----------------------------------------------------------------------
# Imágenes
# ----------------------------------------------------------------------

def cargar(ruta):
    """RGBA float 0..1, fila 0 arriba (en el espacio de color del archivo, sRGB normalmente)."""
    if not os.path.exists(ruta):
        raise RuntimeError("no existe " + ruta)
    im = bpy.data.images.load(ruta, check_existing=False)
    try:
        w, h = im.size
        if not w or not h:
            raise RuntimeError("Blender no puede leer la imagen " + ruta)
        a = np.empty(w * h * 4, dtype=np.float32)
        im.pixels.foreach_get(a)
    finally:
        bpy.data.images.remove(im)
    return a.reshape(h, w, 4)[::-1].copy()


def guardar(img, ruta):
    h, w = img.shape[:2]
    rgba = np.ones((h, w, 4), dtype=np.float32)
    rgba[..., :img.shape[2]] = img
    im = bpy.data.images.new("_buho_comparar", w, h, alpha=True)
    try:
        im.pixels.foreach_set(rgba[::-1].ravel())
        os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
        im.filepath_raw = ruta
        im.file_format = "PNG"
        im.save()
    finally:
        bpy.data.images.remove(im)


def redimensionar(a, w, h):
    """Bilineal (con un prefiltrado por cajas al reducir mucho)."""
    H, W = a.shape[:2]
    k = int(min(H / h, W / w))
    if k >= 2:
        H2, W2 = (H // k) * k, (W // k) * k
        a = a[:H2, :W2].reshape(H2 // k, k, W2 // k, k, -1).mean(axis=(1, 3)).astype(np.float32)
        H, W = a.shape[:2]
    ys = np.clip((np.arange(h) + 0.5) * H / h - 0.5, 0, H - 1)
    xs = np.clip((np.arange(w) + 0.5) * W / w - 0.5, 0, W - 1)
    y0 = np.floor(ys).astype(int)
    x0 = np.floor(xs).astype(int)
    y1, x1 = np.minimum(y0 + 1, H - 1), np.minimum(x0 + 1, W - 1)
    wy, wx = (ys - y0)[:, None, None], (xs - x0)[None, :, None]
    return ((1 - wy) * ((1 - wx) * a[y0][:, x0] + wx * a[y0][:, x1]) + wy * ((1 - wx) * a[y1][:, x0] + wx * a[y1][:, x1])).astype(np.float32)


def _sobre(img, fondo):
    al = img[..., 3:4]
    return img[..., :3] * al + np.asarray(fondo, dtype=np.float32) * (1 - al)


# ----------------------------------------------------------------------
# Siluetas
# ----------------------------------------------------------------------

def silueta(img, umbral=0.12):
    """Máscara del objeto: por transparencia, o por diferencia con un fondo uniforme (el color del borde).
    Devuelve (máscara bool o None, color de fondo o None, cómo)."""
    al = img[..., 3]
    if (al < 0.5).mean() > 0.02:
        return al > 0.5, None, "transparencia"
    h, w = al.shape
    b = max(2, int(0.02 * min(h, w)))
    borde = np.concatenate([img[:b, :, :3].reshape(-1, 3), img[-b:, :, :3].reshape(-1, 3),
                            img[:, :b, :3].reshape(-1, 3), img[:, -b:, :3].reshape(-1, 3)])
    fondo = np.median(borde, axis=0)
    dist_borde = np.linalg.norm(borde - fondo, axis=1)
    if np.percentile(dist_borde, 90) > umbral:
        return None, None, "el fondo no es uniforme"
    m = np.linalg.norm(img[..., :3] - fondo, axis=2) > umbral
    if m.mean() < 0.001:
        return None, fondo, "no se distingue nada del fondo"
    return m, fondo, "fondo uniforme"


def _caja(m):
    ys, xs = np.nonzero(m)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def _iou(a, b):
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0


def _hex(c):
    c = np.clip(np.asarray(c) * 255 + 0.5, 0, 255).astype(int)
    return "#%02X%02X%02X" % tuple(c[:3])


def _zonas(img, m, n=3):
    """Color medio en una rejilla n×n sobre la caja de la máscara (o sobre toda la imagen)."""
    h, w = img.shape[:2]
    x0, y0, x1, y1 = _caja(m) if m is not None else (0, 0, w, h)
    out = []
    for i in range(n):
        fila = []
        for j in range(n):
            ya, yb = y0 + (y1 - y0) * i // n, y0 + (y1 - y0) * (i + 1) // n
            xa, xb = x0 + (x1 - x0) * j // n, x0 + (x1 - x0) * (j + 1) // n
            px = img[ya:yb, xa:xb, :3]
            if m is not None:
                px = px[m[ya:yb, xa:xb]]
            else:
                px = px.reshape(-1, 3)
            fila.append(px.mean(axis=0) if len(px) else None)
        out.append(fila)
    return out


def comparar(referencia, render, salida, alto=None, umbral=0.12, rotulo_render="RENDER"):
    """Junta referencia y render (mismo alto) en una imagen con rótulos y mide su parecido."""
    ref = cargar(referencia)
    ren = cargar(render)
    h, w = ren.shape[:2]
    if alto and alto != h:
        w, h = int(round(w * alto / h)), int(alto)
        ren = redimensionar(ren, w, h)
    ref = redimensionar(ref, w, h)

    m_ref, fondo_ref, como_ref = silueta(ref, umbral)
    m_ren, fondo_ren, como_ren = silueta(ren, umbral)
    fondo_vista = fondo_ref if fondo_ref is not None else np.array([0.5, 0.5, 0.5], dtype=np.float32)
    ref3 = _sobre(ref, fondo_vista)
    ren3 = _sobre(ren, fondo_vista)
    mezcla = 0.5 * ref3 + 0.5 * ren3

    paneles = [("REFERENCIA", ref3), (rotulo_render, ren3), ("SUPERPOSICION 50%", mezcla)]
    met = {"tamano_px": [w, h], "silueta_referencia": como_ref, "silueta_render": como_ren}
    if m_ref is not None and m_ren is not None:
        sil = np.zeros((h, w, 3), dtype=np.float32) + 0.12
        solo_ref = m_ref & ~m_ren
        solo_ren = m_ren & ~m_ref
        sil[m_ref & m_ren] = (0.92, 0.92, 0.92)
        sil[solo_ref] = (0.9, 0.2, 0.2)
        sil[solo_ren] = (0.2, 0.55, 1.0)
        paneles.append(("SILUETAS: ROJO REF, AZUL RENDER", sil))
        ca, cb = _caja(m_ref), _caja(m_ren)
        # Forma sin contar encuadre: cada silueta recortada a su caja y llevada a 128×128.
        fa = redimensionar(m_ref[ca[1]:ca[3], ca[0]:ca[2], None].astype(np.float32), 128, 128)[..., 0] > 0.5
        fb = redimensionar(m_ren[cb[1]:cb[3], cb[0]:cb[2], None].astype(np.float32), 128, 128)[..., 0] > 0.5
        prop_a = (ca[2] - ca[0]) / max(ca[3] - ca[1], 1)
        prop_b = (cb[2] - cb[0]) / max(cb[3] - cb[1], 1)
        cen_a = np.argwhere(m_ref).mean(axis=0)
        cen_b = np.argwhere(m_ren).mean(axis=0)
        met["silueta"] = {
            "iou": round(_iou(m_ref, m_ren), 3),
            "iou_forma": round(_iou(fa, fb), 3),
            "proporcion_ancho_alto": {"referencia": round(prop_a, 3), "render": round(prop_b, 3)},
            "escala_render_vs_ref": round((cb[3] - cb[1]) / max(ca[3] - ca[1], 1), 3),
            "desplazamiento_pct": {"x": round(float(cen_b[1] - cen_a[1]) / w * 100, 1),
                                   "y_abajo": round(float(cen_b[0] - cen_a[0]) / h * 100, 1)},
            "ocupa_pct": {"referencia": round(float(m_ref.mean()) * 100, 1), "render": round(float(m_ren.mean()) * 100, 1)},
            "solo_referencia_pct": round(float(solo_ref.mean()) * 100, 2),
            "solo_render_pct": round(float(solo_ren.mean()) * 100, 2),
        }
    # Colores por zonas (sobre cada silueta si las hay; si no, sobre la imagen entera)
    za = _zonas(ref3, m_ref if (m_ref is not None and m_ren is not None) else None)
    zb = _zonas(ren3, m_ren if (m_ref is not None and m_ren is not None) else None)
    zonas, difs = [], []
    nombres = (("arriba_izq", "arriba", "arriba_der"), ("izq", "centro", "der"), ("abajo_izq", "abajo", "abajo_der"))
    for i in range(3):
        for j in range(3):
            a, b = za[i][j], zb[i][j]
            if a is None or b is None:
                continue
            d = float(np.linalg.norm((a - b) * 255))
            difs.append(d)
            zonas.append({"zona": nombres[i][j], "referencia": _hex(a), "render": _hex(b), "diferencia": round(d, 1)})
    met["color_por_zonas"] = {"sobre": "siluetas" if (m_ref is not None and m_ren is not None) else "imagen entera",
                              "diferencia_media": round(float(np.mean(difs)), 1) if difs else None,
                              "nota": "diferencia = distancia RGB 0-255; < 25 parecido, > 60 muy distinto",
                              "zonas": zonas}
    if m_ref is not None and m_ren is not None:
        met["color_medio"] = {"referencia": _hex(ref3[m_ref].mean(axis=0)), "render": _hex(ren3[m_ren].mean(axis=0))}

    # Composición: paneles con una barra de rótulo encima y una línea de métricas debajo.
    hueco, barra, pie = 8, 30, 30
    n = len(paneles)
    W = n * w + (n + 1) * hueco
    Ht = barra + h + pie + 2 * hueco
    hoja = np.zeros((Ht, W, 3), dtype=np.float32) + 0.1
    for k, (titulo, p) in enumerate(paneles):
        x = hueco + k * (w + hueco)
        hoja[hueco + barra:hueco + barra + h, x:x + w] = p
        _texto(hoja, x + 4, hueco + 8, titulo, 2, (0.95, 0.95, 0.95))
    linea = []
    if "silueta" in met:
        s = met["silueta"]
        linea.append("IOU %.2f  FORMA %.2f  ESCALA %.2f" % (s["iou"], s["iou_forma"], s["escala_render_vs_ref"]))
    else:
        linea.append("SIN SILUETA (%s)" % como_ref)
    if met["color_por_zonas"]["diferencia_media"] is not None:
        linea.append("COLOR DIF. %.0f" % met["color_por_zonas"]["diferencia_media"])
    _texto(hoja, hueco + 4, hueco + barra + h + 8, "   ".join(linea), 2, (0.0, 0.76, 0.72))
    guardar(hoja, salida)
    met["comparacion"] = salida
    met["paneles"] = [t for t, _ in paneles]
    return met
