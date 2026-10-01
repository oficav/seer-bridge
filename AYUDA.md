# Ayuda de seerr-bridge 1.2.8

seerr-bridge es un **puente** entre **Seerr** (donde los clientes piden películas y series) y **XtreamFilter** (catálogo del proveedor IPTV y cola de descargas). Hace el trabajo que normalmente harían Radarr y Sonarr, pero usando el catálogo del proveedor.

Página de control: `http://IP-DEL-SERVIDOR:5056`

---

## 1. Qué hace, de principio a fin

```
Cliente pide "Duna" en Seerr  → la petición queda PENDIENTE
   ▼  (el puente revisa Seerr cada 5 minutos)
Puente busca por TMDB en el catálogo del proveedor (XtreamFilter)
   ├─ Hay versión en tu idioma (sin 4K) → la APRUEBA en Seerr
   ├─ No existe en el catálogo          → la RECHAZA y avisa al cliente
   └─ Existe pero no en tu idioma       → la RECHAZA y avisa al cliente
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

Busca por el **número de TMDB** (da igual que el título esté en inglés o en español) y, entre las versiones encontradas (siempre sin 4K si la regla está activa):

```
¿Alguna versión empieza por un prefijo del idioma preferido? (título o grupo)
   ├─ Sí → se elige y se deja de buscar (no se analiza nada)
   └─ No → ¿alguna tiene audio en los idiomas indicados (p. ej. SPA)?
             ├─ Sí → se elige
             └─ No → ¿alguna tiene subtítulos completos en los idiomas indicados?
                       ├─ Sí → se elige
                       └─ No → se rechaza (o queda "Esperando" si es del administrador)
```

- **Prefijos** (p. ej. `"ES -","LA -","ESP","ES-"`): se acepta toda versión cuyo título o grupo **empiece** por uno de ellos, tal cual están escritos. Si varias coinciden, gana el prefijo que está antes en la lista y después la más reciente.
- **Audio y subtítulos**: se leen las pistas de cada versión con **ffprobe** (1-2 s por versión, máximo 8 por petición; en series, el primer episodio de la temporada pedida). Los códigos (`SPA`, `ES`…) se comparan con el idioma de cada pista, sin distinguir mayúsculas. Según el ajuste, cuenta **cualquier pista**, **primero la principal** o **solo la principal** (la marcada por defecto, o la primera).
- **Conexión con el proveedor**: el proveedor admite una sola conexión y una descarga a medias la ocupa **aunque esté en pausa**. Por eso, si hay una descarga a medias, antes de consultar al proveedor el puente pausa la cola (para que no empiece otra), **cancela** esa descarga, hace sus consultas, la **reintenta** (vuelve a "en espera" en su sitio y empieza de cero; no cuenta como reintento) y reanuda la cola si la pausó él. Si la cola la pausaste tú, la deja pausada. Si no hay ninguna descarga a medias, no toca nada. Las pistas de cada versión se recuerdan 24 h.
- En el **Detalle** de la petición siempre se indica el motivo de la elección: *"(prefijo «ES -» en el grupo)"*, *"(audio SPA)"* o *"(subtítulos SPA)"*, y si hubo análisis, su resultado, p. ej. *"Sin versión con prefijo del idioma preferido. Analizada 1 versión: NF-DO - Furioza (audio SPA ✓)"*. Si coincide un prefijo no se analiza el audio (el prefijo basta).

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
| Idioma preferido: prefijos | Lista entre comillas y separada por comas (p. ej. `"ES -","LA -","ESP","ES-"`). Toda versión cuyo título o grupo **empiece** por uno de estos textos, tal cual, se acepta sin analizar. El orden es la preferencia. `"ES"` incluye `ES - …`, `ES-DO - …` y grupos `ESPAÑA …` (y cualquier cosa que empiece por ES); `"ES -"` es más estricto |
| Idiomas del audio | Códigos de idioma de la pista de audio, separados por comas (p. ej. `SPA` o `SPA, ES`). Sugerencias: SPA/ES español, ENG/EN inglés, FRE/FRA francés, POR/PT portugués, ITA/IT italiano, GER/DEU alemán |
| Qué pista de audio cuenta | Cualquier pista · Primero la principal, luego cualquiera · Solo la principal |
| Idiomas de los subtítulos | Igual que el audio; solo cuentan los subtítulos completos (no forzados) |
| Excluir versiones 4K | No elige versiones `4K-…` ni grupos ⁴ᴷ |
| Poner lo pedido en Seerr al principio de la cola | Adelanta lo pedido a todo lo que espera en XtreamFilter |
| Si no coincide ningún prefijo, buscar otras versiones con audio en los idiomas indicados | Activa el análisis de audio (pausa la cola unos segundos si XtreamFilter está descargando) |
| …o con subtítulos completos en los idiomas indicados | Si ninguna versión tiene el audio, acepta una con subtítulos completos en esos idiomas |
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

Una vez al día, dentro de la ventana horaria configurada (o con "Buscar ahora"):

```
1. Si hay algo en la cola, se pausa y se esperan 15 s (conexión libre). Si había una descarga a medias
   queda en pausa (no se cancela). Al terminar: se esperan 15 s, si había una descarga en curso se
   reintentan las descargas con error (según la configuración de reintentos) y se reanuda la cola.
   Solo si la pausó el puente; si la pausaste tú, no se toca
2. Series de Jellyfin (disco), sin las ignoradas ni las carpetas vacías
3. Sin número de TMDB en Jellyfin → no se procesa; aparece en la lista como "FALTA TMDB"
4. Completa según Seerr (compara Jellyfin con TMDB) → no se consulta al proveedor
5. Parcial según Seerr → se consulta al proveedor su lista de episodios (una serie cada 5 s)
6. Episodios del proveedor − Jellyfin − cola de XtreamFilter = episodios que faltan
```

- **Misma versión que la carpeta**: si la carpeta es `EN - Lioness`, busca en `EN - Lioness`. Nunca adivina: si el nombre del catálogo no coincide con la carpeta (salvo año/país), la serie aparece como "sin versión en el catálogo".
- **Despacio**: el proveedor puede **bloquear tu IP** si recibe muchas consultas seguidas (ocurrió con ~40 consultas en 26 s). Por eso se consulta una serie cada 5 s y solo las parciales según Seerr.
- **Proveedor sin respuesta**: si falla 2 veces seguidas, la búsqueda se cancela sin tocar las listas (aviso en la página y en el registro).
- **FALTA TMDB**: identifica la serie en Jellyfin (⋮ → Identificar) para que Seerr sepa si le faltan episodios.
- **Añadir automáticamente**: si está activado, al terminar se añade a la cola todo lo encontrado (al final de la cola).
- Las series que ya no existen (borradas del disco) se quitan solas de la lista y de las ignoradas.

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
