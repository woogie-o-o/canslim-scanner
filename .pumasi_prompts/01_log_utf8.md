다음 두 Python 파일의 logging 초기화를 수정해 로그파일을 UTF-8로 기록하도록 만드세요. **다른 파일은 절대 수정 금지.**

## 수정할 파일 (절대경로)
1. `C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\quant_nexus_v20.py`
2. `C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\web_app\app.py`

## quant_nexus_v20.py 변경 (191~195번째 줄)

현재:
```python
logging.basicConfig(
    filename='quant_nexus_v20.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
```

다음으로 **교체**:
```python
from logging.handlers import RotatingFileHandler

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_LOG_FMT = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
# 중복 방지: 같은 baseFilename 핸들러가 이미 있으면 추가하지 않음
_log_path_qn = 'quant_nexus_v20.log'
_already = any(
    isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '').endswith('quant_nexus_v20.log')
    for h in _root_logger.handlers
)
if not _already:
    _fh = RotatingFileHandler(_log_path_qn, maxBytes=5_000_000, backupCount=3, encoding='utf-8', errors='replace')
    _fh.setLevel(logging.INFO)
    _fh.setFormatter(_LOG_FMT)
    _root_logger.addHandler(_fh)
```

## web_app/app.py 변경 (33번째 줄)

현재:
```python
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
```

다음으로 **교체**:
```python
from logging.handlers import RotatingFileHandler

_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_app_fmt = logging.Formatter("%(levelname)s %(message)s")

# RotatingFileHandler (UTF-8) — 중복 방지
_app_log_path = 'quant_nexus_v20.log'
_app_fh_exists = any(
    isinstance(h, RotatingFileHandler) and getattr(h, 'baseFilename', '').endswith('quant_nexus_v20.log')
    for h in _root_logger.handlers
)
if not _app_fh_exists:
    _app_fh = RotatingFileHandler(_app_log_path, maxBytes=5_000_000, backupCount=3, encoding='utf-8', errors='replace')
    _app_fh.setLevel(logging.INFO)
    _app_fh.setFormatter(_app_fmt)
    _root_logger.addHandler(_app_fh)

# StreamHandler (콘솔) — 중복 방지
_app_sh_exists = any(isinstance(h, logging.StreamHandler) and not isinstance(h, RotatingFileHandler) for h in _root_logger.handlers)
if not _app_sh_exists:
    _app_sh = logging.StreamHandler(sys.stderr)
    _app_sh.setLevel(logging.INFO)
    _app_sh.setFormatter(_app_fmt)
    _root_logger.addHandler(_app_sh)
```

## 검증
변경 후 다음을 실행해 성공 확인:
```bash
python -m py_compile "C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\quant_nexus_v20.py"
python -m py_compile "C:\Users\Administrator\Documents\카카오톡 받은 파일\종목스캐너\web_app\app.py"
```

## 금지사항
- 다른 함수/줄 수정 금지
- 새 외부 라이브러리 추가 금지
- 로그 파일명 변경 금지
