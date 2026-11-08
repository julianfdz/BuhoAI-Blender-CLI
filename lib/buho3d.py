"""buho3d: utilidades de modelado para scripts de buho-blender.

    import buho3d as b3
    col = b3.coleccion("MiModelo")              # crea o vacía la colección (idempotente)
    m = b3.metal("Acero", "#8A9099")             # materiales PBR listos
    o = b3.caja("Cuerpo", (0.2, 0.1, 0.05), m, col=col, bevel=0.01, subsurf=1)
    b3.poblar(o, pluma_obj, puntos)              # reparte instancias sobre una superficie

Todas las medidas en metros. Los nombres de mallas llevan el prefijo de la colección para poder limpiarlas.
"""

import math
import random

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

# ======================================================================
# Escena y colecciones
# ======================================================================

_actual = {"col": None}


def coleccion(nombre, limpiar=True, padre=None):
    """Crea (o vacía) una colección y la deja como destino por defecto de lo que se cree."""
    col = bpy.data.collections.get(nombre)
    if col is None:
        col = bpy.data.collections.new(nombre)
        (padre or bpy.context.scene.collection).children.link(col)
    elif limpiar:
        for hija in list(col.children_recursive):
            for o in list(hija.objects):
                bpy.data.objects.remove(o, do_unlink=True)
            bpy.data.collections.remove(hija)
        for o in list(col.objects):
            bpy.data.objects.remove(o, do_unlink=True)
        purgar()
    _actual["col"] = col
    return col


def purgar():
    """Borra mallas, curvas, materiales e imágenes sin usuarios."""
    for bloque in (bpy.data.meshes, bpy.data.curves, bpy.data.lights, bpy.data.cameras):
        for d in list(bloque):
            if d.users == 0:
                bloque.remove(d)


def quitar_por_defecto():
    """Quita el cubo, la luz y la cámara de la escena de fábrica (solo si el archivo no está guardado)."""
    if bpy.data.filepath:
        return
    for n in ("Cube", "Light", "Camera"):
        o = bpy.data.objects.get(n)
        if o:
            bpy.data.objects.remove(o, do_unlink=True)


def _col(col):
    return col or _actual["col"] or bpy.context.scene.collection


# ======================================================================
# Color
# ======================================================================

def color(h):
    """'#RRGGBB' (sRGB) -> tupla lineal RGBA para Blender."""
    if isinstance(h, (tuple, list)):
        return (*h[:3], 1.0) if len(h) == 3 else tuple(h)
    h = h.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in c) + (1.0,)


# ======================================================================
# Materiales
# ======================================================================

