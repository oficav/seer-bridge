# Historial de cambios

Versiones de seerr-bridge, de la más reciente a la más antigua. La **versión 1** publicada corresponde a la **1.2.2**. La **versión 2** corresponde a la **1.2.8**.

## 1.2.8

#### Cambios en la búsqueda de episodios que faltan
- **Antes**: si hay algo en la cola (en espera o descargando) y no está pausada, se pausa y se **esperan 15 s** para que la conexión quede libre.
- **Después** (aunque la búsqueda falle a mitad), solo si la pausó el puente:
  1. Se **esperan 15 s**.
  2. Si antes de empezar había una **descarga en curso**, se **reintentan las descargas con error** según la configuración ("Reintentar las descargas con error" y "Reintentos por descarga"; cuentan para el límite).
  3. Se **reanuda** la cola.
- Si la cola la habías pausado tú, no se toca.

#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor.

## 1.2.7

#### Cambios
- Búsqueda de episodios que faltan: **ya no se aplaza** si XtreamFilter está descargando (la prueba real mostró que una descarga en curso no impide las consultas de catálogo; los fallos anteriores eran por el bloqueo de IP del proveedor).
- En su lugar, **se pausa la cola de XtreamFilter mientras dura la búsqueda**, porque XtreamFilter arranca descargas por su cuenta cada minuto dentro de su horario. Si había una descarga a medias, queda en pausa (no se cancela). Al terminar, la cola se reanuda (solo si la pausó el puente; si la pausaste tú, se queda pausada). Así "Buscar ahora" es seguro a cualquier hora.

#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor.

## 1.2.6

#### Motivo
La búsqueda de episodios que faltan hizo ~40 consultas en 26 s al proveedor y **el proveedor bloqueó la IP** del servidor (comprobado: desde otra IP funcionaba).

#### Cambios en la búsqueda de episodios que faltan
- **Solo se consultan las series que Seerr marca como parcialmente disponibles**; las completas según Seerr no se consultan (sustituye a la memoria de "series terminadas y completas" de la 1.2.0).
- Las series **sin TMDB** en Jellyfin no se procesan y aparecen en la lista como **"FALTA TMDB"**.
- **Una consulta cada 5 s** (antes 0,2 s).
- Si el proveedor falla **2 veces seguidas** (antes 5) se cancela la búsqueda sin tocar las listas.
- Si XtreamFilter **está descargando algo**, la búsqueda no se hace y se muestra **"Descarga en curso en XtreamFilter. Búsqueda APLAZADA"** (no se cancela ninguna descarga). Dentro de la ventana horaria se reintenta en la siguiente revisión.
- En la página: "Series completas según Seerr (no se consultan al proveedor): N".

#### Otros
- Si al reintentar una descarga cancelada XtreamFilter ya la había vuelto a poner en espera, se da por buena y se anota en el registro.

#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor.

## 1.2.5

#### Cambios
- **Se cancela la descarga a medias en lugar de solo pausar la cola**, siempre que el puente necesita consultar al proveedor (análisis de audio/subtítulos, episodios de una serie pedida en Seerr y búsqueda de episodios que faltan). Motivo: una descarga a medias ocupa la única conexión con el proveedor **aunque esté en pausa** (el proveedor rechazaba las consultas o cortaba la descarga).
  1. Pausa la cola (para que no empiece la siguiente descarga).
  2. Cancela la descarga a medias (`/api/cart/cancel`) → la conexión queda libre.
  3. Hace las consultas.
  4. Reintenta la descarga cancelada (`/api/cart/{id}/retry`): vuelve a "en espera" en su sitio y empieza de cero. No cuenta para "Reintentos por descarga".
  5. Reanuda la cola (solo si la pausó el puente; si la pausaste tú, se queda pausada).
- Si no hay ninguna descarga a medias, no se toca nada.
- Se quitan las esperas de "5 s + comprobar progreso" y de 15 s antes de reanudar; queda una espera de 3 s tras cancelar.

#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor.

## 1.2.4

#### Cambios
- El **Detalle** (y el registro) indican siempre por qué se eligió la versión, también cuando es por prefijo:
  - *"Añadida a la cola: ES-DO - Puñales por la espalda… | ES - PELÍCULAS ᴰᴼᴸᴮʸ ᴬᵁᴰᴵᴼ (prefijo «ES -» en el grupo)"*
  - *"(prefijo «ES -» en el título)"*, *"(audio SPA)"*, *"(subtítulos SPA)"*.
- Si coincide un prefijo no se analiza el audio (sin cambios): el prefijo basta.

#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor.

## 1.2.3

#### Cambios
- **Idioma preferido configurable** (⚙ Ajustes → Reglas), sustituye a "Idioma (prefijo en el catálogo)" y a la regla fija de latino:
  - **Prefijos** (p. ej. `"ES -","LA -","ESP","ES-"`): se acepta directamente toda versión cuyo título o grupo empiece por uno de ellos, tal cual están escritos. El orden es la preferencia.
  - **Idiomas del audio** (p. ej. `SPA`) y **de los subtítulos** (p. ej. `SPA`): códigos separados por comas, con sugerencias en la página.
  - **Qué pista de audio cuenta**: cualquier pista / primero la principal, luego cualquiera / solo la principal.
- Orden de búsqueda: prefijo → (si no) audio → (si no) subtítulos → (si no) rechazar.
- Textos del registro y del Detalle con los códigos configurados (p. ej. "audio SPA ✓").
- Las pistas analizadas se recuerdan 24 h y se evalúan con los ajustes vigentes (cambiar los códigos no obliga a volver a analizar).

#### Cambio de comportamiento
- Con el valor inicial `"ES -","LA -","ESP","ES-"` funciona como antes y además acepta directamente `ES-DO - …` y los grupos `ESPAÑA …`.

