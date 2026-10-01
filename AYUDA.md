# Ayuda de seerr-bridge 1.2.2

seerr-bridge es un **puente** entre **Seerr** (donde los clientes piden películas y series) y **XtreamFilter** (catálogo del proveedor IPTV y cola de descargas). Hace el trabajo que normalmente harían Radarr y Sonarr, pero usando el catálogo del proveedor.

Página de control: `http://IP-DEL-SERVIDOR:5056`

---

## 1. Qué hace, de principio a fin

```
Cliente pide "Duna" en Seerr  → la petición queda PENDIENTE
   ▼  (el puente revisa Seerr cada 5 minutos)
Puente busca por TMDB en el catálogo del proveedor (XtreamFilter)
   ├─ Hay versión ES (sin 4K)        → la APRUEBA en Seerr
   ├─ No existe en el catálogo        → la RECHAZA y avisa al cliente
   └─ Existe pero no en ES            → la RECHAZA y avisa al cliente
   ▼
Puente comprueba Jellyfin y la cola de XtreamFilter (no descarga lo que ya tienes)
   ▼
Añade la película / los episodios que faltan a la cola de XtreamFilter
(al principio de la cola si la opción está activa)
   ▼
XtreamFilter descarga → el puente ve en su cola que está "completado" (revisa cada minuto)
   ├─ Pide a Jellyfin que actualice la biblioteca
   └─ (opcional) Lo envía a shrinkerr y lo sube al principio de su cola
   ▼
Jellyfin lo muestra → Seerr lo marca "Disponible" → el puente la marca "Completada"
```

### De dónde saca el catálogo

De la **lista de títulos de XtreamFilter en formato Xtream Codes** (`player_api.php`, la misma que usan los reproductores). Esa lista ya viene **filtrada con las Filter Rules** de XtreamFilter: el puente solo ve los grupos que has dejado. Lo que no esté en esos grupos se trata como "no existe en el catálogo".

### Cómo elige la versión

Busca por el **número de TMDB** (no por el nombre: da igual que el título esté en inglés o en español) y elige, en este orden:

1. **Grupo ES** (nombre que empieza por `ES -`, `ES-DO -`…): se acepta directamente, sin analizar. En estos grupos el audio puede estar etiquetado como inglés pero es español.
2. **Grupo LA** (latino: `LA -`, grupos `LATINO …`): se acepta directamente.
3. **Otra versión con audio en español** (NF, D+, EN…): se analizan los archivos.
4. **Otra versión con subtítulos completos en español** (los forzados no cuentan).
5. Si no hay nada: no disponible en español (se rechaza o queda esperando).

Siempre sin 4K (si la regla está activa). Si hay varias válidas en un paso, la más reciente.

**Análisis (pasos 3 y 4)**: el puente lee las pistas de audio y subtítulos de cada versión con **ffprobe** (1-2 s por versión, como máximo 8 versiones por petición; en series, el primer episodio de la temporada pedida). Como el proveedor admite **una sola conexión**, si XtreamFilter está descargando, antes de analizar **pausa la cola** de XtreamFilter, espera 5 s y a que la descarga en curso se detenga, analiza, espera 15 s más (para que el proveedor libere la conexión) y la **reanuda** (solo si la pausó él). Si el análisis corta la conexión de la descarga que estaba en pausa y esta termina con error, se reintenta en la siguiente revisión (opción "reintentar las descargas con error"). El resultado del análisis de cada versión se recuerda 24 h, para no volver a pausar la cola por lo mismo (p. ej. al pulsar "Reprocesar"). En el detalle de la petición se indica el motivo, p. ej. *"NF-DO - Furioza (2021) | NETFLIX MOVIES DOLBY AUDIO (audio en español)"*.

### Cómo evita duplicados

Algo cuenta como "ya lo tienes" solo si:
- Está **en Jellyfin** (en el disco): películas por su número de TMDB; series episodio a episodio, en cualquier carpeta de esa serie, **o**
- está **en la cola** de XtreamFilter: en espera, descargando, con error (se reintenta en XtreamFilter, no se duplica) o completado (Jellyfin aún no lo ha visto).

El puente **no usa el historial** de XtreamFilter. Lo borrado del disco **se vuelve a descargar** si se pide en Seerr.

Aviso: una película que está en el disco pero que **Jellyfin no tiene identificada con TMDB** no se reconoce y podría volver a descargarse. Arréglala en Jellyfin con **Identificar**.

### Cómo sabe que una descarga ha terminado