def _nuevo_mat(nombre):
    m = bpy.data.materials.get(nombre) or bpy.data.materials.new(nombre)
    m.use_nodes = True
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    out.location = (600, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (300, 0)
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return m, nt, bsdf, out


def pbr(nombre, base="#808080", metal=0.0, rugosidad=0.5, emision=None, fuerza=0.0,
        transmision=0.0, ior=1.45, alfa=1.0, capa=0.0, brillo_tela=0.0):
    m, nt, b, _ = _nuevo_mat(nombre)
    b.inputs["Base Color"].default_value = color(base)
    b.inputs["Metallic"].default_value = metal
    b.inputs["Roughness"].default_value = rugosidad
    b.inputs["Transmission Weight"].default_value = transmision
    b.inputs["IOR"].default_value = ior
    b.inputs["Coat Weight"].default_value = capa
    b.inputs["Sheen Weight"].default_value = brillo_tela
    if emision:
        b.inputs["Emission Color"].default_value = color(emision)
        b.inputs["Emission Strength"].default_value = fuerza
    if alfa < 1:
        b.inputs["Alpha"].default_value = alfa
        m.surface_render_method = "BLENDED"
    return m


def metal(nombre, base="#8A9099", rugosidad=0.3):
    return pbr(nombre, base, metal=1.0, rugosidad=rugosidad)


def plastico(nombre, base="#202020", rugosidad=0.45):
    return pbr(nombre, base, rugosidad=rugosidad, capa=0.2)


def vidrio(nombre, base="#FFFFFF", rugosidad=0.0, ior=1.5):
    return pbr(nombre, base, rugosidad=rugosidad, transmision=1.0, ior=ior)


def emisivo(nombre, base="#00C1B8", fuerza=5.0):
    return pbr(nombre, base, rugosidad=0.4, emision=base, fuerza=fuerza)


class Nodos:
    """Ayuda para montar árboles de nodos sin pelearse con los índices.

        n = Nodos(material)
        tc = n.nuevo("ShaderNodeTexCoord")
        ruido = n.nuevo("ShaderNodeTexNoise", Scale=4, Detail=8)
        n.unir(tc, "Object", ruido, "Vector")
    """

    def __init__(self, material_o_arbol):
        self.nt = getattr(material_o_arbol, "node_tree", material_o_arbol)
        self.x = -1400

    def nuevo(self, tipo, **entradas):
        nodo = self.nt.nodes.new(tipo)
        nodo.location = (self.x, 0)
        self.x += 180
        for k, v in entradas.items():
            if hasattr(nodo, k) and k not in nodo.inputs:
                setattr(nodo, k, v)
            else:
                nombre = k.replace("_", " ")
                entrada = nodo.inputs.get(nombre) or nodo.inputs.get(k)
                if entrada is None:
                    raise KeyError(f"{tipo} no tiene la entrada {k}")
                if isinstance(v, str) and v.startswith("#"):
                    v = color(v)
                entrada.default_value = v
        return nodo

    def unir(self, a, salida, b, entrada):
        so = a.outputs[salida] if isinstance(salida, (int, str)) else salida
        si = b.inputs[entrada] if isinstance(entrada, (int, str)) else entrada
        self.nt.links.new(so, si)
        return b

    def mezcla(self, fac, a, b, modo="MIX"):
        """Mezcla de colores (ShaderNodeMix RGBA). a/b: socket o color '#...'."""
        mx = self.nuevo("ShaderNodeMix", data_type="RGBA", blend_type=modo)
        self._a_entrada(fac, mx.inputs[0])
        self._a_entrada(a, mx.inputs[6])
        self._a_entrada(b, mx.inputs[7])
        return mx.outputs[2]

    def mate(self, op, a, b=0.0):
        m = self.nuevo("ShaderNodeMath", operation=op)
        self._a_entrada(a, m.inputs[0])
        self._a_entrada(b, m.inputs[1])
        return m.outputs[0]

    def rampa(self, fac, paradas):
        """paradas: [(posición, '#color' o (r,g,b,a)), ...]"""
        r = self.nuevo("ShaderNodeValToRGB")
        els = r.color_ramp.elements
        while len(els) > len(paradas):
            els.remove(els[-1])
        while len(els) < len(paradas):
            els.new(0.5)
        for el, (pos, c) in zip(els, paradas):
            el.position = pos
            el.color = color(c) if isinstance(c, str) else (c if len(c) == 4 else (c, c, c, 1))
        self._a_entrada(fac, r.inputs[0])
        return r.outputs[0]

    def _a_entrada(self, v, entrada):
        if isinstance(v, bpy.types.NodeSocket):
            self.nt.links.new(v, entrada)
        elif isinstance(v, str):
            entrada.default_value = color(v)
        else:
            entrada.default_value = v


def material_nodos(nombre):
    """Material vacío con Principled y salida; devuelve (material, Nodos, bsdf)."""
    m, nt, b, _ = _nuevo_mat(nombre)
    return m, Nodos(m), b


# ======================================================================
# Mallas
# ======================================================================

def objeto(nombre, bm, material=None, col=None, loc=(0, 0, 0), rot=(0, 0, 0), escala=(1, 1, 1),
           suave=True, bevel=0.0, bevel_segs=3, subsurf=0, solidify=0.0, padre=None):
    """Convierte un bmesh en objeto. rot en grados."""
    me = bpy.data.meshes.new(nombre)
    bm.normal_update()
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = suave
    o = bpy.data.objects.new(nombre, me)
    o.location = loc
    o.rotation_euler = [math.radians(a) for a in rot]
    o.scale = escala
    if material is not None:
        for m in (material if isinstance(material, (list, tuple)) else [material]):
            me.materials.append(m)
    _col(col).objects.link(o)
    if padre:
        o.parent = padre
    if solidify:
        md = o.modifiers.new("Solidify", "SOLIDIFY")
        md.thickness = solidify
        md.offset = 0
    if bevel:
        md = o.modifiers.new("Bevel", "BEVEL")
        md.width = bevel
        md.segments = bevel_segs
        md.limit_method = "ANGLE"
    if subsurf:
        md = o.modifiers.new("Subdiv", "SUBSURF")
        md.levels = subsurf
        md.render_levels = subsurf
    return o


def bm_caja(sx, sy, sz, cortes=0):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    if cortes:
        bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cortes, use_grid_fill=True)
    bmesh.ops.scale(bm, vec=(sx, sy, sz), verts=bm.verts)
    return bm


