# Estatua de búho v2: metal negro azulado con pátina dorada, plumas una a una, ojos ámbar con córnea,
# humo azul detrás y bloom. A partir de la imagen de referencia de Julián.
#   buho-blender run buho_estatua.py   [--fondo]      · idempotente
# Unidades en metros (unos 2,1 m de alto). Mira hacia -Y.

import importlib
import math
import random
import sys

import bmesh
import bpy
from mathutils import Vector

LIB = r"C:\Users\jferc\Documents\Bussines\BUHO\repo auxiliar\BuhoAI-Blender-CLI\lib"
if LIB not in sys.path:
    sys.path.insert(0, LIB)
import buho3d as b3
importlib.reload(b3)

random.seed(17)
b3.quitar_por_defecto()
col = b3.coleccion("BuhoEstatua")
plumas_col = b3.coleccion("BuhoEstatua_Plumas", padre=col)
b3._actual["col"] = col

# ======================================================================
# Materiales
# ======================================================================

def bronce(nombre, escamas=50.0, oro=0.66, azul="#123A9A", oro_arriba=0.0):
    """Metal oscuro con vetas azules, relieve de escamas y pátina dorada (más arriba si oro_arriba > 0)."""
    m, n, b = b3.material_nodos(nombre)
    tc = n.nuevo("ShaderNodeTexCoord")
    vor = n.nuevo("ShaderNodeTexVoronoi", feature="DISTANCE_TO_EDGE", Scale=escamas)
    n.unir(tc, "Object", vor, "Vector")
    alto = n.rampa(vor.outputs["Distance"], [(0.0, "#000000"), (0.06, "#FFFFFF")])
    bump = n.nuevo("ShaderNodeBump", Strength=0.45, Distance=0.006)
    n.unir(alto.node, 0, bump, "Height")
    ruido = n.nuevo("ShaderNodeTexNoise", Scale=3.5, Detail=10.0, Roughness=0.68)
    n.unir(tc, "Object", ruido, "Vector")
    fac_oro = ruido.outputs["Fac"]
    if oro_arriba:
        sep = n.nuevo("ShaderNodeSeparateXYZ")
        n.unir(tc, "Object", sep, "Vector")
        fac_oro = n.mate("ADD", fac_oro, n.mate("MULTIPLY", sep.outputs["Z"], oro_arriba))
    manchas = n.rampa(fac_oro, [(oro, "#000000"), (oro + 0.1, "#FFFFFF")])
    juntas = n.rampa(vor.outputs["Distance"], [(0.0, "#FFFFFF"), (0.012, "#000000")])
    juntas = n.mate("MULTIPLY", juntas, 0.35)
    f = n.mate("MAXIMUM", manchas, juntas)
    vetas = n.nuevo("ShaderNodeTexNoise", Scale=1.3, Detail=4.0)
    n.unir(tc, "Object", vetas, "Vector")
    base = n.mezcla(n.rampa(vetas.outputs["Fac"], [(0.35, "#000000"), (0.7, "#FFFFFF")]), "#04060B", azul)
    col_final = n.mezcla(f, base, "#B68D47")
    n.nt.links.new(col_final, b.inputs["Base Color"])
    rug = n.rampa(f, [(0.0, (0.3, 0.3, 0.3, 1)), (1.0, (0.45, 0.45, 0.45, 1))])
    n.nt.links.new(rug, b.inputs["Roughness"])
    b.inputs["Metallic"].default_value = 0.9
    b.inputs["Coat Weight"].default_value = 0.25
    n.unir(bump, "Normal", b, "Normal")
    return m


