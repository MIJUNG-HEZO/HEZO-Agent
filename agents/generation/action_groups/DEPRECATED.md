# DEPRECATED — v1.2 Lambda Action Groups

이 디렉터리는 v1.2 관리형 Bedrock Agent의 Lambda Action Group 코드입니다.
**v2.0에서 AgentCore Runtime으로 전환하면서 폐기되었습니다.**

## 대체 위치

| v1.2 파일 | v2.0 대체 |
|---|---|
| `contract_loader.py` (Lambda 핸들러) | `../tools/contract_loader.py` (Python 모듈) |
| `render_spec_saver.py` (Lambda 핸들러) | `../tools/render_spec_saver.py` (Python 모듈) |
| `contract_uploader.py` | 미사용 (P1에서 직접 S3 업로드) |

## 참고

전환 이유: `PRD_P4_확정판_v2.md § 0. 아키텍처 전환 배경`
