---
name: buho-blender
description: Modelar, esculpir, poner esqueleto, animar, fotografiar, grabar en vídeo, medir, importar y exportar modelos 3D en Blender con el CLI `buho-blender`, en directo en el Blender abierto (se ve lo que haces) o sin ventana para generar en lote. Úsala SIEMPRE que haya que crear o retocar un modelo 3D, un arma, un prop, un personaje, una figura tipo Funko o un escenario, esculpir una forma orgánica, ponerle huesos y animarlo (andar, puñetazos, saludar, saltar, bailar, animaciones de Mixamo), sacar una captura, una hoja de vistas o un vídeo/GIF de un .blend, contar polígonos, importar un GLB/FBX/OBJ/STL, exportar a GLB/FBX/OBJ para Unity o la web, buscar algo en la API de Blender o revisar qué hay en una escena, aunque no se nombre el CLI. Vale para cualquier agente con terminal (Claude, Codex, Copilot, Gemini, scripts).
---

# buho-blender

CLI propio de Búho AI para trabajar con Blender desde un agente. Todo sale en JSON (`--json` para una sola línea). Dos modos:

- **En directo** (por defecto): habla con el Blender abierto por el puerto 9876 del complemento oficial «MCP» de Blender Lab (`--puerto N` para otro). Lo que haces aparece en la ventana al momento. Si no hay ningún Blender abierto: `buho-blender abrir`.
- **Sin ventana** (`--fondo`): lanza `blender -b` con la escena de fábrica, o con `--archivo x.blend`. Para generar y exportar en lote sin tocar lo que el usuario tiene abierto. `--guardar x.blend` guarda al acabar (cada orden con `--fondo` es un Blender nuevo: encadena con `--archivo` + `--guardar`).

Blender está en `D:\Programas\Blender\blender.exe` (portable, 5.2 LTS). Se cambia con `BUHO_BLENDER_PATH`.

## Comandos

```bash
# Mirar
buho-blender estado                      # versión, archivo, cambios, nº de objetos, con o sin ventana
buho-blender escena                      # objetos con posición, tamaño, caras, materiales
buho-blender stats [--coleccion X]       # vértices, caras, triángulos (con modificadores e instancias), materiales,
                                         # medidas en metros, instancias, enlazados, datos compartidos, los más pesados
buho-blender captura salida.png --vista tres_cuartos     # frente|atras|derecha|izquierda|arriba|abajo|iso|camara
buho-blender captura hoja.png --vistas frente,derecha,tres_cuartos,arriba   # UNA hoja de contactos con todas
buho-blender captura salida.png --motor CYCLES --muestras 64            # Cycles en GPU si hay (OptiX/CUDA…), si no CPU
buho-blender captura pose.png --frame 30                 # un fotograma concreto de la animación
buho-blender giro giro.mp4 [--segundos 4 --fps 24 --vista tres_cuartos]  # vuelta de 360°; también .gif
buho-blender video anim.mp4 --camara orbita [--vueltas 1] [--segundos 10] [--hoja revision.png]
                                         # la animación de la escena en MP4: cámara fija (la de la escena),
                                         # frontal u orbita; --hoja saca 6 fotogramas en una hoja para revisarlo
buho-blender pantalla vista.png [--render]   # la vista 3D del Blender abierto tal como se ve (--render: sin interfaz)
buho-blender comparar foto.jpg --blend x.blend [--vista camara] [--salida comp.png]
                                         # render con el aspecto de la foto + imagen referencia | render | superposición
                                         # | siluetas, con métricas (IoU de silueta y de forma, escala, color por zonas)

# Hacer
buho-blender run script.py [--var "VOXEL=0.009"]   # ejecuta Python (bpy); también "código" o - (entrada). result = {...}
buho-blender vigilar script.py --captura c.png --vistas frente,tres_cuartos   # re-ejecuta al guardar; Ctrl+C
buho-blender importar pieza.glb [--en Coleccion]   # .glb .gltf .fbx .obj .stl .ply .usd; .blend = append
buho-blender importar otro.blend --objetos "A,B"    # o --colecciones "X,Y"
buho-blender exportar salida.glb         # o .fbx / .obj; --objetos "A,B" o --coleccion X; --unir = una sola malla
buho-blender limpiar                     # purga datos huérfanos

# Esqueleto y animación
buho-blender esqueleto [Objeto] [--coleccion X] [--tipo humanoide|cuadrupedo|cadena] [--auto] [--huesos h.json]
buho-blender animar andar --segundos 4 --bucle     # andar|punetazos|saludar|saltar|bailar|idle|asentir|girar_cabeza
buho-blender animar punetazos --nla                # --nla: la añade al final de la pista «buho» (se encadenan)
buho-blender animar --json poses.json              # animación a medida
buho-blender animar --reposo                       # quita la acción activa y vuelve a la pose de reposo
buho-blender importar-animacion walk.fbx [--en-sitio] [--nla]   # p. ej. de Mixamo, pasada al esqueleto propio
                                                   # --pies apoyar|no_atravesar|no, --cabeza auto|estable|libre

# Renders largos en segundo plano
buho-blender giro giro.mp4 --en-cola               # vuelve al instante con un id (vale para giro, video, captura, run, comparar)
buho-blender cola                                  # trabajos con estado, fotogramas hechos/total, % y tiempo restante
buho-blender cola ver <id> | esperar <id> | cancelar <id> | limpiar

# Archivos
buho-blender nuevo [salida.blend] [--vacia]   # escena de inicio sin mallas (--vacia: sin cámara ni luz)
buho-blender cargar archivo.blend        # abre un .blend en el Blender abierto
buho-blender guardar archivo.blend
buho-blender abrir [archivo.blend]

# Ayuda
buho-blender docs "bmesh bevel" [--api | --manual]   # busca en la documentación local de la API y del manual
```