def mat_pluma(nombre, azul="#1A4FC4", oro_borde=0.82, prob_oro=0.45):
    """Pluma: negro azulado con barbas en relieve, borde dorado en parte de las plumas (al azar)."""
    m, n, b = b3.material_nodos(nombre)
    borde = n.nuevo("ShaderNodeAttribute", attribute_name="borde")
    largo = n.nuevo("ShaderNodeAttribute", attribute_name="largo")
    info = n.nuevo("ShaderNodeObjectInfo")
    lleva = n.mate("GREATER_THAN", info.outputs["Random"], 1 - prob_oro)
    filo = n.rampa(borde.outputs["Fac"], [(oro_borde, "#000000"), (oro_borde + 0.08, "#FFFFFF")])
    punta = n.rampa(largo.outputs["Fac"], [(0.82, "#000000"), (0.95, "#FFFFFF")])
    oro = n.mate("MULTIPLY", n.mate("MAXIMUM", filo, n.mate("MULTIPLY", punta, 0.7)), lleva)
    raquis = n.rampa(borde.outputs["Fac"], [(0.0, "#FFFFFF"), (0.07, "#000000")])
    oro = n.mate("MAXIMUM", oro, n.mate("MULTIPLY", raquis, 0.35))
    tono = n.mate("MULTIPLY", n.mate("ADD", largo.outputs["Fac"], info.outputs["Random"]), 0.5)
    base = n.mezcla(n.rampa(tono, [(0.15, "#000000"), (0.8, "#FFFFFF")]), "#03050A", azul)
    n.nt.links.new(n.mezcla(oro, base, "#C09450"), b.inputs["Base Color"])
    tc = n.nuevo("ShaderNodeTexCoord")
    ondas = n.nuevo("ShaderNodeTexWave", wave_type="BANDS", bands_direction="DIAGONAL", Scale=22.0,
                    Distortion=1.5, Detail=2.0)
    n.unir(tc, "Generated", ondas, "Vector")
    bump = n.nuevo("ShaderNodeBump", Strength=0.25, Distance=0.002)
    n.unir(ondas, "Fac", bump, "Height")
    n.unir(bump, "Normal", b, "Normal")
    b.inputs["Metallic"].default_value = 0.9
    b.inputs["Roughness"].default_value = 0.32
    b.inputs["Coat Weight"].default_value = 0.3
    return m


def mat_iris(nombre):
    m, n, b = b3.material_nodos(nombre)
    tc = n.nuevo("ShaderNodeTexCoord")
    sep = n.nuevo("ShaderNodeSeparateXYZ")
    n.unir(tc, "Object", sep, "Vector")
    r = n.mate("SQRT", n.mate("ADD", n.mate("POWER", sep.outputs["X"], 2.0), n.mate("POWER", sep.outputs["Z"], 2.0)))
    r = n.mate("DIVIDE", r, 0.095)
    estrias = n.nuevo("ShaderNodeTexNoise", Scale=40.0, Detail=3.0)
    n.unir(tc, "Object", estrias, "Vector")
    col_iris = n.rampa(r, [(0.0, "#FFF2A0"), (0.45, "#FFD21F"), (0.78, "#FF9A00"), (0.97, "#5A2200")])
    col_iris = n.mezcla(n.mate("MULTIPLY", estrias.outputs["Fac"], 0.35), col_iris, "#FF7A00", "MULTIPLY")
    n.nt.links.new(col_iris, b.inputs["Base Color"])
    n.nt.links.new(col_iris, b.inputs["Emission Color"])
    b.inputs["Emission Strength"].default_value = 2.6
    b.inputs["Roughness"].default_value = 0.35
    b.inputs["Coat Weight"].default_value = 1.0
    b.inputs["Coat Roughness"].default_value = 0.02
    return m


CUERPO = bronce("BE_Cuerpo", 55.0, 0.7)
CABEZA = bronce("BE_Cabeza", 60.0, 0.62, oro_arriba=0.35)
ANILLO = bronce("BE_Anillo", 90.0, 0.3, azul="#3A2A12")
DISCO = bronce("BE_Disco", 80.0, 0.75, azul="#0E2E7A")
P_ALA = mat_pluma("BE_PlumaAla")
P_PECHO = mat_pluma("BE_PlumaPecho", azul="#0F2F7E", oro_borde=0.9, prob_oro=0.2)
P_CARA = mat_pluma("BE_PlumaCara", azul="#1A4FC4", oro_borde=0.85, prob_oro=0.6)
IRIS = mat_iris("BE_Iris")
PUPILA = b3.pbr("BE_Pupila", "#010101", rugosidad=0.05, capa=1.0)
CORNEA = b3.vidrio("BE_Cornea", rugosidad=0.0, ior=1.38)
ORO = b3.metal("BE_Oro", "#9C7838", rugosidad=0.35)

# ======================================================================
# Malla de pluma (raíz en el origen, largo hacia -Y, normal +Z) con atributos «borde» y «largo»
# ======================================================================

def malla_pluma(nombre, largo, ancho, redonda=False, nu=12, nv=8, grosor=0.003):
    def perfil(u):
        if redonda:
            return (min(u / 0.12, 1.0)) ** 0.5 * max(0.0, 1 - u ** 3) ** 0.45
        return (min(u / 0.18, 1.0)) ** 0.6 * max(0.0, 1 - u ** 2.2) ** 0.5

    def f(u, v):
        w = perfil(u) * ancho / 2
        x = (v - 0.5) * 2 * w
        z = 0.0035 * (1 - abs(2 * v - 1)) + 0.012 * (1 - u) ** 2   # raquis y raíz levantada
        return (x, -largo * u, z)

    vieja = bpy.data.meshes.get(nombre)
    if vieja:
        bpy.data.meshes.remove(vieja)
    bm = b3.bm_rejilla(nu, nv, f)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=grosor)
    me = bpy.data.meshes.new(nombre)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    a_borde = me.attributes.new("borde", "FLOAT", "POINT")
    a_largo = me.attributes.new("largo", "FLOAT", "POINT")
    for i, v in enumerate(me.vertices):
        u = min(max(-v.co.y / largo, 0.0), 1.0)
        w = perfil(u) * ancho / 2
        a_borde.data[i].value = min(abs(v.co.x) / w, 1.0) if w > 1e-5 else 1.0
        a_largo.data[i].value = u
    return me


