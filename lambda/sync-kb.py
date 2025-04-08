import os
import json
import boto3


bedrockClient = boto3.client('bedrock-agent')

def lambda_handler(event, context):
    # TODO implement
    print('Inside Lambda Handler')
    print('event: ', event)
    dataSourceId = os.environ['DATASOURCEID'] # Create environment variable DATASOURCEID
    knowledgeBaseId = os.environ['KNOWLEDGEBASEID'] # Create environment variable KNOWLEDGEBASEID
    
    print('knowledgeBaseId: ', knowledgeBaseId)
    print('dataSourceId: ', dataSourceId)


    response = bedrockClient.start_ingestion_job(
        knowledgeBaseId=knowledgeBaseId,
        dataSourceId=dataSourceId
    )
    
    print('Ingestion Job Response: ', response)
    
    return {
        'statusCode': 200,
        'body': json.dumps('response')
    }