---
name: cloudification-agent
description: Skill para búsqueda y evaluación de objetos SAP usando los JSON públicos del repositorio de Cloudification de SAP. Proporciona búsqueda inteligente, filtros por Clean Core Level (A/B/C/D), descubrimiento de versiones PCE, verificación de cumplimiento, y obtención de descripciones/metadatos desde api.sap.com.
---

# Cloudification Agent — SKILL

Resumen
-------
Skill para búsqueda y evaluación de objetos SAP usando los JSON públicos del repositorio de Cloudification de SAP. Proporciona búsqueda inteligente, filtros por Clean Core Level (A/B/C/D), descubrimiento de versiones PCE, verificación de cumplimiento, y obtención de descripciones/metadatos desde `api.sap.com`.

Características principales
-------------------------
- Búsqueda de objetos SAP: clases, CDS views, tablas, data elements, BDEFs, etc.
- Filtrado por Clean Core Level (A, B, C, D) — nuevo modelo desde agosto 2025.
- Detección de sucesores para objetos obsoletos o no liberados.
- Verificación de cumplimiento Clean Core para listas de objetos (tasa de cumplimiento).
- Descripciones enriquecidas desde `api.sap.com`: capacidades, flags de extensibilidad, listas de campos para CDS/BDEFs.
- Estadísticas agregadas: cuentas por nivel, tipo y componente de aplicación.
- Búsqueda inteligente (multi-token) con ranking por relevancia.
- Soporte multi-sistema: `public_cloud`, `btp`, `private_cloud`, `on_premise`.
- Versionado dinámico: versiones PCE descubiertas automáticamente del repositorio.
- Dual transport: modo servidor remoto hospedado o ejecutable local por stdio.

Cómo funciona
------------
- En tiempo de ejecución el servidor descarga los JSON públicos del repositorio oficial de SAP Cloudification y los cachea en memoria por 24 horas.
- No se requiere conexión a un sistema SAP; toda la información proviene del repositorio público de SAP y `api.sap.com` para metadatos adicionales.
- Para PCE/On-Premise los archivos `objectReleaseInfo_PCE*.json` se usan para descubrir versiones y niveles A–D.

Concepto Clean Core (desde agosto 2025)
------------------------------------
- Nivel A — Released APIs (ABAP Cloud). Fuente: `objectReleaseInfoLatest.json` (Public Cloud), `objectReleaseInfo_BTPLatest.json` (BTP), `objectReleaseInfo_PCE*.json` (PCE). Upgrade-safe.
- Nivel B — Classic APIs. Fuente: `objectClassifications_SAP.json`. Upgrade-stable.
- Nivel C — Internal / Unclassified objects. Objetos no catalogados. Riesgo manejable.
- Nivel D — noAPI (no recomendado). Objetos marcados `noAPI`. Alto riesgo.

Tipos de sistema soportados
-------------------------
- `public_cloud`: S/4HANA Cloud Public Edition — dataset `objectReleaseInfoLatest.json` (solo Nivel A).
- `btp`: BTP ABAP Environment / Steampunk — dataset `objectReleaseInfo_BTPLatest.json` (solo Nivel A).
- `private_cloud`: S/4HANA Cloud Private Edition — archivos `objectReleaseInfo_PCE*.json` (Niveles A–D). Versiones detectadas dinámicamente.
- `on_premise`: S/4HANA On-Premise — mismo dataset PCE (A–D). Versionado dinámico.

Herramientas expuestas (endpoints internos del agente)
-----------------------------------------------------

sap_search_objects
- Descripción: Busca objetos SAP con filtros avanzados y ranking por relevancia (soporta nombre exacto y consultas en lenguaje natural).
- Parámetros:
	- `query` (string, requerido): término de búsqueda (p.ej. `I_PRODUCT`, `purchase order`).
	- `system_type` (enum): `public_cloud` | `btp` | `private_cloud` | `on_premise`. Default: `public_cloud`.
	- `clean_core_level` (enum): `A` | `B` | `C` | `D`. Máximo nivel acumulado. Default: `A`.
	- `version` (string): versión PCE (p.ej. `2025`, `2023_3`). Ignorado para `public_cloud` y `btp`. Default: `latest`.
	- `object_type` (string): filtro TADIR (p.ej. `CLAS`, `DDLS`, `TABL`). Default: todos.
	- `app_component` (string): componente de aplicación (p.ej. `MM-PUR`, `FI-GL`).
	- `state` (string): filtrar por estado de liberación.
	- `limit` (number): 1–100. Default: 25.
	- `offset` (number): paginación offset. Default: 0.

