from bson import json_util, ObjectId
import json
import re
from pymongo import MongoClient
import yaml
import urllib3
import urllib.request 
import urllib.parse
import urllib3.exceptions
import time
from celery import Celery, current_task
from datetime import datetime
import codecs


class FakeDict(dict):
    def __init__(self, items):
        if items != []:
            self['something'] = 'something'
        self._items = items
    def items(self):
        return self._items

db = MongoClient(yaml.load(open('database.yaml'),Loader=yaml.FullLoader)['uri'])['jobilee']
reader = codecs.getreader("utf-8")
def parse_json(data):
    return json.loads(json_util.dumps(data))

def prepare_params(job_params, chosen_params, splitMultiChoice):
    for p in job_params:
        if not p['name'] in chosen_params:
            if p['type'] == 'text':
                chosen_params['name'] = p['default'] if 'default' in p else ''
            if p['type'] == 'choice' or p['type'] == 'multi-choice':
                chosen_params['name'] = p['default'] if 'default' in p else p['choices'][0]
 
    for x, y in list(chosen_params.items()):
        for p in job_params:
            if p['name'] == x:
                if p['type'] == "multi-choice":
                    temp = y
                    if isinstance(temp, list):
                        while "," in temp: temp.remove(",")   
                        if splitMultiChoice:
                            chosen_params[x] = temp
                        else:
                            chosen_params[x] = ','.join(temp)               
                    else:
                        if splitMultiChoice:
                            chosen_params[x] = temp.split(",")
                        else:
                            chosen_params[x] = temp
    
    return chosen_params

def replace_template(parameter, key_value_pairs):
    param_list = []
    for k, v in key_value_pairs.items():
        if isinstance(v, list):
            for p in v:
                param_list.append(json.loads(json.dumps(parameter).replace(f"{{key}}",k).replace(f"{{value}}",p)))
        elif isinstance(v, dict):
            for key, value in v.items():
                param_list.extend(replace_template(parameter, {key: value}))
        else:
            param_list.append(json.loads(json.dumps(parameter).replace(f"{{key}}",k).replace(f"{{value}}",v)))

    return param_list

def replace_parameters(parameter, payload, chosen_params):
    if isinstance(payload, list):
        if payload.count('{parameter}') > 0:
            new_parameters = replace_template(parameter, chosen_params)
            payload.remove('{parameter}')
            payload.extend(new_parameters)
        else:
            for i in payload:
                replace_parameters(parameter, i, chosen_params)
    elif isinstance(payload, dict):
        for k, v in payload.items():
            if v == '{parameter}':
                payload[k] = {k:v for list_item in replace_template(parameter, chosen_params) for (k,v) in list_item.items()}
            else:
                replace_parameters(parameter, v, chosen_params)
    elif isinstance(payload, str):
        if payload == f'{{parameter}}':
            new_parameters = replace_template(parameter, chosen_params)
            payload = new_parameters
    return payload

def querify(chosen_params,splitMultiChoice):
    return urllib.parse.urlencode(chosen_params, doseq=splitMultiChoice, safe=',~()*!.\'') 

def replace_placeholders(string, values):
    for key in values:
        string = string.replace("{" + key + "}", str(values[key]))
    return string