def bm_esfera(r=1.0, u=48, v=24):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=r)
    return bm


def bm_cilindro(r1, r2, alto, segs=48, eje="Z", tapas=True):
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=tapas, segments=segs, radius1=r1, radius2=r2, depth=alto)
    _girar_eje(bm, eje)
    return bm


def bm_toro(R, r, segs=64, rsegs=16, eje="Z", arco=360.0):
    bm = bmesh.new()
    cerrado = arco >= 360
    n = segs if cerrado else segs + 1
    anillos = []
    for i in range(n):
        a = math.radians(arco) * i / segs
        c = Vector((math.cos(a) * R, math.sin(a) * R, 0))
        d = Vector((math.cos(a), math.sin(a), 0))
        anillos.append([bm.verts.new(c + d * math.cos(b) * r + Vector((0, 0, math.sin(b) * r)))
                        for b in (2 * math.pi * j / rsegs for j in range(rsegs))])
    for i in range(segs):
        a, b = anillos[i], anillos[(i + 1) % n]
        for j in range(rsegs):
            bm.faces.new((a[j], a[(j + 1) % rsegs], b[(j + 1) % rsegs], b[j]))
    _girar_eje(bm, eje)
    return bm


def bm_rejilla(nu, nv, f):
    """Superficie paramétrica: f(u, v) -> (x, y, z) con u, v en [0, 1]."""
    bm = bmesh.new()
    vs = [[bm.verts.new(f(i / nu, j / nv)) for j in range(nv + 1)] for i in range(nu + 1)]
    for i in range(nu):
        for j in range(nv):
            bm.faces.new((vs[i][j], vs[i + 1][j], vs[i + 1][j + 1], vs[i][j + 1]))
    return bm


def bm_revolucion(perfil, segs=64, eje="Z"):
    """Sólido de revolución a partir de [(radio, altura), ...] de abajo arriba."""
    bm = bmesh.new()
    anillos = []
    for r, z in perfil:
        anillos.append([bm.verts.new((math.cos(2 * math.pi * k / segs) * r, math.sin(2 * math.pi * k / segs) * r, z))
                        for k in range(segs)])
    for a, b in zip(anillos, anillos[1:]):
        for k in range(segs):
            bm.faces.new((a[k], a[(k + 1) % segs], b[(k + 1) % segs], b[k]))
    bm.faces.new(list(reversed(anillos[0])))
    bm.faces.new(anillos[-1])
    _girar_eje(bm, eje)
    return bm


def _girar_eje(bm, eje):
    if eje == "Y":
        bmesh.ops.rotate(bm, verts=bm.verts, matrix=Matrix.Rotation(math.radians(-90), 3, "X"))
    elif eje == "X":
        bmesh.ops.rotate(bm, verts=bm.verts, matrix=Matrix.Rotation(math.radians(90), 3, "Y"))


