# Búho esculpido (v3): una sola pieza fundida con buho_sdf, plumas y escamas talladas en la superficie,
# metal negro azulado con oro en los relieves (Cycles: pointiness) y ojos ámbar. Diseño propio.
#   buho-blender run buho_esculpido.py --fondo --guardar buho_esculpido.blend
#   VOXEL = 0.008 para tanteos rápidos, 0.004 para el final.

import math

import numpy as np
import bpy

import buho3d as b3
import buho_sdf as S

VOXEL = globals().get("VOXEL", 0.005)
b3.quitar_por_defecto()
col = b3.coleccion("BuhoEsculpido")
for viejo in ("BuhoEstatua",):
    c = bpy.data.collections.get(viejo)
    if c:
        c.hide_render = True
        c.hide_viewport = True

# ======================================================================
# Forma
# ======================================================================
# Cuerpo: dos elipsoides fundidos (base ancha, pecho más estrecho) con el pecho algo plano.
cuerpo = S.elipsoide((0, 0, 0.62), (0.56, 0.46, 0.64)).suave(S.elipsoide((0, 0.02, 1.2), (0.45, 0.37, 0.52)), 0.2)
cuerpo = cuerpo.cortar_suave(S.plano((0, -0.4, 0), (0, -1, 0)), 0.1)

# Cabeza: bloque redondeado, orejas en las esquinas de arriba, coronilla hundida.
cabeza = S.caja((0, 0.0, 1.74), (0.37, 0.3, 0.27), redondeo=0.09)
for s in (-1, 1):
    cabeza = cabeza.suave(S.capsula((s * 0.25, 0.0, 1.9), (s * 0.34, 0.02, 2.05), 0.075, 0.025), 0.06)
cabeza = cabeza.restar_suave(S.elipsoide((0, -0.02, 2.09), (0.17, 0.4, 0.09)), 0.05)

# Discos faciales: cuencos con borde levantado alrededor de los ojos.
discos = None
for s in (-1, 1):
    d = S.elipsoide((s * 0.175, -0.25, 1.72), (0.18, 0.085, 0.17))
    discos = d if discos is None else discos | d
huecos = S.esfera((0.175, -0.395, 1.72), 0.15).simetria_x()
cara = cabeza.suave(discos, 0.035).restar_suave(huecos, 0.02)

# Cejas en V (bajan hacia el pico) y pico ganchudo.
cejas = None
for s in (-1, 1):
    c_ = S.tubo([(0, -0.335, 1.79), (s * 0.15, -0.335, 1.895), (s * 0.33, -0.265, 1.935)], [0.03, 0.028, 0.016])
    cejas = c_ if cejas is None else cejas | c_
pico = S.tubo([(0, -0.335, 1.8), (0, -0.385, 1.71), (0, -0.37, 1.615)], [0.05, 0.036, 0.008])
cara = cara.suave(cejas, 0.025).suave(pico, 0.02)

# Alas plegadas a los lados, algo inclinadas hacia dentro arriba.
alas = None
for s in (-1, 1):
    a = S.elipsoide((s * 0.41, 0.03, 1.0), (0.17, 0.4, 0.77)).girar(s * 6, "Y", centro=(s * 0.41, 0.03, 1.0))
    alas = a if alas is None else alas | a

# Anillo dorado grabado y peana.
_tor = S.toro((0, 0, 0.42), 0.565, 0.045)
anillo = S.SDF(lambda P: _tor(P * np.array([1, 1 / 0.84, 1], dtype=np.float32)))
peana = S.cilindro((0, 0, 0.07), 0.62, 0.07, redondeo=0.025)

figura = cuerpo.suave(cara, 0.11).suave(alas, 0.06)
figura = figura.cortar_suave(S.plano((0, 0, 0.13), (0, 0, -1)), 0.03) | anillo | peana