Cada minuto mira en la **cola de XtreamFilter** lo que añadió:
- **Completado**: descargado. La cola da la ruta del archivo → actualiza Jellyfin y (si está activo) lo envía a shrinkerr.
- En espera / descargando / con error: sigue esperando.
- **Ya no está en la cola** (XtreamFilter la vacía al terminarla): lo busca en Jellyfin. Si está, se da por descargado (pero no se envía a shrinkerr arriba: su vigilante lo cogerá al final de su cola).
- **Películas**: se comprueban todas las versiones con el mismo TMDB.
- **Series**: episodio a episodio y solo las temporadas pedidas.
- **Si ya estaba en la cola** de XtreamFilter: no se añade otra vez; se "adopta" (se sube al principio y se sigue para Jellyfin/shrinkerr).

### Series en emisión

El puente pregunta a Seerr (TMDB) si la serie está **terminada** o **en emisión**:
- **Terminada**: se descarga lo pedido y ya.
- **En emisión**: además se añade al **seguimiento** de XtreamFilter para que descargue solo los episodios nuevos.

### Orden

El primero que pidió, primero: las peticiones se procesan de la más antigua a la más nueva, y lo pedido en Seerr queda por delante del resto de la cola, en ese orden. Lo que se está descargando nunca se interrumpe.

---

## 2. Página principal

### Cabecera

| Elemento | Qué es |
|---|---|
| **v1.0** | Versión del puente |
| **⚙ Ajustes** | Abre la configuración |
| **MODO PRUEBA / MODO REAL** | En prueba no aprueba, rechaza ni añade nada; solo muestra lo que haría |
| Última / Próxima revisión | Cuándo revisó Seerr y cuándo volverá a hacerlo |
| **Revisar ahora** | Revisa Seerr en ese momento (y reintenta lo que estaba esperando) |

### Peticiones

Estados:

| Estado | Significado |
|---|---|
| **Prueba** | Modo prueba: muestra lo que haría |
| **En cola** | Añadida a la cola de XtreamFilter, esperando descarga |
| **Convirtiendo** | Ya está en Jellyfin, pero shrinkerr aún no ha terminado de convertirla |
| **Completada** | Disponible en Jellyfin (y convertida, si shrinkerr está activo), o ya estaba descargada |
| **Esperando** | Sin versión válida por ahora; se reintenta cada 24 h |
| **Rechazada** | Rechazada en Seerr (no existe o no en el idioma) y cliente avisado |
| **Error** | Falló algo; se reintenta más tarde (ver el Registro) |

Debajo de cada petición puede aparecer el estado en shrinkerr: *en cola de conversión, convirtiendo, convertido, no necesitaba conversión, falló la conversión* (en series, un resumen: *"5 convertidos, 1 convirtiendo"*).

Botones:
- **Reprocesar**: vuelve a procesar esa petición desde cero.
- **Cancelar**: rechaza (si está pendiente) o borra la petición en Seerr, quita de la cola de XtreamFilter lo que el puente añadió y aún no se ha descargado, quita el seguimiento de la serie si lo creó el puente, y la borra de la lista. Lo que ya estaba en la cola antes de la petición no se quita. No se avisa al cliente.
- **Borrar completadas y rechazadas**: limpia la lista (no toca Seerr, colas ni shrinkerr). Las que están "Convirtiendo" no se borran.

### Episodios que faltan

Lista de series del disco a las que les faltan episodios que el proveedor sí tiene (ver apartado 4).

- **Buscar ahora**: hace la búsqueda en ese momento (tarda unos minutos).
- **Añadir a la cola**: añade los episodios que faltan de esa serie (al final de la cola).
- **Ignorar serie**: no se vuelve a buscar. Se puede deshacer con "dejar de ignorar".

### Registro

Lo que ha ido haciendo el puente. **Borrar registro** lo vacía (los registros del contenedor en Portainer no se tocan).

---

## 3. Ajustes (⚙)

### Seerr
| Ajuste | Explicación |
|---|---|
| Dirección | Dirección interna de Seerr, p. ej. `http://10.10.10.16:5055` |
| Clave de API | Seerr → Ajustes → General. Déjala vacía para conservar la guardada |

### Jellyfin
| Ajuste | Explicación |
|---|---|
| Dirección | Dirección interna de Jellyfin, p. ej. `http://10.10.10.10:8096` |
| Clave de API | Jellyfin → Panel de control → Claves de API |
| Actualizar la biblioteca al terminar cada descarga | Pide a Jellyfin una actualización cuando XtreamFilter termina una descarga (y cuando shrinkerr termina una conversión) |

### Lista de títulos (Xtream Codes)
| Ajuste | Explicación |
|---|---|
| Dirección | `player_api.php` de XtreamFilter, p. ej. `http://10.10.10.16:5000/merged/player_api.php` |
| Usuario / Contraseña | Los de esa lista (p. ej. `USER` / `PASS`). Se puede pegar la dirección completa con `?username=…&password=…` y se separan solos |