M_ALA = malla_pluma("BE_m_PlumaAla", 0.15, 0.075)
M_ALA_LARGA = malla_pluma("BE_m_PlumaAlaLarga", 0.30, 0.08)
M_PECHO = malla_pluma("BE_m_PlumaPecho", 0.075, 0.06, redonda=True)
M_CARA = malla_pluma("BE_m_PlumaCara", 0.07, 0.035)
for me, mat in ((M_ALA, P_ALA), (M_ALA_LARGA, P_ALA), (M_PECHO, P_PECHO), (M_CARA, P_CARA)):
    me.materials.append(mat)

# ======================================================================
# Formas grandes
# ======================================================================

def forma_cuerpo(p):
    ancho = 1.0 + 0.12 * (-p.z)
    return Vector((p.x * 0.56 * ancho, p.y * 0.46 * ancho * (0.85 if p.y < 0 else 1.0), p.z * 0.86))


cuerpo = b3.objeto("Cuerpo", b3.deformar(b3.bm_esfera(1.0, 64, 32), forma_cuerpo), CUERPO, loc=(0, 0, 0.98), subsurf=1)

CZ = 1.78


def forma_cabeza(p):
    t = (p.z + 0.3) / 0.6
    p.x *= 0.9 + 0.1 * t
    if t > 0.95:
        p.z += 0.08 * (abs(p.x) / 0.38) ** 3
        p.z -= 0.025 * (1 - abs(p.x) / 0.38)
    if p.y < 0:
        p.y *= 0.9
        if 0.45 < t < 0.8:   # entrecejo adelantado en V sobre el pico
            p.y -= 0.035 * max(0.0, 1 - abs(p.x) / 0.12) * (1 - abs(t - 0.62) / 0.18)
    return p


cabeza = b3.objeto("Cabeza", b3.deformar(b3.bm_caja(0.76, 0.56, 0.6, cortes=6), forma_cabeza), CABEZA,
                   loc=(0, 0.02, CZ), bevel=0.04, subsurf=1)

alas = []
for s in (-1, 1):
    def forma_ala(p, s=s):
        p = Vector((p.x * 0.13, p.y * 0.33, p.z * 0.8))
        if p.x * s < 0:
            p.x *= 0.4
        return p
    alas.append(b3.objeto(f"Ala_{'D' if s > 0 else 'I'}", b3.deformar(b3.bm_esfera(1.0, 48, 24), forma_ala), CUERPO,
                          loc=(s * 0.44, -0.05, 1.02), rot=(0, s * -5, s * 10), subsurf=1))

b3.objeto("Anillo", b3.deformar(b3.bm_toro(0.53, 0.045, 96, 20), lambda p: Vector((p.x, p.y * 0.86, p.z * 1.4))),
          ANILLO, loc=(0, -0.02, 0.36))
b3.objeto("Faldon", b3.bm_revolucion([(0.5, 0.0), (0.56, 0.06), (0.55, 0.2), (0.5, 0.3)], 96), CUERPO, loc=(0, 0, 0.06),
          escala=(1, 0.88, 1), subsurf=1)
b3.objeto("Peana", b3.bm_revolucion([(0.64, 0.0), (0.66, 0.05), (0.62, 0.09), (0.6, 0.12)], 96), ANILLO, loc=(0, 0, 0))

