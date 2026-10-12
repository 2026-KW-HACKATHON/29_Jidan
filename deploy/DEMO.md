# 공개 매뉴얼 데모

`demo/back-end/dev`와 `demo/front-end/dev`를 PR merge commit으로 `demo/main`에 통합한다.
일반 사용자는 `jidan-ilban-demo.leehyowon14.dev`, 점주는
`jidan-jumju-demo.leehyowon14.dev`에 접속하며 홈·로그인 화면을 제공하지 않는다.

사용자 지정 실행 서버는 **RPi4**다. GitHub-hosted ARM64 runner에서 검사와 이미지를
빌드하고, RPi4 runner는 digest로 고정한 이미지를 Compose로 실행한다. RPi4에서
소스 빌드를 수행하지 않는다. 기존 RPi5의 운영·dev 앱은 별도의 Compose 프로젝트다.

기존 dev env를 기준으로 RPi4 `/home/ubuntu/apps/jidan/demo/runtime.env`를 구성한다.
AI/STT·연동 값은 재사용하고 `DB_HOST`, `DB_NAME`, `DB_PORT`, `MEDIA_ROOT`, cookie와
Origin만 데모에 맞춰 지정한다. `jidan_demo` MySQL·데모 media volume을 새로 만들어
기존 dev DB·파일과 분리한다. env는 권한 600으로 보관하고 Git에 추가하지 않는다.

2026-10-13 RPi4의 실행·정지 컨테이너와 listening port를 확인했다. 데모는
frontend `127.0.0.1:3044`, worker API `127.0.0.1:3045`, owner API
`127.0.0.1:3046`을 사용한다. Nginx는 RPi4의 Tailscale IP `100.78.68.53:80`에서
역방향 프록시를 제공한다. 기존 RPi5의 Cloudflare Tunnel → Nginx 80에서 두 host만
RPi4로 전달하며 기존 host 설정은 유지한다. DB port는 host에 게시하지 않는다.

한 역할당 API 프로세스 하나·작업 실행기 하나로 요청 제한을 유지한다.
두 API는 같은 데모 DB와 media volume을 쓰고 DB의 기존 소유권·독자 권한을 검사한다.
별도 retention 서비스가 만료된 방문자 데이터·AI 입력·파일을 5분마다 정리한다.
고정 샘플은 초기화와 만료 정리 대상에서 제외한다.

배포 절차·검사·복구 명령은 전용 Compose 및 배포 스크립트와 함께 기록한다.
이미지 rollback은 제거된 임시 체험 데이터를 복원하지 않는다.