# ======================================================================
# Relieve: plumas de las alas, escamas del pecho, plumas radiales de los discos, grabado del anillo
# ======================================================================
def relieve(P):
    h = np.zeros(len(P), dtype=np.float32)
    # alas: plumas grandes en filas, siguiendo el contorno del ala
    for s in (-1, 1):
        m = S.mascara(S.elipsoide((s * 0.41, 0.03, 1.0), (0.2, 0.43, 0.8)), P, 0.03)
        sel = m > 0
        if sel.any():
            Q = P[sel]
            ang = np.arctan2(Q[:, 1] - 0.03, s * (Q[:, 0] - s * 0.41))
            hp = S.plumas(Q, lambda X, ang=ang: ang * 0.3, lambda X: X[:, 2], ancho=0.075, largo=0.11, filas=0.07)
            h[sel] += 0.013 * hp * m[sel]
    # pecho y vientre: escamas pequeñas
    m = S.mascara(S.SDF(lambda X: np.maximum(cuerpo(X), np.maximum(X[:, 1] + 0.12, np.maximum(X[:, 2] - 1.5, 0.46 - X[:, 2])))), P, 0.02)
    m *= 1 - S.mascara(alas, P, 0.03)
    sel = m > 0
    if sel.any():
        Q = P[sel]
        h[sel] += 0.0055 * S.plumas(Q, lambda X: X[:, 0], lambda X: X[:, 2], ancho=0.048, largo=0.06, filas=0.038) * m[sel]
    # discos: plumitas radiales
    for s in (-1, 1):
        c = np.array([s * 0.175, -0.3, 1.72], dtype=np.float32)
        Q = P - c
        r = np.hypot(Q[:, 0], Q[:, 2])
        anillo_disco = np.clip(1 - np.abs(r - 0.135) / 0.06, 0, 1) * (np.abs(Q[:, 1]) < 0.12)
        ang = np.arctan2(Q[:, 2], Q[:, 0])
        h += 0.005 * anillo_disco * (0.5 + 0.5 * np.cos(ang * 36 + r * 40)) ** 2
    # anillo: grabado de rombos
    m = S.mascara(anillo, P, 0.01)
    ang = np.arctan2(P[:, 1], P[:, 0])
    h += 0.004 * m * ((np.sin(ang * 70) * np.sin((P[:, 2] - 0.42) * 140)) > 0)
    # fundición: ruido fino por todo
    h += 0.0018 * S.fbm(P, 9.0, 3)
    return h


figura = figura.desplazar(relieve)

mascaras = {
    "ala": alas,
    "disco": discos,
    "anillo": anillo,
    "peana": peana,
    "oro": cejas | pico,
    "coronilla": S.SDF(lambda X: 1.99 - X[:, 2]),
}
buho = S.malla(figura, "Buho", limites=((-0.72, -0.5, -0.01), (0.72, 0.55, 2.12)), voxel=VOXEL, mascaras=mascaras, col=col)


# ======================================================================
# Material (Cycles): oro en lo que sobresale (pointiness) y en las máscaras
# ======================================================================
def mat_bronce():
    m, n, b = b3.material_nodos("BX_Bronce")
    geo = n.nuevo("ShaderNodeNewGeometry")
    attr = {k: n.nuevo("ShaderNodeAttribute", attribute_name=k).outputs["Fac"] for k in mascaras}
    tc = n.nuevo("ShaderNodeTexCoord")
    # oro en aristas: pointiness alto
    filo = n.rampa(geo.outputs["Pointiness"], [(0.56, "#000000"), (0.64, "#FFFFFF")])
    ruido = n.nuevo("ShaderNodeTexNoise", Scale=3.0, Detail=10.0, Roughness=0.7)
    n.unir(tc, "Object", ruido, "Vector")
    manchas = n.rampa(ruido.outputs["Fac"], [(0.58, "#000000"), (0.68, "#FFFFFF")])
    coron = n.mate("MULTIPLY", attr["coronilla"], n.rampa(ruido.outputs["Fac"], [(0.5, "#000000"), (0.62, "#FFFFFF")]))
    oro = n.mate("MAXIMUM", n.mate("MULTIPLY", filo, n.mate("ADD", n.mate("MULTIPLY", manchas, 0.6), 0.35)), coron)
    oro = n.mate("MAXIMUM", oro, n.mate("MULTIPLY", attr["oro"], 0.85))
    oro = n.mate("MAXIMUM", oro, n.mate("MULTIPLY", attr["anillo"], 0.75))
    oro = n.mate("MINIMUM", oro, 1.0)
    # base: negro con vetas azul intenso (más azul en alas)
    vetas = n.nuevo("ShaderNodeTexNoise", Scale=1.6, Detail=5.0)
    n.unir(tc, "Object", vetas, "Vector")
    azul = n.mate("ADD", n.rampa(vetas.outputs["Fac"], [(0.38, "#000000"), (0.72, "#FFFFFF")]), n.mate("MULTIPLY", attr["ala"], 0.35))
    base = n.mezcla(n.mate("MINIMUM", azul, 1.0), "#04060C", "#1543B0")
    peana_col = n.mezcla(attr["peana"], base, "#0B0D12")
    color = n.mezcla(oro, peana_col, "#C29A52")
    n.nt.links.new(color, b.inputs["Base Color"])
    rug = n.mate("ADD", 0.26, n.mate("MULTIPLY", oro, 0.12))
    n.nt.links.new(rug, b.inputs["Roughness"])
    b.inputs["Metallic"].default_value = 0.92
    b.inputs["Coat Weight"].default_value = 0.35
    b.inputs["Coat Roughness"].default_value = 0.15
    # microescamas en lo que no es ala
    vor = n.nuevo("ShaderNodeTexVoronoi", feature="DISTANCE_TO_EDGE", Scale=160.0)
    n.unir(tc, "Object", vor, "Vector")
    micro = n.rampa(vor.outputs["Distance"], [(0.0, "#000000"), (0.08, "#FFFFFF")])
    fuerza = n.mate("MULTIPLY", n.mate("SUBTRACT", 1.0, attr["ala"]), 0.18)
    bump = n.nuevo("ShaderNodeBump", Distance=0.002)
    n.nt.links.new(micro, bump.inputs["Height"])
    n.nt.links.new(fuerza, bump.inputs["Strength"])
    n.unir(bump, "Normal", b, "Normal")
    return m