# ======================================================================
# Cara
# ======================================================================
OJO_Y = -0.262
for s in (-1, 1):
    lado = "D" if s > 0 else "I"
    c = Vector((s * 0.178, OJO_Y, CZ - 0.01))
    b3.objeto(f"Cuenco_{lado}", b3.bm_revolucion([(0.0, 0.0), (0.12, 0.004), (0.185, 0.022), (0.2, 0.03), (0.19, 0.04)], 64,
              eje="Y"), DISCO, loc=(c.x, c.y + 0.03, c.z), rot=(0, 0, 180), subsurf=1)
    for anillo, (r, cuantas, esc) in enumerate(((0.105, 20, 0.8), (0.135, 26, 1.0), (0.165, 32, 1.15))):
        for k in range(cuantas):
            a = 2 * math.pi * (k + 0.5 * (anillo % 2)) / cuantas
            radial = Vector((math.cos(a), 0, math.sin(a)))
            p = c + radial * r + Vector((0, 0.012 + 0.006 * anillo, 0))
            normal = (Vector((0, -1, 0)) + radial * 0.35).normalized()
            b3.instancia(f"PlumaCara_{lado}{anillo}_{k}", M_CARA,
                         b3.matriz_superficie(p, normal, abajo=radial, inclinacion=8, escala=esc), col=plumas_col)
    b3.objeto(f"Iris_{lado}", b3.bm_esfera(0.095, 48, 24), IRIS, loc=c)
    b3.objeto(f"Pupila_{lado}", b3.deformar(b3.bm_esfera(0.058, 32, 16), lambda p: Vector((p.x, p.y * 0.45, p.z))),
              PUPILA, loc=(c.x, c.y - 0.072, c.z))
    b3.objeto(f"AroOjo_{lado}", b3.bm_toro(0.1, 0.007, 64, 10, eje="Y"), ORO, loc=(c.x, c.y - 0.01, c.z))
    b3.tubo(f"Ceja_{lado}", [(s * 0.02, -0.3, CZ + 0.075), (s * 0.13, -0.29, CZ + 0.115), (s * 0.29, -0.255, CZ + 0.14)],
            0.016, ORO, radios=[0.6, 1.0, 0.35])

b3.tubo("Pico", [(0, -0.29, CZ + 0.03), (0, -0.335, CZ - 0.04), (0, -0.32, CZ - 0.11)], 0.04, ORO, radios=[1.0, 0.65, 0.08])

# ======================================================================
# Plumas proyectadas sobre alas, pecho y cuello
# ======================================================================
bpy.context.view_layer.update()
n_plumas = 0

for s, ala in zip((-1, 1), alas):
    arbol = b3.bvh(ala)
    centro = ala.matrix_world.translation
    filas = 19
    for f in range(filas):
        z = 1.72 - f * 0.072
        larga = f >= filas - 4
        malla = M_ALA_LARGA if larga else M_ALA
        cols = 6 if larga else 8
        for k in range(cols):
            phi = math.radians(-10 + (k + 0.5 * (f % 2)) * (150 / cols))
            d = Vector((s * math.sin(phi), -math.cos(phi), 0))
            hit = b3.proyectar(arbol, Vector((centro.x, centro.y, z)) + d * 1.5, -d)
            if not hit:
                continue
            p, nrm = hit
            esc = random.uniform(0.92, 1.08) * (1.0 + 0.15 * (f / filas))
            b3.instancia(f"PlumaAla_{s}_{f}_{k}", malla,
                         b3.matriz_superficie(p, nrm, giro=random.uniform(-6, 6), inclinacion=10 + random.uniform(-3, 3),
                                              escala=esc, separacion=0.004), col=plumas_col)
            n_plumas += 1

# ======================================================================
# Escena: humo, luces, cámara, render
# ======================================================================
b3.niebla("Humo", (9, 2.5, 6), (0, 2.6, 1.6), "#2B4A8C", densidad=0.22, escala=1.1, contraste=(0.52, 0.8))
b3.niebla("HumoCerca", (7, 1.0, 4), (0, -1.6, 1.4), "#6C8CC8", densidad=0.08, escala=1.6, contraste=(0.55, 0.85))
clave = b3.luz("Clave", "SPOT", 900, (-1.6, -2.4, 3.4), (0, 0, 1.6), "#FFE0B8", 0.25)
clave.data.spot_size = 0.9
clave.data.spot_blend = 0.6
clave.data.specular_factor = 1.0
b3.luz("ContraI", "AREA", 650, (-2.0, 1.4, 2.4), (0, 0, 1.3), "#5C7CFF", 1.6)
b3.luz("ContraD", "AREA", 650, (2.0, 1.4, 2.4), (0, 0, 1.3), "#5C7CFF", 1.6)
b3.luz("Halo", "POINT", 500, (0, 1.6, 1.6), (0, 0, 1.6), "#4F78D8", 0.6)
b3.camara("BE_Cam", (0, -3.7, 0.45), (0, 0, 1.36), lente=40)
b3.mundo("#010204")
b3.render("BLENDER_EEVEE", 1920, 1080, muestras=64)
bloom = b3.resplandor(0.55, 1.2, 7)

result = {"objetos": len(col.all_objects), "plumas": n_plumas, "bloom": bloom}
