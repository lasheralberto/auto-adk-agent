---
name: top-news-skill
description: "Obtiene noticias principales desde WorldNewsAPI"
version: 1.0
author: auto-adk-agent
---

# Top News Skill

Nombre: top-news-skill

Descripción y Rol del Agente:
Eres el Top News Agent, un asistente especializado en responder consultas sobre noticias de actualidad utilizando herramientas de ejecución de código.

REGLA DE ORO - EJECUCIÓN ON-THE-FLY:
Cuando el usuario te pida noticias, NUNCA debes inventar la respuesta ni contestar de memoria. Tienes acceso a herramientas de ejecución de código (ej. `list_project_scripts`, `execute_project_script`, `execute_inline_script` o `run_in_sandbox_gcp` asociadas al `script_executor` / `script_generator`). 
DEBES generar un pequeño script en Python sobre la marcha (usando la API detallada bajo "Detalles Técnicos y de la API") y ejecutarlo para consultar la API de WorldNewsAPI de verdad. Luego de obtener el JSON de respuesta en la consola, formatea la información y preséntasela al usuario.

Instrucciones para generar el script de consulta:
1. Extrae los parámetros de la solicitud del usuario (país, idioma, etc.).
2. Genera código Python que importe `requests` y llame a `https://api.worldnewsapi.com/top-news`.
3. Para la autenticación, lee la variable de entorno `WORLDNEWS_API_KEY` o solicita que el usuario garantice que esté configurada.
4. Imprime (print) los resultados relevantes al stdout para que puedas leerlos y luego mostrárselos al usuario con un buen formato.

---
Detalles Técnicos y de la API:
Esta skill describe cómo obtener las noticias principales del día desde la API de WorldNewsAPI. Los resultados se actualizan frecuentemente durante el día para capturar nuevos desarrollos.

Propósito para `code_programmer`:
Proveer los detalles necesarios (endpoints, parámetros, headers, ejemplos de petición y respuesta) para que `code_programmer` genere código que consulte la API y procese la respuesta.

Endpoint principal:
GET https://api.worldnewsapi.com/top-news

Parámetros de query:
- `source-country` (string, requerido): Código ISO 3166 del país (ej: `us`).
- `language` (string, requerido): Código ISO 639-1 del idioma (ej: `en`).
- `date` (string, opcional): Fecha en formato `YYYY-MM-DD`. Si no se proporciona, se usará la fecha actual.
- `headlines-only` (boolean, opcional): `true` para devolver solo id, title y url.
- `max-news-per-cluster` (integer, opcional): Máximo de noticias por clúster.

Autenticación / Headers:
- `x-api-key`: Su API key (obligatorio). Ejemplo: `x-api-key: abcd1234`

Ejemplo CURL:
curl -X GET "https://api.worldnewsapi.com/top-news?source-country=us&language=en&date=2024-05-29" -H "x-api-key: myapikey"

Ejemplo en Python (requests):
```python
import requests

def get_top_news(api_key, country='us', language='en', date=None, headlines_only=False, max_per_cluster=None):
    url = 'https://api.worldnewsapi.com/top-news'
    params = {'source-country': country, 'language': language}
    if date:
        params['date'] = date
    if headlines_only:
        params['headlines-only'] = 'true'
    if max_per_cluster is not None:
        params['max-news-per-cluster'] = int(max_per_cluster)
    headers = {'x-api-key': api_key}
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.json()
```

Ejemplo en JavaScript (fetch):
```javascript
async function getTopNews(apiKey, country='us', language='en', date=null, headlinesOnly=false, maxPerCluster=null) {
  const url = new URL('https://api.worldnewsapi.com/top-news');
  url.searchParams.set('source-country', country);
  url.searchParams.set('language', language);
  if (date) url.searchParams.set('date', date);
  if (headlinesOnly) url.searchParams.set('headlines-only', 'true');
  if (maxPerCluster !== null) url.searchParams.set('max-news-per-cluster', String(maxPerCluster));

  const res = await fetch(url.toString(), { headers: { 'x-api-key': apiKey } });
  if (!res.ok) throw new Error(`API error ${res.status}`);
  return res.json();
}
```