Opciones comunes: `--fondo`, `--archivo x.blend`, `--guardar x.blend`, `--texto` (muestra también lo que imprima el script), `--puerto N`, `--json`, `--timeout <s>` (600). Las capturas, el giro y el vídeo aceptan `--objetos "A,B"` o `--coleccion X` (el resto se oculta solo durante el render) y `--ancho/--alto`. Humo, niebla y cajas de volumen (nombre con «humo», «niebla», «fog»…, o material que solo tiene Volume) no cuentan para el encuadre y se ocultan en las vistas automáticas; `--con-niebla` los deja (con `--vista camara` y `video --camara fija` se dejan siempre).

**Fondos, suelos y estudios** salen en el render pero no cuentan para el encuadre (un ciclorama de 40 m dejaba el logo diminuto). Se reconocen:

- por la propiedad personalizada `buho_fondo = True` (en el objeto o en su malla), que es lo más seguro al modelar un escenario;
- por el nombre: Fondo, Suelo, Floor, Ground, Backdrop, Background, Estudio, Studio, Ciclorama, Cyc, Telón, Infinito, Piso;
- por la forma: un plano mucho más grande que el resto, una caja o cúpula que lo envuelve todo, o una losa grande por debajo.

El JSON dice cuáles en `fondo_fuera_del_encuadre`. Si la cámara quedaría al otro lado de la pared del estudio (en un giro, por detrás), se acerca y abre la lente (`camara_dentro_del_fondo.lente_mm`); si ni así cabe, esa pieza se oculta solo en ese render. `--encuadrar "A,B"` fuerza qué objetos se encuadran (vale en `captura`, `--vistas`, `giro`, `video` y `comparar`).

## Cómo trabajar

