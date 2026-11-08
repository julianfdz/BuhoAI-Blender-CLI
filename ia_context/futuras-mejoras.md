# buho-blender · futuras mejoras

Ideas pendientes, apuntadas el 08/11/2026. Lo que ya está en marcha (encuadre sin fondos, pies en el suelo, cabeza estable con Mixamo, `comparar`, cola de renders, `optimizar`, `imprimir`, `visor`) no va aquí.

## Principio
La herramienta base es **general**: tiene que servir para hacer cualquier cosa (armas, estatuas, escenarios, logos, personajes, props). Los estilos concretos (Funko, low-poly, estatua de bronce…) son **una capa de adaptación por encima** (preajustes que el CLI sabe cargar), no parte del núcleo.

## Capa de estilos y preajustes (adaptabilidad)
- **Estilos**: paquetes de reglas + materiales + proporciones (p. ej. «vinilo coleccionable», «bronce con pátina», «low-poly juego», «realista»). `buho-blender estilo aplicar <nombre>` y que los scripts los puedan pedir.
- **Librería de piezas reutilizables** por estilo: cabezas, pelos (liso, corto, rizado, coleta), gafas, ropa, manos con gestos, ojos y bocas con expresiones; versionadas y con miniatura.
- **Estudios ya hechos**: «estudio blanco», «noche con humo», «producto premium», «escaparate»; cámara + luces + fondo (marcado como fondo para que no cuente al encuadrar).

## Animación y vídeo
- Expresiones faciales y labios (parpadeo, sonrisa, hablar) con shape keys o huesos de cara.
- Escenas con varios personajes que interactúan (p. ej. dos figuras peleando), con coreografía en JSON.
- Retarget de dedos y mejor roll de huesos (compatible con Rigify).

## Flujo de trabajo para proyectos grandes (videojuego)
- **Puente con Unity** como el de Blender: un script de editor dentro del proyecto que escucha en localhost y acepta órdenes (refrescar assets, ejecutar BZSetup, entrar en Play, capturas, compilar) para trabajar con el Unity **abierto**, sin el modo batch que choca con el editor.
- **Tablero del proyecto**: una interfaz (app Búho o página) con la biblia del juego (historia, estilo, mapas), lista de assets con estado y miniaturas renderizadas solas, y comentarios del usuario por asset que el agente lee y responde. Que deje de estar todo «en docs de contexto y en la cabeza».
- Pipeline asset → juego: `optimizar` + exportar directo a la carpeta de Unity + registro en el tablero.
