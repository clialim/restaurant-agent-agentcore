# dining-web frontend

배포된 dining-web HTTP API(`POST /ask`)를 호출하는 Cloudscape 기반 React 채팅 SPA.

- Vite + React 19
- [Cloudscape Design System](https://cloudscape.design/) — `components`, `chat-components`, `global-styles`

## 로컬 실행

```bash
npm install
cp .env.example .env.local   # VITE_API_URL을 배포된 ApiUrl로 설정
npm run dev                  # http://localhost:5173
```

`VITE_API_URL`은 빌드/dev 서버 시작 시점에 주입됩니다. 값을 바꾸면 dev 서버를 재시작하세요.

로컬 오리진(`http://localhost:5173`)에서 배포된 API를 호출하려면 API 쪽 CORS 허용이 필요합니다. 상위 [`template.yaml`](../template.yaml)의 `DiningHttpApi.CorsConfiguration`에 해당 오리진이 등록되어 있습니다.

## 스크립트

| 명령 | 설명 |
| --- | --- |
| `npm run dev` | 로컬 개발 서버 |
| `npm run build` | 프로덕션 번들(`dist/`) |
| `npm run preview` | 빌드 결과 미리보기 |
| `npm run lint` | oxlint |