def process_step(job, integrationSteps,chosen_params,integration,outputs,task_id,stepIndex):
    message = "Success"
    error = ""
    r = {}
    chosen_params = prepare_params(job['parameters'], chosen_params, integration['splitMultiChoice'])
    url = integrationSteps["url"]+(replace_placeholders(integration['definition'].replace(f'{{job}}',job['apiID']),outputs))
    payload=querify(chosen_params,integration['splitMultiChoice'])
    headers = {d['key']: d['value'] for d in integration['headers']} 

    if integration["type"] == "post" and integration['mode'] == 'payload':
        replacedPayload = replace_parameters(integration['parameter'],integration['payload'],chosen_params)
        try:
            payload = FakeDict([(list(k.keys())[0],list(k.values())[0]) for k in replacedPayload])
        except:
            payload = replacedPayload
    http = urllib3.PoolManager(
        cert_reqs = 'CERT_NONE' if integration['ignoreSSL'] else 'CERT_REQUIRED'
    )
    if integration['authentication'] == "Basic":
        headers.update(urllib3.make_headers(basic_auth="{key}:{value}".format(key=integration['authenticationData'][0]['value'],value=integration['authenticationData'][1]['value'])))
    if integration['authentication'] == "Bearer":
        headers.update({'Authorization': 'Bearer {token}'.format(token=integration['authenticationData'][0]['value'])})


    update_step_field(task_id,stepIndex,"url",url)


    try:
        if integration["type"] == "post" and integration['mode'] == 'payload':
            r = http.request(
                method=integration["type"],
                url=url,
                headers=headers,
                body=json.dumps(payload).encode('utf-8'),
                timeout=3.0,
                retries=urllib3.util.Retry(total=0,backoff_factor=0)
            )
        else:
            r = http.request(
                method=integration["type"],
                url=url,
                headers=headers,
                fields=payload if integration['type'] == 'GET' else [(itm.split('=')[0],itm.split('=')[1]) for itm in payload.split("&")],
                timeout=3.0,
                retries=urllib3.util.Retry(total=0,backoff_factor=0)
            )          
    except Exception as ex:
        print(ex)
        error = str(ex)
    if r:
        if r.status not in range(200, 300):
            error = "Failure"

    message = error if error else message
    parsingOK = True
    parsingCondition = True
    res_json = r.json() if r else {}

    extracted_outputs = {}
    if integration['parsing']:
        if integration['outputs']:
            try:
                extract_placeholder_values(integration['outputs'], res_json, extracted_outputs)
                if bool(integration.get('retryUntil')):
                    for k, v in integration['retryUntil'].items():
                        if k in extracted_outputs:
                            if v != extracted_outputs[k]:
                                parsingOK = False
                        else:
                            parsingOK = False
                if bool(integration.get('failWhen')):
                    for k, v in integration['failWhen'].items():
                        if k in extracted_outputs:
                            if (v == extracted_outputs[k]) and (extracted_outputs[k] is not None):
                                parsingCondition = False
                        else:
                            parsingOK = False                            
                else:
                    if extracted_outputs:
                        for k, v in extracted_outputs.items():
                            if v is None:
                                parsingOK = False
            except Exception as e:
                print("Error")
                print(e)
                parsingOK = False
                message = str(e)
        
    update_step_field(task_id,stepIndex,"outputs",extracted_outputs)
    update_step_field(task_id,stepIndex,"response",res_json)
    update_step_field(task_id,stepIndex,"message",message)
    update_step_field(task_id,stepIndex,"status",r.status if r else 500)

    return {
        'extracted_outputs': extracted_outputs,
        'parsingCondition': parsingCondition,
        'parsingOK': parsingOK,
        "message": message,
        "r":r if r else {},
        "extracted_outputs":extracted_outputs,
        "res_json":res_json,
        "url":url
    }

def update_step_field(task_id,index,key,value):
    db["tasks"].update_one(
        {'_id': ObjectId(task_id), 'steps': {'$elemMatch': { 'step':  index }}}, {'$set': {'steps.$.'+key: value}
    })

# def update_time(task_id):
#     db["tasks"].update_one(
#     {'_id': ObjectId(task_id), "$set":
#         {
#             "update_time":datetime.now()
#         }
#     })


def percent(part, whole):
    return 100-(100 * float(part)/float(whole))

