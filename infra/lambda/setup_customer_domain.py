"""
EventBridge site-published 이벤트 → Lambda
Route 53에 CNAME 추가 + CloudFront에 Aliases 추가
"""
import json
import boto3
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

cf_client = boto3.client('cloudfront', region_name='us-east-1')
route53_client = boto3.client('route53')
cfn_client = boto3.client('cloudformation', region_name='ap-northeast-2')

# 환경 설정
HOSTED_ZONE_ID = 'Z10483373699UEVYVQFQS'  # doodo.cloud
WILDCARD_CERT_ARN = 'arn:aws:acm:us-east-1:492554570964:certificate/28b32e94-bdc2-40e2-ae45-7dfdd7d62c56'


def lambda_handler(event, context):
    """
    EventBridge 이벤트:
    {
        "detail": {
            "site_id": "site_tax_13_001",
            "template_type": "tax-accounting",
            "template_category": "landing"
        }
    }
    """
    try:
        site_id = event['detail']['site_id']
        # DNS 표준: 언더스코어 불허 → 하이픈으로 변환
        domain_name = f"{site_id.replace('_', '-')}.doodo.cloud"

        logger.info(f"🔧 고객사 도메인 설정: {domain_name}")

        # Step 1: CloudFormation 스택에서 CloudFront Distribution ID 조회
        logger.info("1️⃣ CloudFront Distribution ID 조회...")
        stack_name = f"hezo-customer-{site_id.replace('_', '-')}"
        cfn_response = cfn_client.describe_stacks(StackName=stack_name)

        cf_distribution_id = None
        for output in cfn_response['Stacks'][0].get('Outputs', []):
            if output['OutputKey'] == 'CloudFrontDistributionId':
                cf_distribution_id = output['OutputValue']
                break

        if not cf_distribution_id:
            raise Exception("CloudFront Distribution ID를 찾을 수 없음")

        logger.info(f"   Distribution ID: {cf_distribution_id}")

        # Step 2: CloudFront Distribution 설정 가져오기
        logger.info("2️⃣ CloudFront 설정 가져오기...")
        cf_response = cf_client.get_distribution_config(Id=cf_distribution_id)
        dist_config = cf_response['DistributionConfig']
        etag = cf_response['ETag']

        # Step 3: Aliases 추가
        logger.info("3️⃣ Aliases 추가...")
        dist_config['Aliases'] = {
            'Quantity': 1,
            'Items': [domain_name]
        }

        # Step 4: ViewerCertificate 업데이트 (ACM 인증서 사용)
        logger.info("4️⃣ ACM 인증서 설정...")
        dist_config['ViewerCertificate'] = {
            'ACMCertificateArn': WILDCARD_CERT_ARN,
            'SSLSupportMethod': 'sni-only',
            'MinimumProtocolVersion': 'TLSv1.2_2021',
            'Certificate': WILDCARD_CERT_ARN,
            'CertificateSource': 'acm'
        }

        # Step 5: CloudFront 업데이트
        logger.info("5️⃣ CloudFront Distribution 업데이트...")
        cf_client.update_distribution(
            Id=cf_distribution_id,
            DistributionConfig=dist_config,
            IfMatch=etag
        )
        logger.info(f"   ✅ CloudFront 업데이트 완료 (Distribution ID: {cf_distribution_id})")

        # Step 6: Route 53에 A Record 추가 (CloudFront로 Alias)
        logger.info("6️⃣ Route 53 A Record 추가...")
        cf_domain_name = cf_response['Distribution']['DomainName']

        route53_client.change_resource_record_sets(
            HostedZoneId=HOSTED_ZONE_ID,
            ChangeBatch={
                'Changes': [
                    {
                        'Action': 'CREATE',
                        'ResourceRecordSet': {
                            'Name': domain_name,
                            'Type': 'A',
                            'AliasTarget': {
                                'HostedZoneId': 'Z2FDTNDATAQYW2',  # CloudFront 고정 Zone ID
                                'DNSName': cf_domain_name,
                                'EvaluateTargetHealth': False
                            }
                        }
                    }
                ]
            }
        )
        logger.info(f"   ✅ Route 53 A Record 생성 완료 ({domain_name} → {cf_domain_name})")

        # Step 7: CloudFront 캐시 무효화 (인증서 적용 반영)
        logger.info("7️⃣ CloudFront 캐시 무효화...")
        cf_client.create_invalidation(
            DistributionId=cf_distribution_id,
            InvalidationBatch={
                'Paths': {
                    'Quantity': 1,
                    'Items': ['/*']
                },
                'CallerReference': f"{site_id}-domain-setup"
            }
        )
        logger.info("   ✅ 캐시 무효화 완료")

        result = {
            'statusCode': 200,
            'site_id': site_id,
            'domain_name': domain_name,
            'cf_distribution_id': cf_distribution_id,
            'message': f'도메인 {domain_name} 설정 완료 (HTTPS 적용 예상 시간: 5~15분)'
        }

        logger.info(f"✅ 성공: {json.dumps(result)}")
        return result

    except Exception as e:
        logger.error(f"❌ 오류: {str(e)}", exc_info=True)
        raise