Es el catálogo que usa el puente (películas, series y episodios), ya filtrado con las Filter Rules. Si XtreamFilter cambia de puerto o empieza a pedir usuario y contraseña, solo hay que cambiarlo aquí.

### XtreamFilter (cola y seguimiento)
| Ajuste | Explicación |
|---|---|
| Dirección | Dirección interna de XtreamFilter, p. ej. `http://10.10.10.16:5000`. Se usa para añadir a la cola, ver su estado y crear seguimientos (el formato Xtream Codes no tiene esas funciones) |

### Reglas
| Ajuste | Explicación |
|---|---|
| Idioma (prefijo en el catálogo) | `ES` = solo versiones cuyo nombre empieza por `ES -` (o `ES-DO -`, etc.) |
| Excluir versiones 4K | No elige versiones `4K-…` ni grupos ⁴ᴷ |
| Poner lo pedido en Seerr al principio de la cola | Adelanta lo pedido a todo lo que espera en XtreamFilter |
| Buscar otras versiones con audio en español | Si no hay versión ES ni LA, analiza las demás versiones (pausando la cola unos segundos) y elige una con audio en español |
| …o con subtítulos completos en español | Si ninguna tiene audio en español, acepta una con subtítulos completos en español |
| En cada revisión, reintentar las descargas con error | En cada revisión (cada "Revisar Seerr cada (minutos)"), mira la cola de XtreamFilter y reintenta todo lo que esté con error, cancelado o con fallo al mover, sea de Seerr o no |
| Reintentos por descarga | Cuántas veces se reintenta cada elemento (por defecto 3). Al agotarse se deja con error y se anota en el registro (y en el Detalle si es de Seerr). Los contadores se guardan aunque el puente se reinicie |
| Series en emisión: añadir al seguimiento | Crea el seguimiento en XtreamFilter para bajar episodios nuevos |
| Reintentar las peticiones de Seerr no encontradas cada (horas) | Cada cuánto se vuelven a buscar las peticiones de Seerr que quedaron "Esperando" o con error (no afecta a la búsqueda de episodios que faltan) |
| Revisar Seerr cada (minutos) | Frecuencia de la revisión normal |
| Rechazar en Seerr lo que no esté disponible | Si está desactivado, las pendientes sin versión válida quedan esperando en vez de rechazarse |
| **Modo prueba** | No aprueba, rechaza ni añade nada; solo muestra lo que haría |

### Episodios que faltan
| Ajuste | Explicación |
|---|---|
| Buscar episodios que faltan | Activa la búsqueda automática, una vez al día |
| Hacer la búsqueda diaria entre las … y las … | Ventana horaria (hora local del puente, con AM/PM; por defecto 5:05 PM a 11:00 PM). La búsqueda se hace en la primera revisión que cae dentro de la ventana; si ese día no pudo hacerse dentro, espera a la ventana del día siguiente. "Buscar ahora" funciona a cualquier hora. Las peticiones de Seerr no dependen de esta ventana |
| Añadir automáticamente a la cola | Si está activo, lo encontrado se añade solo; si no, solo se muestra en la lista |

### Shrinkerr
| Ajuste | Explicación |
|---|---|
| Enviar lo pedido en Seerr a shrinkerr… | Al terminar la descarga, lo manda a shrinkerr y lo sube al principio de su cola |
| Dirección | Dirección interna de shrinkerr, p. ej. `http://10.10.10.16:6680` |
| Clave de API (opcional) | Solo si activas la autenticación en shrinkerr |
| Traducción de rutas | Cómo ve shrinkerr las carpetas de XtreamFilter, una por línea: `/downloads/movies = /media/hdd1/movies` |

### Mensajes al cliente (aviso en Jellyfin)
| Ajuste | Explicación |
|---|---|
| Avisar al cliente en Jellyfin cuando se rechaza | Muestra un aviso emergente en su Jellyfin |
| Guardar el aviso si no está conectado (días) | Si no está conectado, se muestra la próxima vez que abra Jellyfin (hasta N días) |
| Título del aviso | Por defecto "Solicitud rechazada" |
| Cuando no existe en el catálogo / Cuando no está en el idioma | Textos del aviso. Se sustituyen solas: `{titulo}`, `{usuario}`, `{tipo}` (película/serie) |

Todos los ajustes tienen un botón **Probar conexión** (Seerr, Jellyfin, XtreamFilter, shrinkerr). Pulsa **Guardar ajustes** para aplicarlos.

---

## 4. Búsqueda de episodios que faltan

Para cada serie que hay en Jellyfin (es decir, en el disco):

```
Episodios que tiene el proveedor (misma versión que la carpeta)
  − los que están en Jellyfin (disco)
  − los que están en la cola de XtreamFilter (en cualquier estado, también completados)
  = episodios que faltan (huecos y temporadas enteras)
```

