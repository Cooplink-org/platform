web: gunicorn cooplink.wsgi --log-file -
worker: celery -A cooplink worker --loglevel=info -P solo
beat: celery -A cooplink beat --loglevel=info
release: python manage.py migrate