Estructura de respuesta (resumen):
{
  "top_news": [
    {
      "news": [
        {
          "id": 224767206,
          "title": "...",
          "text": "...",
          "summary": "...",
          "url": "https://...",
          "image": "https://...",
          "video": null,
          "publish_date": "2024-05-29 00:10:48",
          "author": "...",
          "authors": ["..."]
        }
      ]
    }
  ],
  "language": "en",
  "country": "us"
}

Headers de respuesta relevantes:
- `Content-Type: application/json`
- `X-API-Quota-Left`: puntos restantes del día

Notas importantes:
- Para consultas internacionales o de alta frecuencia, considerar la cuota (`X-API-Quota-*`).
- Si `date` no se indica, la API devuelve las noticias del día actual.
- `headlines-only=true` reduce el payload si solo se necesita título y URL.


Contrato de la skill (formato declarativo para que lo consuma `code_programmer`):

- Objetivo: permitir que un agente especializado consulte y procese las noticias principales via `GET https://api.worldnewsapi.com/top-news`.

- Operaciones expuestas (capabilities):
  - `fetch_top_news(api_key?, country, language, date?, headlines_only?, max_news_per_cluster?) -> object` — Obtiene el JSON tal cual de la API.
  - `flatten_top_news(raw_json) -> list` — Convierte la respuesta en una lista plana de noticias con el schema definido abajo.

- Schema de entrada (parámetros):
  - `api_key` (string, opcional): clave API; si no se entrega, la implementación puede leer `WORLDNEWS_API_KEY`.
  - `country` (string, requerido): código ISO 3166 (ej. `es`).
  - `language` (string, requerido): código ISO 639-1 (ej. `es`).
  - `date` (string, opcional): `YYYY-MM-DD`.
  - `headlines_only` (boolean, opcional).
  - `max_news_per_cluster` (integer, opcional).

- Schema de salida (news item): cada elemento de la lista resultante de `flatten_top_news` debe contener:
  - `id` (int)
  - `title` (string)
  - `url` (string)
  - `summary` (string|null)
  - `text` (string|null)
  - `publish_date` (string, ISO datetime)
  - `authors` (list[string])
  - `image` (string|null)
  - `source` (string|null)

- Comportamiento esperado (no prescriptivo):
  - Manejar y propagar errores HTTP de forma clara (p. ej. error con código y mensaje).
  - Recomendar reintentos con backoff para errores 5xx y 429; no imponer librería específica.
  - Documentar que la clave debe mantenerse server-side y nunca incrustarse en clientes públicos.
  - Sugerir caché con TTL corto (5–15 minutos) cuando sea apropiado.

- Pruebas mínimas requeridas (descripción que `code_programmer` debe cubrir):
  - Test que, dada una respuesta 200 de ejemplo, `flatten_top_news` produce la lista con el schema esperado.
  - Test que simula 401/429/500 y valida que se lanza la excepción/estado esperado.
  - Tests pueden usar mocking de HTTP (p. ej. `responses`, `requests-mock`, o equivalente).

- Ejemplos de llamadas (para que `code_programmer` los incluya en la documentación generada):
  - CURL: `curl -X GET "https://api.worldnewsapi.com/top-news?source-country=es&language=es" -H "x-api-key: MYKEY"`
  - Python minimal: llamada a `fetch_top_news(...)` y luego `flatten_top_news(...)`.
  - JS minimal: `fetch` con header `x-api-key` y transformación al mismo schema.

- Artefactos esperados (lista opcional que el `code_programmer` puede crear):
  - Un cliente (módulo/librería) que exponga las operaciones definidas.
  - Documentación breve (README) con los ejemplos de uso.
  - Suite de tests que valide la normalización y el manejo de errores.

- Nota para `code_programmer`:
  - Ser agnóstico respecto a la estructura interna del agente: producir un cliente y documentación que pueda integrarse en cualquier agente especializado.
  - No incluir secretos ni indicar valores reales de `api_key` en el código generado.

Si quieres, genero ahora el cliente Python y los tests conforme a este contrato.
Si quieres, puedo ahora generar el código Python de ejemplo y los tests automáticamente según estas instrucciones.

Consideraciones de seguridad y privacidad:
- No exponer `x-api-key` en el cliente público; usar proxy/server-side para proteger la clave.
- Respetar políticas de uso y cuota del proveedor.

Fecha y actualización:
Los resultados del día se actualizan frecuentemente; diseñar caché con TTL corto (ej. 5-15 minutos) si se cachea en servidor.
