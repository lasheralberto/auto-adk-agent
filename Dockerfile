# Usa la imagen oficial de Python
FROM python:3.11-slim

# Establece el directorio de trabajo dentro del contenedor
WORKDIR /app

# Copia los archivos de dependencias
COPY requirements.txt .

# Instala las dependencias necesarias y gunicorn
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir gunicorn

# Copia todo el código de la aplicación
COPY . .

# Expone el puerto 8080 (Cloud Run escucha en este puerto)
EXPOSE 8080

# Comando para ejecutar la aplicación con Gunicorn.
# Para SSE y peticiones largas en Cloud Run, usamos worker threaded y timeout infinito
# para evitar abortos del worker mientras se transmite la respuesta.
CMD ["gunicorn", "-b", "0.0.0.0:8080", "--worker-class", "gthread", "--threads", "8", "--workers", "1", "--timeout", "0", "--graceful-timeout", "30", "app:app"]