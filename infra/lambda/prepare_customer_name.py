"""
IaC 파이프라인 ① 단계 — 이름 정제 Lambda.

Step Functions는 문자열 치환(_→-)을 못 하므로, 여기서 site_id를 받아
CloudFormation 스택명·DNS 도메인에 안전한 형태로 변환해 반환한다.

주의: SiteId 값 자체는 _ 원본을 유지(정적 파일 경로 sites/{site_id}/ 와 일치).
      stack_name·domain_name 만 - 로 바꾼다.
"""


def lambda_handler(event, context):
    detail = event.get('detail', event)
    site_id = detail['site_id']
    safe = site_id.replace('_', '-')
    return {
        'site_id': site_id,                                   # _ 원본 (S3 경로용)
        'stack_name': f'hezo-customer-{safe}',                # - (CFN 규칙)
        'domain_name': f'{safe}.doodo.cloud',                 # - (DNS 규칙)
        'template_type': detail.get('template_type', ''),
        'template_category': detail.get('template_category', 'landing'),
    }