- **Solo series con episodios**: las carpetas vacías (sin ningún episodio en Jellyfin) no se revisan.
- **Pausa la cola durante la búsqueda**: hay que pedir al proveedor la ficha de cada serie (~200 consultas) y el proveedor admite una sola conexión, así que, **si XtreamFilter está descargando**, la cola se pausa mientras dura (unos 2-3 minutos) y se reanuda al terminar. Si no está descargando (p. ej. fuera de su horario de descargas), no se toca la cola. Si el proveedor falla 5 veces seguidas, la búsqueda se cancela sin tocar las listas.
- **Añadir automáticamente**: si la opción está activada, al terminar cada búsqueda se añade a la cola todo lo encontrado (al final de la cola).
- **Series terminadas y completas**: si una serie está **terminada** según TMDB (Seerr) y la búsqueda no le encuentra nada que falte, se marca como **completa** y no se vuelve a revisar durante **7 días** (así la búsqueda consulta muchas menos series y la pausa de la cola es más corta). Las series en emisión, o terminadas con huecos, se revisan siempre. En la página se ve cuántas hay y el enlace **"revisar todas en la próxima búsqueda"** las vuelve a incluir. Si a una serie marcada se le borra un episodio, se detecta en la revisión semanal.

- **Misma versión que la carpeta**: si la carpeta es `EN - Lioness`, busca en `EN - Lioness`. Nunca adivina: si el nombre del catálogo no coincide con la carpeta (salvo año/país), la serie aparece como "sin versión en el catálogo". También aparecen así las series de grupos que has quitado con las Filter Rules.
- **También lo borrado**: un episodio que se descargó antes y ya no está en el disco se detecta como que falta. (Maintainerr borra series enteras: al desaparecer de Jellyfin la serie deja de revisarse, así que no se vuelve a descargar.)
- Lo añadido va **al final** de la cola y **no** se sube en shrinkerr (lo coge su vigilante como cualquier descarga).
- En cada búsqueda la lista se rehace: aparecen los episodios nuevos del proveedor y desaparece lo ya descargado.
- Las series que ya no existen (borradas del disco) se quitan solas de la lista y de las ignoradas.

---

## 5. Avisos a los clientes

- **Aviso en Jellyfin** (del puente): emergente, con el motivo exacto del rechazo. Solo lo muestran algunas aplicaciones: navegador (Jellyfin Web), Android, Android TV y WebOS normalmente sí; **Roku no**. Si el cliente no está conectado, se guarda hasta que se conecte (máximo los días configurados).
- **Aviso de Seerr (web push)**: Seerr envía "Tu solicitud … ha sido rechazada" a los clientes que tengan activadas las notificaciones en su perfil de Seerr. El texto es el de Seerr.

---

## 6. Limitaciones conocidas

- La **aprobación automática** de Seerr impide rechazar: las peticiones del administrador (y de usuarios con "Aprobación automática") no se pueden rechazar; quedan "Esperando".
- La búsqueda por TMDB depende de que el proveedor tenga bien puesto el número de TMDB en su catálogo.
- La búsqueda de episodios que faltan depende de que Jellyfin tenga la biblioteca actualizada (lo recién descargado que Jellyfin aún no ha visto sigue en la cola como "completado", así que no se duplica).
- La página no tiene contraseña: úsala solo dentro de tu red.

---

## 7. Solución de problemas

| Problema | Qué revisar |
|---|---|
| Aviso rojo "Falta la clave de API de Seerr" | ⚙ Ajustes → Seerr → Clave de API |
| Las peticiones de clientes no se procesan | Que estén **pendientes** o **aprobadas** en Seerr; que no esté en **Modo prueba** |
| Una petición se queda "Esperando" | El proveedor no la tiene en el idioma elegido; se reintenta cada 24 h (o "Revisar ahora") |
| No se descarga nada | Las descargas de XtreamFilter pueden estar pausadas o con la descarga programada desactivada |
| No llega el aviso en Jellyfin | La aplicación del cliente no lo admite (Roku) o no se ha conectado aún |
| shrinkerr no recibe los archivos | Opción activada, "Probar conexión" en verde y traducción de rutas correcta |
| "Error" en una petición | Mira el **Registro**; pulsa **Reprocesar** cuando esté resuelto |

Los registros completos del contenedor están en Portainer → Containers → `seerr-bridge` → **Logs**.

---

## 8. Archivos

| Ruta | Contenido |
|---|---|
| `/opt/seerr-bridge/app/bridge.py` | Programa |
| `/opt/seerr-bridge/app/index.html` | Página web |
| `/opt/seerr-bridge/data/settings.json` | Ajustes y claves de API |
| `/opt/seerr-bridge/data/bridge.db` | Base de datos del puente |
