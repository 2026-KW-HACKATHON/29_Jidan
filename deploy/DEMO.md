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

## 배포와 복구

`deploy/demo/deploy.sh <40자리 commit> <frontend digest> <backend digest>`를 RPi4에서 실행한다.
env·Compose·Nginx 설정을 비공개 release에 복사하고, 중지된 컨테이너의 HostConfig까지
확인해 포트 충돌을 거부한다. DB를 시작한 뒤 게시된 backend 이미지로 migration·샘플
초기화·정리 CLI를 실행하며 서버에서 소스를 빌드하지 않는다.

전체 Compose health, 실행 이미지 참조, 공개 API health와 두 역할의 세션 응답을 확인한
후에만 current symlink를 원자적으로 바꾼다. 실패 시 이전 release의 이미지·환경 파일로
복구하며 첫 배포 실패는 데모 프로젝트만 내리고 volume을 보존한다. 종료 중 복구가
실패하면 pending 기록을 유지하고 다음 실행에서 먼저 복구한다.

이전 digest 두 개와 commit을 같은 스크립트에 전달하면 재배포할 수 있다. 이미지
rollback은 DB schema 또는 제거된 임시 체험 데이터를 복원하지 않는다. release 경로에
env 사본이 있으므로 상위 디렉터리 700과 파일 600을 유지한다.

`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s deploy/tests -p 'test_demo_*.py' -v`
에서 env 재사용·회전 거부, 포트 경계와 성공/실패/이미지 불일치/복구 실패·재개를 확인한다.
스크립트 테스트의 Docker·curl은 fixture이며 실제 배포 검증과 구분한다.