#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor. No hay que tocar el stack. Los nuevos ajustes toman su valor inicial.

## 1.2.2

#### Cambios
- La ventana horaria de la búsqueda de episodios que faltan se elige con **AM/PM** (desplegables de hora 1–12, minutos en pasos de 5 y AM/PM). Internamente se sigue guardando igual: la ventana configurada se conserva (17:05–23:00 aparece como 5:05 PM – 11:00 PM).
- Etiqueta más clara: **"Reintentar las peticiones de Seerr no encontradas cada (horas)"** (solo afecta a peticiones de Seerr en "Esperando" o con error, no a la búsqueda de episodios que faltan).

#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor. No hay que tocar el stack ni los ajustes.

## 1.2.1

#### Cambios
- Nuevo ajuste: **ventana horaria** para la búsqueda diaria de episodios que faltan (por defecto 17:05 a 23:00, hora local). Se hace una vez al día dentro de la ventana; "Buscar ahora" funciona a cualquier hora. Las peticiones de Seerr no dependen de la ventana.
- **No se pausa la cola** si XtreamFilter no está descargando (análisis de audio y búsqueda de episodios).
#### Instalación
- Solo copiar el programa (`app/`) a `/opt/seerr-bridge/app` y reiniciar el contenedor.

## 1.2.0

#### Cambios
- Series **terminadas y completas**: si una serie está terminada según TMDB y no le falta nada, no se revisa durante 7 días. Enlace "revisar todas en la próxima búsqueda".

## 1.1.4

#### Cambios
- La búsqueda de episodios que faltan pausa la cola mientras consulta al proveedor (~200 consultas) y la reanuda al terminar.
- Si el proveedor falla 5 veces seguidas, la búsqueda se cancela sin tocar las listas.

## 1.1.3

#### Cambios
- En cada revisión se reintentan **todas** las descargas con error de la cola de XtreamFilter (de Seerr o no).
- Nuevo ajuste **Reintentos por descarga** (por defecto 3). Los contadores se guardan aunque el puente se reinicie.
- Se quitan las revisiones especiales de "30 s y 2 min tras reanudar".

## 1.1.2

#### Cambios
- La columna **Detalle** muestra el resultado de cada paso (p. ej. "Sin versión ES ni LA. Analizada 1 versión: … (sin audio ni subtítulos en español)").

## 1.1.1

#### Cambios
- Tras analizar se esperan 15 s antes de reanudar la cola (el proveedor libera la conexión).
- Tras reanudar, se revisa la cola a los 30 s y a los 2 min y se reintenta lo que tenga error.
- El resultado del análisis de cada versión se recuerda 24 h (no se vuelve a pausar la cola por lo mismo).

## 1.1.0

#### Cambios
- Nueva elección de versión: 1) grupo ES  2) grupo LA  3) otra versión con **audio en español**  4) otra con **subtítulos completos en español** (análisis con ffprobe, máximo 8 versiones).
- Antes de analizar se pausa la cola de XtreamFilter (el proveedor admite una sola conexión) y después se reanuda.
- Nuevas opciones en Reglas: buscar otras versiones con audio en español / con subtítulos.
#### Instalación
- **Requiere actualizar el stack**: se quita `user: "1000:1000"` y la orden de arranque instala ffprobe y lanza el puente como usuario 1000 (ver docker-compose.yml).

## 1.0.4

#### Cambios
- Reintento automático (hasta 3 veces) de lo pedido en Seerr que quede con error en la cola de XtreamFilter.

## 1.0.3

#### Cambios
- Se deja de usar el **historial** de XtreamFilter: para saber si una descarga terminó se mira la **cola** (estado "completado" con su ruta) y, si ya no está en la cola, Jellyfin.
- "Ya lo tienes" = está en Jellyfin o en la cola (también lo que tiene error, para no duplicarlo).
#### Instalación
- Solo copiar el programa y reiniciar.

## 1.0.2

#### Cambios
- Nuevo apartado de ajustes **Lista de títulos (Xtream Codes)**: dirección `player_api.php`, usuario y contraseña. El catálogo (películas, series, episodios) se lee de esa lista, ya filtrada con las Filter Rules de XtreamFilter.
- El apartado XtreamFilter queda para cola, historial y seguimiento.

## 1.0.1

#### Cambios
- Búsqueda de episodios que faltan: ya no se ocultan los episodios que estén en el historial de XtreamFilter (solo los descargados en las últimas 24 h).
- Solo se revisan las series que tienen al menos un episodio en Jellyfin (las carpetas vacías se saltan).

## 1.0

#### Funciones
- Puente entre Seerr y XtreamFilter con página web de control (puerto 5056) y modo prueba.
- Peticiones pendientes: aprueba si hay versión ES (sin 4K) en el catálogo del proveedor; rechaza si no existe o no está en español y avisa al cliente en Jellyfin (mensajes personalizables).
- Peticiones aprobadas: añade la película o los episodios que faltan a la cola de XtreamFilter, al principio de la cola; las series en emisión se añaden al seguimiento de XtreamFilter.
- Botones Reprocesar, Cancelar, Borrar completadas y rechazadas, Borrar registro.
- Shrinkerr: envía lo pedido en Seerr al terminar la descarga y lo sube al principio de su cola; estado "Convirtiendo".
- Búsqueda de episodios que faltan (comparando proveedor, Jellyfin, historial y cola), con lista, añadir a la cola e ignorar serie.
- Ajustes en pantalla aparte, colores por filas (Conexiones / Opciones).
- Corrección: una petición de Seerr vuelve a descargar lo que se descargó antes pero ya no está en el disco.