def deformar(bm, f):
    """Aplica f(Vector) -> Vector a cada vértice."""
    for v in bm.verts:
        v.co = Vector(f(v.co.copy()))
    return bm


# Atajos que crean el objeto directamente
def caja(nombre, tam, material=None, **kw):
    return objeto(nombre, bm_caja(*tam, cortes=kw.pop("cortes", 0)), material, **kw)


def esfera(nombre, r, material=None, **kw):
    return objeto(nombre, bm_esfera(r), material, **kw)


def cilindro(nombre, r, alto, material=None, r2=None, eje="Z", **kw):
    return objeto(nombre, bm_cilindro(r, r if r2 is None else r2, alto, eje=eje), material, **kw)


def toro(nombre, R, r, material=None, eje="Z", **kw):
    return objeto(nombre, bm_toro(R, r, eje=eje), material, **kw)


def tubo(nombre, puntos, radio, material=None, col=None, resolucion=12, radios=None):
    """Tubo suave que pasa por los puntos (curva Bézier con grosor). radios: grosor por punto (factor)."""
    cu = bpy.data.curves.new(nombre, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = radio
    cu.bevel_resolution = 4
    cu.resolution_u = resolucion
    cu.use_fill_caps = True
    sp = cu.splines.new("BEZIER")
    sp.bezier_points.add(len(puntos) - 1)
    for i, p in enumerate(puntos):
        bp = sp.bezier_points[i]
        bp.co = p
        bp.handle_left_type = bp.handle_right_type = "AUTO"
        if radios:
            bp.radius = radios[i]
    o = bpy.data.objects.new(nombre, cu)
    if material is not None:
        cu.materials.append(material)
    _col(col).objects.link(o)
    return o


def modificador(o, tipo, **props):
    md = o.modifiers.new(tipo.title(), tipo)
    for k, v in props.items():
        setattr(md, k, v)
    return md


def booleano(o, cortador, operacion="DIFFERENCE", ocultar=True):
    md = o.modifiers.new("Bool", "BOOLEAN")
    md.operation = operacion
    md.object = cortador
    md.solver = "EXACT"
    if ocultar:
        cortador.hide_render = True
        cortador.display_type = "WIRE"
        cortador.hide_set(True)
    return md


def aplicar_modificadores(o):
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(o.evaluated_get(dg))
    viejo = o.data
    o.modifiers.clear()
    o.data = me
    if viejo.users == 0:
        bpy.data.meshes.remove(viejo)
    return o


# ======================================================================
# Repartir instancias sobre superficies (plumas, escamas, remaches, pelo…)
# ======================================================================

def bvh(o):
    """Árbol de búsqueda de la malla evaluada (con modificadores) en coordenadas de mundo."""
    dg = bpy.context.evaluated_depsgraph_get()
    oe = o.evaluated_get(dg)
    me = oe.to_mesh()
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.transform(o.matrix_world)
    arbol = BVHTree.FromBMesh(bm)
    bm.free()
    oe.to_mesh_clear()
    return arbol


def proyectar(arbol, origen, direccion):
    """Lanza un rayo; devuelve (punto, normal) o None."""
    loc, nor, _, _ = arbol.ray_cast(Vector(origen), Vector(direccion).normalized())
    return (loc, nor) if loc is not None else None


def matriz_superficie(punto, normal, abajo=(0, 0, -1), giro=0.0, inclinacion=0.0, escala=1.0, separacion=0.0):
    """Matriz para un elemento plano tumbado sobre la superficie.

    El elemento se modela en local con la normal en +Z y el largo hacia -Y (raíz en el origen).
    'abajo' es hacia dónde cae (se proyecta en el plano tangente); 'inclinacion' (grados) levanta la punta.
    """
    n = Vector(normal).normalized()
    t = Vector(abajo) - n * Vector(abajo).dot(n)
    if t.length < 1e-5:
        t = n.orthogonal()
    t.normalize()
    if giro:
        t = Matrix.Rotation(math.radians(giro), 3, n) @ t
    arriba = -t
    x = arriba.cross(n).normalized()
    rot = Matrix((x, arriba, n)).transposed().to_4x4()
    if inclinacion:
        rot = rot @ Matrix.Rotation(math.radians(inclinacion), 4, "X")
    return Matrix.Translation(Vector(punto) + n * separacion) @ rot @ Matrix.Scale(escala, 4)


def instancia(nombre, malla, matriz, col=None, material=None):
    """Copia enlazada (comparte malla) colocada con una matriz de mundo."""
    o = bpy.data.objects.new(nombre, malla)
    o.matrix_world = matriz
    _col(col).objects.link(o)
    if material is not None and o.material_slots:
        o.material_slots[0].link = "OBJECT"
        o.material_slots[0].material = material
    return o


def unir(objetos, nombre, col=None):
    """Une objetos (con modificadores aplicados) en una sola malla y borra los originales."""
    dg = bpy.context.evaluated_depsgraph_get()
    bm = bmesh.new()
    mats = []
    for o in objetos:
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
    for o in objetos:
        bpy.data.objects.remove(o, do_unlink=True)
    me = bpy.data.meshes.new(nombre)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    o = bpy.data.objects.new(nombre, me)
    _col(col).objects.link(o)
    return o


# ======================================================================
# Cámara, luces, mundo y render
# ======================================================================

def mirar(o, objetivo):
    o.rotation_euler = (Vector(objetivo) - o.location).to_track_quat("-Z", "Y").to_euler()


def camara(nombre, pos, objetivo, lente=50, col=None, activa=True, dof=None):
    cd = bpy.data.cameras.get(nombre) or bpy.data.cameras.new(nombre)
    cd.lens = lente
    o = bpy.data.objects.get(nombre) or bpy.data.objects.new(nombre, cd)
    if o.name not in _col(col).objects:
        _col(col).objects.link(o)
    o.location = pos
    mirar(o, objetivo)
    if dof:
        cd.dof.use_dof = True
        cd.dof.focus_distance = (Vector(objetivo) - Vector(pos)).length
        cd.dof.aperture_fstop = dof
    if activa:
        bpy.context.scene.camera = o
    return o


def luz(nombre, tipo, energia, pos, objetivo=(0, 0, 0), color_luz="#FFFFFF", tam=1.0, col=None):
    ld = bpy.data.lights.new(nombre, tipo)
    ld.energy = energia
    ld.color = color(color_luz)[:3]
    if tipo == "AREA":
        ld.size = tam
    elif tipo in ("POINT", "SPOT"):
        ld.shadow_soft_size = tam
    o = bpy.data.objects.new(nombre, ld)
    o.location = pos
    mirar(o, objetivo)
    _col(col).objects.link(o)
    return o


def estudio(objetivo=(0, 0, 0), distancia=4.0, fuerza=1.0, col=None, calida="#FFE2C0", fria="#9FB8FF"):
    """Iluminación de tres puntos alrededor de un objetivo."""
    o = Vector(objetivo)
    d = distancia
    return [
        luz("Clave", "AREA", 300 * fuerza * d * d / 16, o + Vector((-0.6 * d, -0.8 * d, 0.7 * d)), o, calida, d * 0.3, col),
        luz("Relleno", "AREA", 90 * fuerza * d * d / 16, o + Vector((0.9 * d, -0.6 * d, 0.2 * d)), o, "#FFFFFF", d * 0.4, col),
        luz("Contra", "AREA", 400 * fuerza * d * d / 16, o + Vector((0.2 * d, 0.9 * d, 0.6 * d)), o, fria, d * 0.3, col),
    ]


def mundo(fondo="#050608", fuerza=1.0):
    sc = bpy.context.scene
    w = sc.world or bpy.data.worlds.new("World")
    sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background") or w.node_tree.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = color(fondo)
    bg.inputs["Strength"].default_value = fuerza
    return w


def niebla(nombre, tam, pos, color_niebla="#2A4C8A", densidad=0.6, escala=1.5, col=None, contraste=(0.45, 0.75)):
    """Caja de humo volumétrico con ruido (EEVEE y Cycles)."""
    m, n, b = material_nodos(nombre + "_Vol")
    nt = n.nt
    nt.nodes.remove(b)
    out = [x for x in nt.nodes if x.type == "OUTPUT_MATERIAL"][0]
    tc = n.nuevo("ShaderNodeTexCoord")
    ruido = n.nuevo("ShaderNodeTexNoise", Scale=escala, Detail=10.0, Roughness=0.62, Distortion=0.6)
    n.unir(tc, "Object", ruido, "Vector")
    dens = n.rampa(ruido.outputs["Fac"], [(contraste[0], "#000000"), (contraste[1], "#FFFFFF")])
    dens = n.mate("MULTIPLY", dens, densidad)
    vol = n.nuevo("ShaderNodeVolumePrincipled")
    vol.inputs["Color"].default_value = color(color_niebla)
    vol.inputs["Anisotropy"].default_value = 0.4
    nt.links.new(dens, vol.inputs["Density"])
    nt.links.new(vol.outputs[0], out.inputs["Volume"])
    o = objeto(nombre, bm_caja(*tam), m, col=col, loc=pos, suave=False)
    return o


def render(motor="BLENDER_EEVEE", ancho=1920, alto=1080, muestras=64, look="AgX - Punchy", trazado=True):
    sc = bpy.context.scene
    sc.render.engine = motor
    sc.render.resolution_x, sc.render.resolution_y = ancho, alto
    try:
        sc.view_settings.view_transform = "AgX"
        sc.view_settings.look = look
    except Exception:
        pass
    if motor == "BLENDER_EEVEE":
        e = sc.eevee
        for k, v in (("taa_render_samples", muestras), ("use_raytracing", trazado), ("use_shadows", True),
                     ("volumetric_tile_size", "4"), ("volumetric_samples", 96), ("volumetric_end", 40.0),
                     ("use_volumetric_shadows", True)):
            try:
                setattr(e, k, v)
            except Exception:
                pass
    elif motor == "CYCLES":
        sc.cycles.samples = muestras
        sc.cycles.use_denoising = True


def resplandor(intensidad=0.6, umbral=1.0, tam=6):
    """Bloom en el compositor (Glare tipo niebla). Silencioso si la API cambia."""
    sc = bpy.context.scene
    try:
        ng = bpy.data.node_groups.get("Buho_Comp") or bpy.data.node_groups.new("Buho_Comp", "CompositorNodeTree")
        ng.nodes.clear()
        if not any(s.name == "Image" for s in ng.interface.items_tree):
            ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
        rl = ng.nodes.new("CompositorNodeRLayers")
        gl = ng.nodes.new("CompositorNodeGlare")
        out = ng.nodes.new("NodeGroupOutput")
        for nombre, valor in (("Type", "Fog Glow"), ("Threshold", umbral), ("Strength", intensidad), ("Size", tam / 9)):
            s = gl.inputs.get(nombre)
            if s is not None:
                try:
                    s.default_value = valor
                except Exception:
                    pass
        for attr, valor in (("glare_type", "FOG_GLOW"), ("threshold", umbral), ("mix", intensidad - 1), ("size", tam)):
            if hasattr(gl, attr):
                try:
                    setattr(gl, attr, valor)
                except Exception:
                    pass
        ng.links.new(rl.outputs["Image"], gl.inputs["Image"])
        ng.links.new(gl.outputs["Image"], out.inputs[0])
        sc.compositing_node_group = ng
        sc.render.use_compositing = True
        return True
    except Exception as ex:  # API de compositor distinta: seguimos sin bloom
        return str(ex)


def semilla(n=7):
    random.seed(n)
