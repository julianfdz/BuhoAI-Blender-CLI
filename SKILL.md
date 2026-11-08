---
name: buho-blender
description: Modelar, editar, fotografiar, animar en giro, medir, importar y exportar modelos 3D en Blender con el CLI `buho-blender`, en directo en el Blender abierto (se ve lo que haces) o sin ventana para generar en lote. Úsala SIEMPRE que haya que crear o retocar un modelo 3D, un arma, un prop, un personaje o un escenario, sacar una captura, una hoja de vistas o un vídeo/GIF girando de un .blend, contar polígonos, importar un GLB/FBX/OBJ/STL, exportar a GLB/FBX/OBJ para Unity o la web, buscar algo en la API de Blender o revisar qué hay en una escena, aunque no se nombre el CLI. Vale para cualquier agente con terminal (Claude, Codex, Copilot, Gemini, scripts).
---

# buho-blender

CLI propio de Búho AI para trabajar con Blender desde un agente. Todo sale en JSON (`--json` para una sola línea). Dos modos:

- **En directo** (por defecto): habla con el Blender abierto por el puerto 9876 del complemento oficial «MCP» de Blender Lab (`--puerto N` para otro). Lo que haces aparece en la ventana al momento. Si no hay ningún Blender abierto: `buho-blender abrir`.
- **Sin ventana** (`--fondo`): lanza `blender -b` con la escena de fábrica, o con `--archivo x.blend`. Para generar y exportar en lote sin tocar lo que el usuario tiene abierto. `--guardar x.blend` guarda al acabar.

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
buho-blender giro giro.mp4 [--segundos 4 --fps 24 --vista tres_cuartos]  # vuelta de 360°; también .gif
buho-blender pantalla vista.png [--render]   # la vista 3D del Blender abierto tal como se ve (--render: sin interfaz)

# Hacer
buho-blender run script.py               # ejecuta Python (bpy); también "código" o - (entrada). Devuelve result = {...}
buho-blender vigilar script.py --captura c.png --vistas frente,tres_cuartos   # re-ejecuta al guardar; Ctrl+C
buho-blender importar pieza.glb [--en Coleccion]   # .glb .gltf .fbx .obj .stl .ply .usd; .blend = append
buho-blender importar otro.blend --objetos "A,B"    # o --colecciones "X,Y"
buho-blender exportar salida.glb         # o .fbx / .obj; --objetos "A,B" o --coleccion X; --unir = una sola malla
buho-blender limpiar                     # purga datos huérfanos

# Archivos
buho-blender nuevo [salida.blend] [--vacia]   # escena de inicio sin mallas (--vacia: sin cámara ni luz)
buho-blender cargar archivo.blend        # abre un .blend en el Blender abierto
buho-blender guardar archivo.blend
buho-blender abrir [archivo.blend]

# Ayuda
buho-blender docs "bmesh bevel" [--api | --manual]   # busca en la documentación local de la API y del manual
```

Opciones comunes: `--fondo`, `--archivo x.blend`, `--guardar x.blend`, `--texto` (muestra también lo que imprima el script), `--puerto N`, `--json`, `--timeout <s>` (600). Las capturas y el giro aceptan `--objetos "A,B"` o `--coleccion X` (el resto se oculta solo durante el render), `--ancho/--alto` y `--transparente` (captura).

## Cómo trabajar

1. **Mira antes de tocar**: `estado`, `escena` y `stats`. No borres ni cambies objetos del usuario sin preguntar.
2. **El modelo vive en un script**: escribe el modelado como un `.py` guardado junto al proyecto (no código suelto), para poder rehacerlo y versionarlo. El script debe ser idempotente: borra solo lo que él creó (por colección propia) y lo vuelve a crear.
3. **Usa la librería `buho3d`** (`lib/buho3d.py` del repo): `import buho3d as b3` funciona en los dos modos (el CLI añade `lib/` al `sys.path` y la recarga en el Blender abierto si cambia). También se pueden importar módulos de la carpeta del script. El script corre con `__name__ == "__main__"` y `__file__` = su ruta.
4. **Devuelve datos con `result = {...}`**, no con `print`.
5. **Itera con `vigilar`**: deja `buho-blender vigilar modelo.py --captura c.png --vistas frente,derecha,tres_cuartos,arriba` en segundo plano; cada vez que guardas el .py (o `lib/buho3d.py`) lo vuelve a ejecutar y rehace la hoja. Cada pasada imprime una línea JSON con `ok`, `result` o `error`. `--veces N` para que acabe solo.
6. **Comprueba con los ojos**: una hoja de contactos (`--vistas`) y léela antes de dar algo por bueno. La captura encuadra sola los objetos y pone cámara y luces temporales; no deja nada en la escena. Con `--vista camara` usa la cámara y la luz de la escena. `pantalla` enseña lo que ve el usuario en su ventana.
7. **Para enseñarlo**: `giro modelo.mp4` (o `.gif`) da una vuelta completa con la luz acompañando a la cámara.
8. **Exporta** a GLB para Unity (glTFast) o la web: Y arriba, escala aplicada, modificadores aplicados. Medidas reales en metros (`stats` da `tamano_m`).
9. **Dudas de la API**: `buho-blender docs "keyframe_insert"` antes de inventarte un parámetro.

## Archivos y seguridad

- `cargar` y `nuevo` guardan antes el archivo actual si tiene ruta (Blender no marca como cambiado lo que se hace por script). Si la escena no tiene archivo y tiene objetos, paran: guárdala antes o usa `--descartar` si de verdad se puede perder.
- Con ventana, el cambio de archivo se hace justo después de contestar y el CLI espera a que acabe.
- `limpiar` borra datos sin usuarios (materiales sin asignar, mallas sueltas): no lo uses en el archivo del usuario sin avisar.

## Consejos de modelado

- Construye con primitivas + modificadores (Bevel, Subdivision, Mirror, Solidify, Boolean) y aplica antes de exportar si el destino es un motor de juego.
- Materiales con Principled BSDF; colores en lineal. Para emisivos usa `Emission Strength` (glTF lo exporta).
- Origen y orientación según el destino: para un arma en primera persona de Unity, cañón hacia +Y de Blender (que pasa a +Z en Unity) y el origen en la empuñadura.
- Polígonos razonables (`stats`): un arma de juego, 2.000–15.000 triángulos.
