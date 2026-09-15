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

# 2 workers suffisent pour l'usage attendu : le compteur d'analyses est partagé
# entre eux via SQLite, pas via la mémoire du processus.
# Timeout élevé car Monte Carlo peut prendre du temps selon la taille du planning.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--timeout", "900", "app:app"]
