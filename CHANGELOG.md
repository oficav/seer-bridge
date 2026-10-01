# Historial de cambios

Versiones de seerr-bridge, de la más reciente a la más antigua. La **versión 1** publicada corresponde a la **1.2.2**.

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