def process_request(job, integrationSteps,chosen_params,task_id):

    outputs = {}

    update_doc = {"job_id":job['_id'],"steps":[],"creation_time":datetime.now().isoformat(),"integration_id":str(integrationSteps["_id"]),"chosen_params":chosen_params}
    db["tasks"].update_one({"_id": ObjectId(task_id)}, {"$set": update_doc},upsert=True)
    resultAggregator = True
    for step in integrationSteps['steps']:
        stepIndex = integrationSteps['steps'].index(step)
        retriesLeft = (step['retryCount'] if step['retryCount'] >= 0 else 0)
        retriesDelay = (step['retryDelay'] if step['retryDelay'] >= 0 else 0)


        db["tasks"].update_one(
            {"_id": ObjectId(task_id)}, 
            {
                "$set":
                {
                    "update_time":datetime.now().isoformat()
                },
                "$push": {
                    "steps": {
                        "url": '',
                        "step":stepIndex,
                        "outputs": {},
                        "result": 0,
                        "percentDone": -1,
                        "status":0,
                        "message":'',
                        "response":{}
                    }
                }
            }, 
            upsert=True)
        res = process_step(job,integrationSteps,chosen_params,step,outputs,task_id,stepIndex)
        # or (not res['parsingOK']))
        while (res["r"].status if res["r"].status else 500) not in range(200, 300) and (retriesLeft > 0):
            time.sleep(retriesDelay)
            res = process_step(job,integrationSteps,chosen_params,step,outputs,task_id,stepIndex)
            retriesLeft -= 1
            update_step_field(task_id,stepIndex,"parsingOK",res['parsingOK'])
            update_step_field(task_id,stepIndex,"parsingCondition",res['parsingCondition'])
            update_step_field(task_id,stepIndex,"retriesLeft",retriesLeft)
            update_step_field(task_id,stepIndex,"percentDone",percent(retriesLeft,step['retryCount']))



        if (res["r"].status if res["r"].status else 500) in range(200, 300) and step['parsing']:
            timeOutLeft = (step['parsingTimeout'] if step['parsingTimeout'] >= 0 else 0)
            timeOutStartTime = datetime.now()            
            parsingDelay = (step['parsingDelay'] if step['parsingDelay'] >= 0 else 0)
            parsingTimeout = (step['parsingTimeout'] if step['parsingTimeout'] >= 0 else 0)            
            while ((not res['parsingOK']) and (timeOutLeft > 0)):
                print("parsing con",res['parsingCondition'])
                if not res['parsingCondition']:
                    update_step_field(task_id,stepIndex,"parsingOK",res['parsingOK'])
                    update_step_field(task_id,stepIndex,"retriesLeft",retriesLeft)
                    update_step_field(task_id,stepIndex,"timeOutLeft",0)
                    update_step_field(task_id,stepIndex,"parsingCondition",res['parsingCondition'])
                    update_step_field(task_id,stepIndex,"percentDone",100)
                    break
                time.sleep(parsingDelay)
                res = process_step(job,integrationSteps,chosen_params,step,outputs,task_id,stepIndex)
                timeOutLeft = int(parsingTimeout - (datetime.now()-timeOutStartTime).total_seconds())
                update_step_field(task_id,stepIndex,"parsingOK",res['parsingOK'])
                update_step_field(task_id,stepIndex,"retriesLeft",retriesLeft)
                update_step_field(task_id,stepIndex,"timeOutLeft",timeOutLeft)
                update_step_field(task_id,stepIndex,"parsingCondition",res['parsingCondition'])
                update_step_field(task_id,stepIndex,"percentDone",-1)


        update_step_field(task_id,stepIndex,"percentDone",100)
        outputs.update(res["extracted_outputs"])
        outcomeNumber = res_code(res)
        resultAggregator = (resultAggregator and outcomeNumber==2)
        update_step_field(task_id,stepIndex,"result",outcomeNumber)

    db["tasks"].update_one({"_id": ObjectId(task_id)}, {"$set":{"result":
        resultAggregator
    }})

def res_code(res):
    if (res["r"].status if res["r"].status else 500) not in range(200, 300):
        return 1
    else:
        if res['parsingCondition']:
            return 2
        else:
            return 3

def trigger_job_api(id,chosen_params,task_id):
    data = db["jobs"].find_one({'_id': ObjectId(id)})
    db_doc = {k: v if k != '_id' else str(v) for k, v in data.items()}
    integration = db["integrations"].find_one({'name': db_doc["integration"]})
    integration_doc = {k: v if k != '_id' else str(v) for k, v in integration.items()}    
    process_request(db_doc,integration_doc,chosen_params,task_id)

def extract_placeholder_values(data, values_data, placeholder_values):
    for key, value in data.items():
        if isinstance(value, str):
            match = re.search(r'{(.*?)}', value)
            if match:
                placeholder = match.group(1)
                placeholder_values[placeholder] = values_data[key]
        elif isinstance(value, dict):
            extract_placeholder_values(value, values_data[key], placeholder_values)
        elif isinstance(value, list):
            for i in range(len(value)):
                if isinstance(value[i], str):
                    match = re.search(r'{(.*?)}', value[i])
                    if match:
                        placeholder = match.group(1)
                        placeholder_values[placeholder] = values_data[key][i]
                elif isinstance(value[i], dict):
                    extract_placeholder_values(value[i], values_data[key][i], placeholder_values)

def check_placeholder_exists(data, values_data):
    for key, value in data.items():
        if isinstance(value, str):
            match = re.search(r'{(.*?)}', value)
            if match:
                placeholder = match.group(1)
                if placeholder not in values_data:
                    return False
        elif isinstance(value, dict):
            if not check_placeholder_exists(value, values_data[key]):
                return False
        elif isinstance(value, list):
            for i in range(len(value)):
                if isinstance(value[i], str):
                    match = re.search(r'{(.*?)}', value[i])
                    if match:
                        placeholder = match.group(1)
                        if placeholder not in values_data:
                            return False
                elif isinstance(value[i], dict):
                    if not check_placeholder_exists(value[i], values_data[key][i]):
                        return False
    return True