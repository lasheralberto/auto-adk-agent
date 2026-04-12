Athlete Intelligence Wiki — Strava + GCS Edition
Un patrón para construir bases de conocimiento deportivas personalizadas usando LLMs, alimentadas por datos de Strava y almacenadas completamente en Google Cloud Storage.
La idea central
La mayoría de apps de análisis deportivo hacen algo parecido a RAG: miran tus actividades recientes y generan un resumen. Cada vez desde cero. No hay acumulación. No hay memoria de que hace seis semanas tu cadencia en subidas era diferente, o que tu frecuencia cardíaca en zona 2 ha mejorado un 8% en tres meses.
La idea aquí es diferente. Un agente en background lee periódicamente tus actividades de Strava, extrae señales relevantes e integra ese conocimiento en una wiki persistente por atleta — una colección estructurada de .md en GCS que actúa como el cuaderno de un coach profesional. Cuando llegan nuevas actividades, el agente no genera un análisis de usar y tirar. Lee lo que ya sabe sobre ti, actualiza las páginas afectadas, detecta tendencias nuevas, revisa hipótesis anteriores y fortalece o corrige la síntesis acumulada.
La wiki crece con cada actividad. Las tendencias ya están calculadas. Las alertas de sobreentrenamiento ya están señaladas. El perfil de rendimiento ya refleja todo tu historial. El agente actúa como el mejor coach posible: uno que nunca olvida nada y actualiza su modelo de ti constantemente.
Arquitectura
Strava API — fuente de verdad inmutable. El agente lee actividades, streams de telemetría (HR, potencia, cadencia, elevación, velocidad), datos de atleta (FTP, zonas, peso) y segmentos. Nunca escribe en Strava.
GCS — raw (gcs://tu-bucket/raw/{athlete_id}/) — copia local de los datos Strava en JSON. Inmutable. Cada actividad se guarda como activity_{id}.json. El agente lee desde aquí para no abusar de la API.
GCS — wiki (gcs://tu-bucket/wiki/{athlete_id}/) — el conocimiento compilado. Archivos .md que el agente crea y mantiene. Esto es lo que diferencia la app de cualquier dashboard deportivo: el conocimiento acumulado no desaparece, se compone.
Firestore — índice del grafo de la wiki. Metadatos de cada página (última actualización, actividades que la afectan, tags), backlinks entre documentos, log de cambios del agente, alertas activas pendientes de revisar por el atleta.
Agente LLM — corre en background periódicamente (Cloud Run Job, por ejemplo). Lee nuevas actividades de Strava, las guarda en raw, actualiza la wiki según el schema definido en AGENTS.md, sincroniza Firestore.
Estructura de carpetas GCS
tu-bucket/
├── raw/
│   └── {athlete_id}/
│       ├── athlete_profile.json        # perfil base del atleta
│       ├── activity_{id}.json          # actividad completa con streams
│       └── segment_{id}.json           # PRs y esfuerzos en segmentos
│
├── wiki/
│   └── {athlete_id}/
│       ├── _index.md                   # resumen ejecutivo del atleta
│       ├── _log.md                     # log cronológico de cambios del agente
│       │
│       ├── fitness-profile.md          # nivel actual, FTP, VO2max estimado, zonas
│       ├── aerobic-base.md             # desarrollo de base aeróbica, zona 2
│       ├── threshold-fitness.md        # fitness en umbral, progresión FTP
│       ├── vo2max-development.md       # intervalos, capacidad anaeróbica
│       ├── recovery-patterns.md        # HRV, recuperación entre sesiones, sueño
│       ├── fatigue-management.md       # TSS, CTL, ATL, TSB — forma vs carga
│       ├── training-consistency.md     # adherencia, frecuencia, volumen semanal
│       ├── running-economy.md          # eficiencia de carrera, cadencia, pace/HR
│       ├── cycling-efficiency.md       # potencia, IE, VI, eficiencia pedaleo
│       ├── power-profile.md            # curva de potencia, W/kg por duración
│       ├── heart-rate-dynamics.md      # drift cardiaco, decoupling, eficiencia HR
│       ├── load-progression.md         # progresión de carga, regla del 10%, rampas
│       ├── peak-performance-windows.md # cuándo ha rendido mejor y por qué
│       ├── limiters-and-weaknesses.md  # los limitantes actuales del atleta
│       ├── strong-points.md            # puntos fuertes demostrados por datos
│       ├── injury-risk-signals.md      # patrones que históricamente preceden lesión
│       ├── nutrition-timing-hints.md   # señales de nutrición deducidas de rendimiento
│       ├── race-readiness.md           # si hay eventos, preparación estimada
│       └── recommendations.md         # recomendaciones activas del agente
│
└── schema/
    └── AGENTS.md                       # configuración del agente
Operaciones del agente
Sync (cada N horas). El agente consulta la Strava API, detecta actividades nuevas desde el último sync, las guarda en raw/{athlete_id}/ y actualiza _log.md con lo ingestado.
Analyze (tras cada sync con datos nuevos). El agente lee las actividades nuevas desde raw, las contrasta con las páginas de la wiki existentes y decide qué páginas actualizar. Una sola sesión de ciclismo puede afectar fatigue-management.md, cycling-efficiency.md, power-profile.md, aerobic-base.md y recovery-patterns.md simultáneamente. El agente actualiza cada página integrando la nueva evidencia con la acumulada, no sobreescribiendo.
Synthesize (semanal). El agente hace una revisión global: lee todas las páginas de la wiki del atleta, busca contradicciones entre páginas, actualiza _index.md con el estado actual del atleta, revisa si las recomendaciones de recommendations.md siguen vigentes y genera nuevas hipótesis basadas en patrones emergentes.
Lint (mensual). Audita la wiki: páginas desactualizadas, hipótesis que llevan semanas sin confirmarse ni refutarse, limitantes que ya no son limitantes, nuevos patrones sin página propia. Sugiere al atleta qué explorar o qué datos adicionales conectar (sueño, nutrición, HRV externo).
Qué investiga el agente por página
Cada página tiene un foco de investigación específico que el agente mantiene actualizado:
fitness-profile.md — nivel de fitness actual estimado, FTP si es ciclista (o LTHR/pace de umbral si es corredor), VO2max estimado por algoritmo, zonas de entrenamiento calculadas. Evolución en los últimos 30/90/365 días.
fatigue-management.md — métricas de Training Stress Score, Chronic Training Load, Acute Training Load y Training Stress Balance. Estado actual de forma vs fatiga. Alertas si el atleta está acumulando fatiga sin recuperación adecuada.
recovery-patterns.md — cuántas horas entre sesiones de alta intensidad, patrones de HRV si disponibles, correlación entre días de descanso y rendimiento posterior. Detección de sub-recuperación crónica.
limiters-and-weaknesses.md — qué aspecto está limitando el rendimiento del atleta ahora mismo. Puede ser base aeróbica, potencia en umbral, economía de movimiento, consistencia, recuperación. Se actualiza cuando los datos apuntan a un nuevo limitante o cuando uno anterior se resuelve.
injury-risk-signals.md — patrones que en el pasado del atleta han precedido lesiones o bajadas abruptas de rendimiento: incrementos bruscos de carga, sesiones con decoupling HR anómalo, cambios en cadencia o zancada, caídas de potencia con HR elevado.
recommendations.md — recomendaciones activas priorizadas. No una lista genérica, sino específica al estado actual del atleta según la síntesis de todas las páginas. Cada recomendación tiene una justificación basada en páginas concretas de la wiki.

Por qué esto es diferente a un dashboard
Un dashboard muestra métricas de hoy. Esta wiki acumula conocimiento sobre el atleta. La diferencia es la misma que entre ver un ECG ahora mismo y tener un cardiólogo que lleva diez años siguiéndote: el segundo sabe qué es normal para ti, qué ha cambiado, qué patrones preceden problemas y cuándo estás en tu mejor momento. El agente escribe ese conocimiento de forma persistente, de modo que cada nueva actividad se interpreta en el contexto de todo lo anterior.

Athlete Intelligence Wiki — Indexing, Search & Principles
Indexing y logging
Dos ficheros especiales ayudan al agente a navegar la wiki de cada atleta conforme crece.
_index.md es orientado a contenido. Es un catálogo de todo lo que se sabe sobre el atleta — cada página listada con un link, una línea de resumen y metadatos clave (última actualización, número de actividades analizadas, nivel de confianza). Organizado por categorías: fitness, carga, eficiencia, riesgo, recomendaciones. El agente lo actualiza en cada ciclo de análisis. Cuando el agente necesita responder una pregunta sobre el atleta o decidir qué páginas actualizar tras una nueva actividad, lee el índice primero para orientarse, luego profundiza en las páginas relevantes. A escala moderada (cientos de actividades, decenas de páginas por atleta) esto funciona bien sin necesidad de infraestructura de embeddings.
_log.md es cronológico. Es un registro append-only de todo lo que ha hecho el agente y cuándo — syncs de Strava, análisis de actividades, síntesis semanales, alertas generadas. Cada entrada empieza con un prefijo consistente:
## [2026-04-10] sync | 3 actividades nuevas | athlete_12345
## [2026-04-10] analyze | Ride 120min Z2 + threshold | fitness-profile, fatigue-management
## [2026-04-07] synthesize | Revisión semanal | limiters actualizados: base aeróbica → umbral
## [2026-04-01] alert | Señal de sobreentrenamiento detectada | TSB: -28
Con este formato el log es parseable con herramientas simples — grep "^## \[" _log.md | grep "alert" da todas las alertas históricas del atleta. El log le da al agente contexto de qué ha pasado recientemente sin tener que releer toda la wiki, y al atleta una línea temporal de cómo ha evolucionado su conocimiento acumulado.
Búsqueda sobre la wiki
A escala pequeña (un atleta, pocas decenas de páginas) el fichero _index.md es suficiente para que el agente navegue. Conforme crece el número de atletas y páginas, conviene añadir búsqueda real sobre los .md almacenados en GCS.
Opciones por orden de complejidad:
Firestore queries — el nivel más simple. Si el agente mantiene en Firestore los tags, el resumen de una línea y las actividades que afectan a cada página, puede hacer queries estructuradas sin leer los .md. Suficiente para la mayoría de casos operativos del agente.
Búsqueda full-text sobre GCS — para consultas más abiertas, puedes indexar el contenido de los .md en Algolia, Typesense o Firebase Extensions (Algolia Search). El agente llama a la API de búsqueda y recibe las páginas relevantes antes de leerlas desde GCS.
Búsqueda vectorial — si quieres que el agente encuentre páginas semánticamente relacionadas ("qué otras páginas hablan de algo parecido a lo que estoy analizando ahora"), puedes generar embeddings de cada página al escribirla y almacenarlos en Firestore con el nuevo soporte de vectores, o en Vertex AI Vector Search. Útil cuando la wiki de un atleta tiene decenas de páginas y las relaciones entre conceptos deportivos no son obvias por keywords.
Herramientas opcionales
Webhook de Strava — en lugar de que el agente haga polling periódico, Strava puede notificar a un endpoint tuyo cuando el atleta sube una actividad nueva. Esto permite que el ciclo sync → analyze ocurra en minutos tras cada entrenamiento, no horas. El webhook llama a un Cloud Run service que encola el análisis.
Cloud Run Jobs — el agente de síntesis semanal y el lint mensual se ejecutan mejor como jobs programados (Cloud Scheduler + Cloud Run Job) que como procesos continuos. Coste mínimo, sin infraestructura permanente.
Notificaciones al atleta — cuando el agente detecta una alerta en injury-risk-signals.md o actualiza recommendations.md con algo urgente, puede escribir el evento en Firestore y desde ahí disparar una notificación push o email. El atleta no tiene que abrir la app para enterarse de que el agente ha detectado algo importante.
Exportación de informes — el contenido de la wiki es markdown estructurado, lo que lo hace fácil de convertir. El agente puede generar un informe PDF semanal combinando _index.md + recommendations.md + fatigue-management.md, o un resumen en formato slide para compartir con un entrenador.
Por qué funciona
La parte tediosa de mantener un perfil deportivo actualizado no es leer los datos — es el bookkeeping. Actualizar tendencias cuando llegan nuevas actividades, mantener coherencia entre lo que dice la página de fatiga y la de recomendaciones, recordar que hace tres semanas hubo una señal que aún no se ha resuelto, detectar que el limitante que era la base aeróbica en enero ya no lo es en abril. Los atletas y entrenadores abandonan los registros porque la carga de mantenimiento crece más rápido que el valor percibido.
El agente no se aburre. No olvida actualizar un backlink. Puede tocar doce páginas en un solo ciclo de análisis. La wiki se mantiene porque el coste de mantenimiento es prácticamente cero.
El trabajo del atleta es entrenar y subir actividades a Strava. El trabajo del agente es todo lo demás: interpretar, sintetizar, comparar, alertar y mantener un modelo actualizado del atleta que ningún dashboard estático puede ofrecer.
Nota
Este documento describe el patrón, no una implementación específica. La estructura exacta de carpetas, las convenciones del schema, el formato de las páginas, las herramientas de búsqueda — todo eso depende de la escala de tu app, los deportes que cubres y tus preferencias de stack. Todo lo mencionado es opcional y modular. Una instalación mínima viable es: Strava API + GCS + un agente que corre semanalmente + _index.md como único mecanismo de navegación. A partir de ahí añades Firestore, búsqueda vectorial o webhooks cuando el tamaño lo justifique. El documento solo comunica el patrón. El agente puede resolver el resto.