1. **Mira antes de tocar**: `estado`, `escena` y `stats`. No borres ni cambies objetos del usuario sin preguntar.
2. **El modelo vive en un script**: escribe el modelado como un `.py` guardado junto al proyecto (no código suelto), para poder rehacerlo y versionarlo. El script debe ser idempotente: borra solo lo que él creó (por colección propia) y lo vuelve a crear.
3. **Usa las librerías de `lib/`**: `import buho3d as b3` (piezas, materiales, modificadores), `import buho_sdf as S` (escultura), `import buho_rig as br` (esqueletos y animación), `import buho_apoyo` (pies en el suelo, cabeza estable) e `import buho_comparar` (comparar imágenes) funcionan en los dos modos: el CLI añade `lib/` al `sys.path` y las recarga en el Blender abierto si cambian. También se pueden importar módulos de la carpeta del script. El script corre con `__name__ == "__main__"` y `__file__` = su ruta; `--var NOMBRE=valor` le pasa variables globales.
4. **Devuelve datos con `result = {...}`**, no con `print`.
5. **Itera con `vigilar`**: deja `buho-blender vigilar modelo.py --captura c.png --vistas frente,derecha,tres_cuartos,arriba` en segundo plano; cada vez que guardas el .py (o `lib/buho3d.py`) lo vuelve a ejecutar y rehace la hoja. Cada pasada imprime una línea JSON con `ok`, `result` o `error`. `--veces N` para que acabe solo.
6. **Comprueba con los ojos**: una hoja de contactos (`--vistas`) y léela antes de dar algo por bueno. Si copias algo real (una foto, un logo, un producto), `comparar foto.jpg` antes de enseñarlo: mira la imagen y las métricas (ver «Comparar con una referencia»). La captura encuadra sola los objetos y pone cámara y luces temporales; no deja nada en la escena. Con `--vista camara` usa la cámara y la luz de la escena. `pantalla` enseña lo que ve el usuario en su ventana.
7. **Para enseñarlo**: `giro modelo.mp4` (o `.gif`) da una vuelta al modelo quieto; `video anim.mp4 --camara orbita` graba la animación mientras la cámara gira. Si va a tardar (Cycles, vídeos largos), añade `--en-cola` y sigue trabajando; avisa al usuario del progreso con `cola`.
8. **Exporta** a GLB para Unity (glTFast) o la web: Y arriba, escala aplicada, modificadores aplicados. Medidas reales en metros (`stats` da `tamano_m`).
9. **Dudas de la API**: `buho-blender docs "keyframe_insert"` antes de inventarte un parámetro.

## Esqueleto y animación (buho_rig)

**Esqueleto.** `esqueleto` crea un Armature `<Colección|Objeto>_Esqueleto` ajustado al tamaño y la forma (si ya existe, lo rehace). El personaje debe mirar hacia -Y (la vista «frente»), con Z arriba.

- **Humanoide** (por defecto): cadera, columna, pecho, cuello, cabeza, hombro/brazo/antebrazo/mano.L/.R, muslo/pierna/pie.L/.R. `.L` es el lado +X (la izquierda del personaje, como en Rigify).
  - Si las piezas tienen nombres reconocibles (Cabeza, Pelo, Torso, Manga, Brazo, Mano, Pierna, Zapato, Suela… en español o inglés), los huesos se colocan con sus cajas.
  - Detecta las figuras cabezonas tipo Funko (o se fuerzan con `--cabezon`).
  - Con una malla sin nombres, usa proporciones y la busca en los vértices (brazos y piernas).
- **Cuadrúpedo**: columna horizontal, cuello, cabeza, cola y cuatro patas (`muslo_del.L`, `pierna_tras.R`…). **Cadena**: `--n` huesos a lo largo del eje más largo (colas, tentáculos, cuerdas).
- **A mano**: `--huesos huesos.json` con `[{"nombre", "cabeza": [x,y,z], "cola": [x,y,z], "padre"}]` en metros.
- **Cómo se cuelga la malla**:
  - Una sola malla (o `--auto`): pesos automáticos (ARMATURE_AUTO). Si el cálculo falla (malla abierta), sale en `avisos`.
  - Piezas sueltas: cada pieza va al hueso que dice su nombre (o al más cercano), conservando su sitio. Las peanas, bases y suelos se quedan fuera.
  - `--sin-emparentar` solo crea los huesos.
- **Piezas rígidas**: en un modelo por piezas, si nada cuelga del antebrazo o de la pierna (p. ej. un Funko con un brazo de una pieza), esos huesos salen en `sin_doblar` y las animaciones no los giran, para que la mano no se separe del brazo.

