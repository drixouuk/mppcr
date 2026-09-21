FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=UTF-8 \
    PIP_NO_CACHE_DIR=1 \
    JAVA_HOME=/usr/lib/jvm/default-java

# Mémoire allouée à la JVM embarquée (lecture des .mpp par MPXJ/POI).
# Indispensable : sans limite, la JVM se dimensionne sur la RAM qu'elle *croit*
# voir. Dans un conteneur LXC, /proc/meminfo montre la mémoire de l'hôte (32 Go
# ici) et non celle allouée au conteneur : la JVM se donnait 7,8 Go de tas pour
# un conteneur qui en partage 8 avec d'autres services — un planning volumineux
# pouvait donc épuiser l'hôte et faire tuer des processus voisins par le noyau.
# Bornée, la même analyse échoue proprement avec un message explicite.
# 2 Go depuis le 21/09/2026 : les mesures montrent que ce qui pèse n'est ni la
# taille du fichier ni le nombre de tâches, mais le volume de données datées
# (avancement saisi période par période). Un plan de 100 tâches suivi finement
# dépassait les 768 Mo précédents ; un million d'entrées datées demande ~790 Mo.
# Ajustable au lancement sans reconstruire l'image :
#   docker run -e JAVA_TOOL_OPTIONS="-Xmx3g ..." …
ENV JAVA_TOOL_OPTIONS="-Xmx2g -XX:MaxMetaspaceSize=256m -XX:+UseSerialGC -XX:ActiveProcessorCount=1 -XX:+ExitOnOutOfMemoryError"

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
# MAX_CONCURRENT_ANALYSES (défaut 1 : une seule JVM lourde à la fois), partagé
# entre workers via SQLite. Le timeout Gunicorn couvre le pire cas légitime :
# deux scripts (diagnostic puis simulation) de 300 s chacun.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "2", "--threads", "4", "--worker-class", "gthread", \
     "--timeout", "900", "app:app"]
