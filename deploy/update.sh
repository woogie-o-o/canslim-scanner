#!/bin/bash
# 코드 업데이트 후 서버 재시작 (Oracle Cloud에서 실행)
# 사용법: ./deploy/update.sh
cd /home/ubuntu/canslim-quant-scanner
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --quiet
sudo systemctl restart scanner
echo "업데이트 완료! 서버 재시작됨"
sudo systemctl status scanner --no-pager -l | head -10
