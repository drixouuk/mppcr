FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    JAVA_HOME=/usr/lib/jvm/default-java

# JRE headless requis par MPXJ / jpype1.
RUN apt-get update \
    && apt-get install -y --no-install-recommends default-jre-headless \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Les scripts existants sont copiés tels quels, sans modification.
COPY dcma14.py montecarlo.py app.py ./
COPY templates ./templates

# /app/data accueille usage.db (compteur d'analyses par IP). Sans ce chown, le
# conteneur tourne en appuser et ne peut pas créer le fichier SQLite.
RUN useradd --create-home appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 5000

# Healthcheck sans curl (absent de l'image slim) : urllib suffit.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/healthz')" || exit 1

# Workers gthread : une analyse Monte Carlo occupe un thread pendant tout le
# sous-processus JVM, mais subprocess.run() relâche le GIL — les autres threads
# du même worker continuent donc de servir le formulaire et /healthz. Sans cela,
# 2 analyses simultanées suffisaient à saturer les 2 workers synchrones et à
# faire passer le conteneur en unhealthy.
# Le nombre d'analyses lourdes réellement menées de front reste borné par
# MAX_CONCURRENT_ANALYSES (défaut 2), partagé entre workers via SQLite.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "2", "--threads", "4", "--worker-class", "gthread", \
     "--timeout", "900", "app:app"]