sap_get_object_details
- Descripción: Obtiene detalle completo de un objeto (evaluación Clean Core, estado de release, sucesores, metadatos desde `api.sap.com`).
- Parámetros:
	- `object_type` (string, requerido): TADIR type (`TABL`, `CLAS`, `DDLS`, ...).
	- `object_name` (string, requerido): nombre del objeto (`MARA`, `I_PURCHASEORDER`).
	- `system_type` (enum): `public_cloud` | `btp` | `private_cloud` | `on_premise`. Default: `public_cloud`.

sap_find_successor
- Descripción: Encuentra sucesor(es) de un objeto obsoleto o no liberado (útil para migración a ABAP Cloud).
- Parámetros:
	- `object_type` (string, requerido)
	- `object_name` (string, requerido)
	- `system_type` (enum): Target system type. Default: `public_cloud`.

sap_check_clean_core_compliance
- Descripción: Verifica cumplimiento Clean Core para una lista de objetos y calcula la tasa de cumplimiento.
- Parámetros:
	- `object_names` (string, requerido): lista separada por comas (`BSEG,MARA,CL_GUI_ALV_GRID`).
	- `system_type` (enum): Default: `public_cloud`.
	- `target_level` (enum): `A` | `B` | `C` | `D`. Default: `A`.

sap_list_versions
- Descripción: Lista versiones S/4HANA PCE disponibles (descubiertas dinámicamente desde `objectReleaseInfo_PCE*.json`).
- Parámetros: ninguno.

sap_list_object_types
- Descripción: Lista tipos TADIR disponibles con conteos por Clean Core level.
- Parámetros: ninguno.

sap_get_statistics
- Descripción: Resumen estadístico del repositorio: totales, breakdown por nivel, por tipo de objeto y por componente de aplicación.
- Parámetros: ninguno.

Algoritmo de búsqueda inteligente
--------------------------------
- Tokeniza la consulta del usuario, normaliza (remueve stopwords SAP comunes) y calcula una puntuación multi-token por coincidencias en: nombre del objeto, título/etiquetas, descripción, etiquetas de campos y componente de aplicación.
- Penaliza coincidencias parciales en cadenas muy genéricas; premia coincidencias exactas y matches en campos clave (p.ej. `I_` prefijo para CDS publicadas).
- Resultados devueltos ordenados por score, con campos: `object_type`, `object_name`, `clean_core_level`, `score`, `summary`.

Fuentes de datos
---------------
- Repositorio público de SAP Cloudification (GitHub) — archivos JSON mencionados.
- `api.sap.com` para metadatos y descripciones enriquecidas cuando estén disponibles.

Implementación y notas operativas
--------------------------------
- Cache en memoria por 24 horas; refresco en segundo plano al expirar.
- Soporte de transporte dual: opción para usar un servidor hospedado remoto (HTTP) o un ejecutable local que comunique por stdio. La elección se hace en la configuración del agente.
- Descubrimiento automático de versiones PCE leyendo patrones `objectReleaseInfo_PCE*.json` y extrayendo tokens de versión.
- Manejar robustamente fallos de red a `api.sap.com` — degradación elegante mostrando los datos del repositorio cuando falten metadatos.

Ejemplos de uso
---------------
- `sap_get_object_details(object_type="TABL", object_name="MARA", system_type="public_cloud")` → devuelve estado, nivel Clean Core, y sucesor si existe.
- `sap_search_objects(query="purchase order", object_type="DDLS", clean_core_level="A")` → lista CDS views Level A relacionadas con órdenes de compra ordenadas por relevancia.
- `sap_check_clean_core_compliance(object_names="BSEG,MARA,CL_GUI_ALV_GRID", target_level="A")` → devuelve evaluaciones por objeto y compliance rate.

Notas finales
------------
- Diseñado para ser usado por agentes conversacionales y herramientas de análisis estático de código para planificar migraciones a ABAP Cloud.
- Los nombres de las herramientas expuestas (p.ej. `sap_search_objects`) deben mapearse a funciones implementadas por el runtime del agente.