**Acciones predefinidas** (`animar <accion>`): andar, punetazos, saludar (`--lado L|R`), saltar, bailar, idle (respirar), asentir, girar_cabeza.

- Cada una tiene una duración natural. Con `--segundos`, las que son ciclo (andar, puñetazos, bailar, idle) se repiten las veces que quepan.
- `--intensidad 0.5` las suaviza. `--bucle` añade un modificador Cycles para que se repitan solas.
- Cada llamada crea una **Action con nombre** (`--nombre`, o el de la acción) con usuario falso, así no se pierde.
- Sin `--nla` queda como acción activa y ajusta el rango de la escena. Con `--nla` se añade al final de la pista NLA «buho»: así se **encadenan** (`animar punetazos --nla` y luego `animar andar --segundos 2 --nla`) y la escena abarca todas.

**Animación a medida** (`animar --json poses.json`):

```json
{"nombre": "ondear", "bucle": true, "fps": 24, "espacio": "armature",
 "claves": [
  {"frame": 1,  "huesos": {"brazo.R": {"rot": [0, 110, 0]}, "cadera": {"loc": [0, 0, 0.02]}}},
  {"frame": 13, "interp": "SINE", "easing": "EASE_IN_OUT", "huesos": {"brazo.R": {"rot": [0, 130, 0]}}},
  {"frame": 25, "huesos": {"brazo.R": {"rot": [0, 110, 0]}}}]}
```

- `rot` va en grados, en los ejes del esqueleto en reposo (X derecha, Y atrás, Z arriba), aplicada en la articulación y relativa al hueso padre:
  - X negativo levanta un brazo hacia delante;
  - Y = -lado·ángulo lo abre hacia fuera (lado +1 para `.L`, -1 para `.R`);
  - X positivo dobla la rodilla.
- `loc` va en metros. `"espacio": "local"` usa los ejes propios de cada hueso.
- Interpolaciones: CONSTANT, LINEAR, BEZIER, SINE, QUAD, CUBIC, QUART, QUINT, EXPO, CIRC, BACK, BOUNCE, ELASTIC. Easing: AUTO, EASE_IN, EASE_OUT, EASE_IN_OUT.
- Los huesos que no estén en una clave vuelven al reposo en esa clave.

**Animaciones de fuera** (`importar-animacion x.fbx`):

- Importa el FBX (Mixamo u otro). Si hay un esqueleto en la escena (o `--armature`), pasa la animación a sus huesos por nombre (Mixamo → nombres de buho_rig, o nombres iguales) y borra lo importado (`--conservar` lo deja).
- La columna, el cuello, la cabeza, las manos y los pies copian el giro. En brazos y piernas se corrige además la pose de reposo (la T de Mixamo frente a brazos caídos).
- La cadera se desplaza en proporción a la altura; `--en-sitio` quita el avance.
- Sin esqueleto propio, deja el del FBX con su acción.
- **Cabeza estable** (`--cabeza auto|estable|libre`, `--estabilidad 0..1`, 0,7 por defecto): en figuras cabezonas (cabeza de más del 25 % de la altura, tipo Funko) una inclinación normal del cuerpo mueve una cabeza enorme. Con `auto` (por defecto), en cabezones se amortigua la inclinación de cadera, columna, pecho y cuello (el tronco queda al ~40 %) y la cabeza se queda casi derecha (~10 % de su inclinación), conservando el giro a los lados. En el andar de Mixamo sobre el Funko: cabeza de 30–43° a 1–4°.
- **Pies en el suelo** (`--pies`, ver abajo): la altura sobre el suelo la marca el esqueleto de origen, así un salto sigue siendo salto y al andar el pie que apoya toca el suelo.

**Pies en el suelo** (`animar` e `importar-animacion`, `lib/buho_apoyo.py`): después de crear la acción se ajusta la altura de la cadera en cada fotograma para que el punto más bajo del personaje toque el suelo, sin atravesar la peana ni flotar.

