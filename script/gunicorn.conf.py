"""Configuration Gunicorn (production).

Lancer depuis la racine du projet :
    gunicorn -c script/gunicorn.conf.py   (ou : docker compose up -d)
puis ouvrir http://<serveur>:8080

Un seul worker : l'état des traitements en cours (JOBS) est gardé en mémoire
dans le processus. Avec plusieurs workers, la page de progression pourrait
arriver sur un autre processus et répondre 404. Le parallélisme passe donc
par les threads.
"""
import os

wsgi_app = "app:app"
chdir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")

bind = f"{os.environ.get('HOST', '0.0.0.0')}:{os.environ.get('PORT', '8080')}"
workers = 1
worker_class = "gthread"
threads = int(os.environ.get("THREADS", "8"))
timeout = 300          # le calcul de l'Excel peut prendre du temps sur de gros fichiers
graceful_timeout = 30

accesslog = "-"
errorlog = "-"
loglevel = os.environ.get("LOG_LEVEL", "info")
