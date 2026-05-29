# Installation

```bash
sudo apt update
sudo apt install python3 python3.12-venv postgresql nginx

git clone https://github.com/AaltoRSE/studyserver.git
cd studyserver

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn

python manage.py collectstatic
python manage.py migrate
```

Create `/etc/systemd/system/gunicorn.service` with

```
[Unit]
Description=gunicorn daemon
After=network.target

[Service]
User=USERNAME
Group=www-data
WorkingDirectory=/home/USERNAME/studyserver
ExecStart=/home/USERNAME/studyserver/venv/bin/gunicorn --access-logfile - --workers 3 --bind unix:/home/USERNAME/studyserver/studyserver.sock study_server.wsgi:application

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl start gunicorn
sudo systemctl enable gunicorn
```

Create `/etc/nginx/sites-available/studyserver` with


```
server {
    listen 80;
    server_name DOMAIN;
    return 301 https://$server_name$request_uri;
}

server {
    listen 443 ssl;
    server_name DOMAIN;

    ssl_certificate /PATH_TO/fullchain.pem;
    ssl_certificate_key /PATH_TO/privkey.pem;

    location = /favicon.ico { access_log off; log_not_found off; }
    location /static/ {
        root /home/USERNAME/studyserver;
    }

    location / {
        include proxy_params;
        proxy_pass http://unix:/home/USERNAME/studyserver/studyserver.sock;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/studyserver /etc/nginx/sites-enabled
sudo nginx -t 
sudo systemctl restart nginx
sudo ufw allow 'Nginx Full'
```


# Install Redis

```bash
sudo apt update
sudo apt install redis-server
sudo systemctl start redis-server
```