- El suelo se detecta solo: lo que haya justo debajo de los pies en reposo (peana, suelo, ciclorama, con rayos hacia abajo); si no hay nada, la altura de los pies en reposo. El JSON lo dice en `pies` (`suelo`, `suelo_z`, `ajuste_cm`).
- `--pies apoyar` (por defecto): siempre tocando el suelo. `no_atravesar` (por defecto en `saltar` y en `--json`): solo sube lo que se hunde. `no`: como sale.
- Ejemplo: `andar` en el Funko de Juan iba de −5,7 cm (la puntera dentro de la peana) a +1,8 cm (flotando); ahora 0 en todos los fotogramas.
- No es IK: los pies pueden patinar un poco en horizontal.

**Vídeo**: `video salida.mp4` renderiza el rango de la escena (o `--segundos` desde el inicio).

- Cámaras: `--camara fija` usa la de la escena (si no hay, una automática); `frontal` y `orbita` crean una temporal que encuadra la animación entera (muestrea 16 fotogramas). `--vueltas` vale para `orbita`.
- Luces: las de la escena si las hay; si no, de estudio (se fuerza con `--luces estudio|escena`).
- Motor: `--motor EEVEE|CYCLES|WORKBENCH`.
- No deja nada en la escena.
- **Revisa siempre** con `--hoja revision.png` (`--n 6`): lee la hoja antes de dar el vídeo por bueno.

## Comparar con una referencia

`buho-blender comparar foto.png --blend modelo.blend --vista camara --salida comp.png` (o en directo, sin `--blend`):

- Renderiza con el mismo aspecto que la referencia (`--alto 540`) y con fondo transparente (`--opaco` para dejar el del mundo).
- Guarda `comp.png` (referencia | render | superposición al 50 % | siluetas: rojo solo en la referencia, azul solo en el render, blanco las dos) y el render suelto en `comp_render.png`.
- **Métricas** (en el JSON):
  - `silueta`, si la referencia tiene fondo uniforme o transparencia (`--umbral` para la tolerancia del fondo). `iou` compara con el mismo encuadre; `iou_forma` compara la forma (cada silueta recortada a su caja); `proporcion_ancho_alto`, `escala_render_vs_ref` y `desplazamiento_pct` dicen cómo mover o escalar la cámara o el modelo.
  - `color_por_zonas`: color medio en una rejilla 3×3 sobre cada silueta (o la imagen entera) y su diferencia (RGB 0–255; menos de 25 se parece, más de 60 es muy distinto).
- Para la forma importa sobre todo que `iou_forma` y la proporción se acerquen; un `iou` bajo con `escala` lejos de 1 solo es encuadre.

## Renders en segundo plano (cola)

Cualquier `giro`, `video`, `captura`, `run` o `comparar` con `--en-cola` (o `--segundo-plano`) se lanza en un proceso aparte y devuelve al momento un id:

- Se renderiza con un Blender sin ventana, de uno en uno (los demás esperan su turno; `--paralelo` para no esperar).
- En directo (sin `--fondo`), primero se guarda una copia de la escena abierta y se trabaja sobre ella: el Blender del usuario queda libre. La copia se borra al acabar.
- `buho-blender cola`: lista con estado (`en_cola`, `corriendo`, `hecho`, `error`, `cancelado`, `perdido`), fotogramas hechos/total, porcentaje, tiempo transcurrido y restante estimado.
- `cola ver <id>`: lo mismo, más el resultado JSON de la orden y las últimas líneas de Blender. `cola esperar <id>`: imprime una línea cada vez que avanza y acaba con el resultado. `cola cancelar <id>` mata ese Blender. `cola limpiar` borra los terminados.
- Registro: `%LOCALAPPDATA%\Programs\BuhoBlender\trabajos\<id>.json` y `.log`.
- **Avisa al usuario**: al lanzar, di el id y cuánto calculas; consulta `cola` de vez en cuando y cuéntale el % y el tiempo restante; al acabar, revisa la salida (o su `--hoja`) antes de darla por buena.

## Esculpir formas orgánicas con buho_sdf

