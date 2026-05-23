# Oracle Cloud 배포 가이드

이 프로젝트는 Oracle Cloud Infrastructure(OCI)에서 `Compute VM + Nginx + gunicorn` 방식으로 배포하는 것이 가장 단순하다.

대상 환경:
- Region: `ap-chuncheon-1`
- OS: Ubuntu 22.04 또는 24.04
- 접속 방식: 공인 IP로 먼저 확인 후, 필요하면 도메인/HTTPS 추가

## 1. Oracle Cloud에서 VM 만들기

OCI 콘솔에서 다음 순서로 만든다.

1. `Compute` -> `Instances` -> `Create instance`
2. 이미지: `Ubuntu`
3. Shape:
   `VM.Standard.A1.Flex` 또는 사용 가능한 Always Free/저비용 인스턴스
4. 네트워크:
   공인 IP 할당
5. SSH 키:
   로컬 공개키 업로드 또는 새 키 생성

## 2. 네트워크 열기

인스턴스가 속한 서브넷/보안 목록 또는 NSG에서 다음 인바운드 포트를 연다.

- `22/tcp` : SSH
- `80/tcp` : HTTP
- `443/tcp` : HTTPS

먼저 `80`만 열어도 배포 확인은 가능하다.

## 3. 코드 올리는 방법

가장 쉬운 방법은 Git 저장소를 쓰는 것이다.

### 방법 A: GitHub에 올린 뒤 서버에서 clone

로컬에서:

```powershell
git status
git remote -v
```

원격 저장소가 없다면 GitHub에 새 저장소를 만든 뒤 push 한다.

서버에서는:

```bash
git clone <YOUR_REPO_URL>
cd <YOUR_REPO_DIR>
```

### 방법 B: Git 없이 파일 직접 업로드

Windows PowerShell에서:

```powershell
scp -r "C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너" ubuntu@<PUBLIC_IP>:/home/ubuntu/
```

업로드 후 서버에서:

```bash
cd /home/ubuntu/종목스캐너
```

주의:
- 한글 경로/폴더명은 Linux에서 다루기 불편할 수 있다.
- 서버에서는 폴더명을 `canslim-quant-scanner` 같이 ASCII 이름으로 바꾸는 편이 낫다.

## 4. 서버 접속

```bash
ssh ubuntu@<PUBLIC_IP>
```

## 5. 서버 초기 설정

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv git nginx certbot python3-certbot-nginx
```

## 6. 앱 설치

프로젝트 폴더에서:

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 7. 환경변수 설정

이 프로젝트는 `.env` 또는 환경변수를 사용한다.

예시:

```bash
cp .env.example .env
nano .env
```

최소한 필요한 값만 채운다. 예:

```env
FINNHUB_API_KEY=
```

실제 키는 Git에 올리지 않는다.

## 8. gunicorn으로 앱 실행 테스트

먼저 수동으로 되는지 확인한다.

```bash
source venv/bin/activate
gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --workers 1 --threads 4 --bind 0.0.0.0:5000 --timeout 120 web_app.app:app
```

브라우저에서 확인:

```text
http://<PUBLIC_IP>:5000/healthz
http://<PUBLIC_IP>:5000/
```

`healthz`가 열리면 앱은 정상 실행 중이다.

테스트가 끝나면 `Ctrl+C`로 종료한다.

## 9. systemd 서비스 등록

```bash
sudo nano /etc/systemd/system/scanner.service
```

아래 내용으로 저장:

```ini
[Unit]
Description=Stock Scanner Web App
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/canslim-quant-scanner
Environment=PATH=/home/ubuntu/canslim-quant-scanner/venv/bin:/usr/bin
ExecStart=/home/ubuntu/canslim-quant-scanner/venv/bin/gunicorn --worker-class geventwebsocket.gunicorn.workers.GeventWebSocketWorker --workers 1 --threads 4 --bind 127.0.0.1:5000 --timeout 120 web_app.app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

주의:
- `WorkingDirectory`와 `ExecStart` 경로는 실제 서버 폴더명에 맞게 바꾼다.

적용:

```bash
sudo systemctl daemon-reload
sudo systemctl enable scanner
sudo systemctl start scanner
sudo systemctl status scanner --no-pager
```

로그 확인:

```bash
sudo journalctl -u scanner -f
```

## 10. Nginx 연결

```bash
sudo nano /etc/nginx/sites-available/scanner
```

아래 내용:

```nginx
server {
    listen 80;
    server_name _;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400;
    }
}
```

활성화:

```bash
sudo ln -sf /etc/nginx/sites-available/scanner /etc/nginx/sites-enabled/scanner
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
```

이제 접속:

```text
http://<PUBLIC_IP>/
http://<PUBLIC_IP>/healthz
```

## 11. 도메인과 HTTPS 붙이기

도메인이 있다면 DNS에서 다음을 설정한다.

- `A` 레코드 -> `<PUBLIC_IP>`

DNS 반영 후 서버에서:

```bash
sudo certbot --nginx -d <YOUR_DOMAIN>
```

예:

```bash
sudo certbot --nginx -d scanner.example.com
```

## 12. 업데이트 방법

Git 배포인 경우:

```bash
cd /home/ubuntu/canslim-quant-scanner
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart scanner
```

## 13. 자주 보는 문제

### `502 Bad Gateway`

원인:
- gunicorn 미실행
- `scanner.service` 경로 오류

확인:

```bash
sudo systemctl status scanner --no-pager
sudo journalctl -u scanner -n 200 --no-pager
```

### `ModuleNotFoundError`

원인:
- 가상환경 패키지 미설치

해결:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

### `/settings` 값이 재부팅 후 사라짐

원인:
- 로컬 파일 기반 설정 저장

해결:
- 운영환경에서는 `.env` 또는 systemd `Environment=` 사용

## 14. 이 저장소 기준 권장 순서

1. GitHub 저장소로 먼저 올린다.
2. Oracle VM을 만든다.
3. 공인 IP와 80 포트를 연다.
4. 서버에서 clone 후 gunicorn 단독으로 먼저 테스트한다.
5. 그 다음 systemd/Nginx를 붙인다.
6. 마지막에 도메인과 HTTPS를 붙인다.
