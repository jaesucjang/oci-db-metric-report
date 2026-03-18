# OCI DB Metric Report

## Project Overview
OCI DB(PostgreSQL/MySQL) 메트릭 수집 → 차트 생성 → 분석 → PDF 리포트 웹 서비스

## Architecture
- **Backend**: Flask (Python 3), `app.py` (메인)
- **Frontend**: `templates/index.html` (Jinja2 + vanilla JS)
- **Scripts**: `fetch_metrics.sh`, `generate_charts.py`, `generate_report.sh`, `generate_pdf.py`, `fetch_db_info.py`, `genai_analysis.py`

## Deployment
- **VM**: `ubuntu@146.56.187.220` (`/home/ubuntu/oci-db-metric-report`)
- **SSH Key**: `/Users/jaesujan/mcp-servers/mcp-aws/ssh-key-2026-03-10.key`
- **Port**: 5050 (Flask, threaded=True)
- **프로세스 관리**:
  ```bash
  kill -9 $(lsof -ti :5050) 2>/dev/null; sleep 1
  cd /home/ubuntu/oci-db-metric-report && nohup python3 app.py > /tmp/app.log 2>&1 &
  ```
- **로그**: `/tmp/app.log`

## Development Workflow
1. VM에서 직접 파일 수정 (ssh + sed/python3 패치)
2. 문법 확인: `python3 -c "import py_compile; py_compile.compile('app.py', doraise=True)"`
3. 재시작 후 `curl -s -o /dev/null -w '%{http_code}' http://localhost:5050/` 로 확인
4. 로컬로 scp 복사 → git commit → push

## Git Push (VM에서 직접 push 불가)
VM에 GitHub 인증이 없으므로 로컬에서 push:
```bash
scp -i <key> ubuntu@146.56.187.220:/home/ubuntu/oci-db-metric-report/app.py ./app.py
scp -i <key> ubuntu@146.56.187.220:/home/ubuntu/oci-db-metric-report/templates/index.html ./templates/index.html
git add <files> && git commit && git push
```

## Key Conventions
- Jinja2 ↔ JS 템플릿 리터럴 충돌 → `{% raw %}...{% endraw %}` 블록 필수
- OCI API 호출 시 반드시 `oci_profile`, `oci_config_file`을 요청에서 읽어서 전달 (하드코딩 금지)
- `jobs` dict 접근 시 `jobs_lock` (threading.Lock) 사용
- PDF: 세로 A4, 20mm 마진, 차트 페이지 초과 시 자동 분할
- 한글 폰트: Noto Sans KR (fpdf2)
- PDF 다운로드 파일명: `REPORT_{PG|MySQL}_{resourceName}_{start}_{end}.pdf` (서버 `Content-Disposition` 헤더 → JS `downloadPdf()`에서 추출)
- GenAI (AI Analysis) 체크박스: 기본 OFF, 사용자가 필요 시 수동 체크

## 배포 방법 (로컬 → VM)
```bash
# 파일 전송
scp -i /Users/jaesujan/mcp-servers/mcp-aws/ssh-key-2026-03-10.key <로컬파일> ubuntu@146.56.187.220:/home/ubuntu/oci-db-metric-report/<경로>
# 재시작
ssh -i /Users/jaesujan/mcp-servers/mcp-aws/ssh-key-2026-03-10.key ubuntu@146.56.187.220 "kill -9 \$(lsof -ti :5050) 2>/dev/null; cd /home/ubuntu/oci-db-metric-report && nohup python3 app.py > /tmp/app.log 2>&1 &"
```

## OCI Profiles (VM ~/.oci/config)
- DEFAULT: ap-seoul-1 (Oracle 내부)
- WESANG_POC: ap-seoul-1 (위삭 PoC 전용 tenancy)
- CHICAGO: us-chicago-1
- CHUNCHEON: ap-chuncheon-1
- OSAKA: ap-osaka-1