`lib/buho_sdf.py` esculpe con campos de distancia (SDF): las formas se suman como arcilla y salen como **una sola malla cerrada**. Numpy evalúa el campo en una rejilla y lo pasa por openvdb y «Volume to Mesh».

- **Cuándo usarlo frente a buho3d**:
  - buho3d: piezas duras y limpias (armas, muebles, Funkos por piezas, cosas que se exportan a un motor de juego con pocos polígonos).
  - buho_sdf: esculturas, animales, cuerpos, rocas, cualquier forma donde las partes se funden (cuello que nace del cuerpo, plumas talladas en la superficie).
  - Da mallas densas: para un juego, retopología o decimado después.
- **Piezas**:
  - Primitivas: `esfera`, `elipsoide`, `caja` (con `redondeo`), `capsula`, `tubo` (por puntos y radios), `toro`, `cilindro`, `plano`, `revolucion` (perfil).
  - Combinaciones: `a | b` (unión), `a - b` (resta), `a & b` (intersección), y en suave `.suave(b, k)`, `.restar_suave(b, k)`, `.cortar_suave(b, k)`, donde `k` es el radio de la fusión en metros.
  - Transformaciones: `.mover`, `.girar`, `.simetria_x`, `.desplazar`.
  - Detalle: `ruido` y `fbm` para superficie, `plumas(...)` como patrón de relieve.
- **Malla**: `S.malla(sdf, "Nombre", limites=((xmin,ymin,zmin),(xmax,ymax,zmax)), voxel=..., mascaras={"zona": sdf_zona}, col=col)`. Da límites ajustados (si no, los estima y tarda más).
- **Tantea con voxel grueso**: el tiempo crece con el cubo del detalle. Usa `voxel` 0,008–0,009 mientras pruebas formas (unos 5 s en una figura de 2 m) y 0,004–0,0045 solo para el final (~50 s). Si el script lee `VOXEL = globals().get("VOXEL", 0.005)`, se cambia sin editarlo: `buho-blender run modelo.py --var VOXEL=0.009`.
- **Materiales (mejor en Cycles)**:
  - `S.mascara(sdf, P, ancho)` define zonas, que `malla(..., mascaras=...)` guarda como atributos de vértice (0..1). Se leen con un nodo **Attribute** (nombre de la máscara → Fac) para mezclar materiales por zonas.
  - Para los relieves, **Geometry → Pointiness** con una rampa estrecha (p. ej. 0,56→0,64) pinta las aristas y las crestas (oro en lo que sobresale, sucio en lo hundido). Pointiness solo funciona en Cycles.
- **Ejemplo completo**: `modelos/buho_estatua/buho_esculpido.py`.

## Archivos y seguridad

- `cargar` y `nuevo` guardan antes el archivo actual si tiene ruta (Blender no marca como cambiado lo que se hace por script). Si la escena no tiene archivo y tiene objetos, paran: guárdala antes o usa `--descartar` si de verdad se puede perder.
- Con ventana, el cambio de archivo se hace justo después de contestar y el CLI espera a que acabe.
- `limpiar` borra datos sin usuarios (materiales sin asignar, mallas sueltas): no lo uses en el archivo del usuario sin avisar.
- `esqueleto` cambia el emparentado de las piezas del usuario: en un archivo suyo, trabaja sobre una copia o avisa antes.

## Consejos de modelado

- Construye con primitivas + modificadores (Bevel, Subdivision, Mirror, Solidify, Boolean) y aplica antes de exportar si el destino es un motor de juego.
- Materiales con Principled BSDF; colores en lineal. Para emisivos usa `Emission Strength` (glTF lo exporta).
- Origen y orientación según el destino: para un arma en primera persona de Unity, cañón hacia +Y de Blender (que pasa a +Z en Unity) y el origen en la empuñadura.
- Para animar después, modela el personaje mirando a -Y, con los brazos caídos o en T, y nombra las piezas por su parte del cuerpo.
- Polígonos razonables (`stats`): un arma de juego, 2.000–15.000 triángulos.