def mat_iris():
    m, n, b = b3.material_nodos("BX_Iris")
    tc = n.nuevo("ShaderNodeTexCoord")
    sep = n.nuevo("ShaderNodeSeparateXYZ")
    n.unir(tc, "Object", sep, "Vector")
    r = n.mate("DIVIDE", n.mate("SQRT", n.mate("ADD", n.mate("POWER", sep.outputs["X"], 2.0), n.mate("POWER", sep.outputs["Z"], 2.0))), 0.095)
    estrias = n.nuevo("ShaderNodeTexNoise", Scale=60.0, Detail=2.0)
    n.unir(tc, "Object", estrias, "Vector")
    ci = n.rampa(r, [(0.0, "#FFF0A0"), (0.5, "#FFC81A"), (0.8, "#F08A00"), (0.97, "#3A1600")])
    ci = n.mezcla(n.mate("MULTIPLY", estrias.outputs["Fac"], 0.4), ci, "#C05A00", "MULTIPLY")
    n.nt.links.new(ci, b.inputs["Base Color"])
    n.nt.links.new(ci, b.inputs["Emission Color"])
    b.inputs["Emission Strength"].default_value = 3.0
    b.inputs["Roughness"].default_value = 0.3
    b.inputs["Coat Weight"].default_value = 1.0
    b.inputs["Coat Roughness"].default_value = 0.0
    return m


buho.data.materials.append(mat_bronce())
IRIS = mat_iris()
PUPILA = b3.pbr("BX_Pupila", "#010101", rugosidad=0.04, capa=1.0)
for s in (-1, 1):
    lado = "D" if s > 0 else "I"
    b3.esfera(f"Iris_{lado}", 0.088, IRIS, col=col, loc=(s * 0.175, -0.262, 1.72))
    b3.objeto(f"Pupila_{lado}", b3.deformar(b3.bm_esfera(0.05, 32, 16), lambda p: (p.x, p.y * 0.4, p.z)), PUPILA,
              col=col, loc=(s * 0.175, -0.345, 1.72))

# ======================================================================
# Escena: humo azul, contraluces, cámara en contrapicado, Cycles
# ======================================================================
b3._actual["col"] = col
b3.niebla("BX_Humo", (9, 2.5, 6), (0, 2.6, 1.6), "#2B4A8C", densidad=0.25, escala=1.1, contraste=(0.52, 0.8))
clave = b3.luz("BX_Clave", "SPOT", 1600, (-1.6, -2.4, 3.4), (0, 0, 1.6), "#FFE0B8", 0.25)
clave.data.spot_size = 0.9
clave.data.spot_blend = 0.6
b3.luz("BX_ContraI", "AREA", 700, (-2.0, 1.4, 2.4), (0, 0, 1.3), "#5C7CFF", 1.6)
b3.luz("BX_ContraD", "AREA", 700, (2.0, 1.4, 2.4), (0, 0, 1.3), "#5C7CFF", 1.6)
b3.luz("BX_Halo", "POINT", 500, (0, 1.6, 1.6), (0, 0, 1.6), "#4F78D8", 0.6)
b3.luz("BX_Ojos", "POINT", 8, (0, -0.75, 1.75), (0, 0, 1.7), "#FFB040", 0.1)
b3.camara("BX_Cam", (0, -3.7, 0.45), (0, 0, 1.36), lente=40)
b3.mundo("#010204")
b3.render("CYCLES", 1920, 1080, muestras=128)
b3.resplandor(0.5, 1.2, 7)

result = {"vertices": len(buho.data.vertices), "info": buho["sdf_info"]}
