import os
import time
from celery import Celery, Task
from project.server.functions import trigger_job_api
import yaml 
from pymongo import MongoClient
import bson
from datetime import datetime
from bson import json_util, ObjectId
import os

url = os.environ.get('REDIS_URL') or "redis://localhost"
port = os.environ.get('REDIS_PORT') or "6379"
redis_db = os.environ.get('REDIS_DB') or 0

celery = Celery(__name__)
celery.conf.broker_url = os.environ.get("CELERY_BROKER_URL", f"{url}:{port}/{redis_db}")
celery.conf.result_backend = os.environ.get("CELERY_RESULT_BACKEND", f"{url}:{port}/{redis_db}")
celery.conf.update(result_extended=True) 

# @celery.task(name="create_task")
# def create_task(task_type):
#     print(task_type)
#     time.sleep(int(task_type) * 10)
#     return True

db = MongoClient(os.environ.get('MONGODB_URI') or yaml.load(open('database.yaml'),Loader=yaml.FullLoader)['uri'])['jobilee']

@celery.task(name="trigger_api_task",bind=True)
def trigger_api_task(self,id,chosen_params):
    return trigger_job_api(id,chosen_params,self.request.id)

@celery.task(name="trigger_api_chart_task",bind=True)
def trigger_chart_job_task(self,name,chosen_params):
    if name:
        chosen_params = db["charts"].find_one({"name": name})
        chosen_params = {k: v if k != '_id' else str(v) for k, v in chosen_params.items()}
    update_doc = {
        "done": False,
        "chart_name": chosen_params['name'],
        "outputs": {},
        "creation_time":datetime.now().isoformat(),
        "message": "Success",
        "chosen_params":chosen_params
    }

    db["chart_tasks"].update_one({"_id": ObjectId(self.request.id)}, {"$set": update_doc},upsert=True)

    taskQueue = []
    for job in chosen_params['jobs']:
        taskQueue.append(trigger_api_task.apply_async(args=[job,{}],task_id=str(bson.ObjectId())))
    finished = len(taskQueue)
    while finished > 0:
        time.sleep(1)
        for task in taskQueue:
            if task.ready():
                finished -= 1        
    taskResults = []
    success = True
    for task in taskQueue:
        print(task)
        data = db["tasks"].find_one({"_id": ObjectId(task.id)})
        db_doc = {k: v if k != '_id' else str(v) for k, v in data.items()}
        taskResults.append(db_doc)
        if not db_doc['result']:
            message = "task {} of job {} failed. ".format(task.id,db_doc['job_name'])
            success = False
    if success:
        labels = []
        datasets = []
        
        # [
        #         {
        #             label: 'My First dataset',
        #             backgroundColor: '#42A5F5',
        #             data: [65, 59, 80, 81, 56, 55, 40]
        #         },
        #         {
        #             label: 'My Second dataset',
        #             backgroundColor: '#FFA726',
        #             data: [28, 48, 40, 19, 86, 27, 90]
        #         }
        # ]
        print(chosen_params)
        for v in taskResults:
            if v['job_name'] == chosen_params['label']['job']:
                for step in v['steps']:
                    if step['name'] == chosen_params['label']['step']:
                        if isinstance(step['items'],list):
                            for output in step['items']:
                                labels.append(output[chosen_params['label']['outputs']])
        labels = list(set(labels))
        for dataset in chosen_params['definitionTemplate']:
            for v in taskResults:
                for step in v['steps']:
                    if len(dataset['output']) > 0:
                        if step['name'] == dataset['output'][0]['step'] and v['job_name'] == dataset['output'][0]['job']:
                            if isinstance(step['items'],dict):
                                step['items'] = [step['items']]
                            data_counter = {item: 0 for item in labels} if chosen_params.get('type') != 'table' else []
                     
                            for label in labels:
                                if chosen_params.get('type') != 'table':
                                    for output in step['items']:
                                        if output[chosen_params['label']['outputs']] == label:
                                            condition = dataset['condition']
                                            if condition == "exists":
                                                if output[dataset['output'][0]['outputs']] != None:
                                                    data_counter[label] += 1    
                                            if condition == "==":
                                                if output[dataset['output'][0]['outputs']] == dataset['value']:
                                                    data_counter[label] += 1    
                                            if condition == "!=":
                                                if output[dataset['output'][0]['outputs']] != dataset['value']:
                                                    data_counter[label] += 1
                                            if condition == "contains":
                                                if dataset['value'] in output[dataset['output'][0]['outputs']]:
                                                    data_counter[label] += 1         
                                            if condition == "in":
                                                if output[dataset['output'][0]['outputs']] in dataset['value'].split(","):
                                                    data_counter[label] += 1      
                                            if condition == "not in":
                                                if output[dataset['output'][0]['outputs']] not in dataset['value'].split(","):
                                                    data_counter[label] += 1                                                                    
                                            if condition == ">":
                                                if output[dataset['output'][0]['outputs']] > dataset['value']:
                                                    data_counter[label] += 1    
                                            if condition == "<":
                                                if output[dataset['output'][0]['outputs']] < dataset['value']:
                                                    data_counter[label] += 1
                                            if condition == ">=":
                                                if output[dataset['output'][0]['outputs']] >= dataset['value']:
                                                    data_counter[label] += 1         
                                            if condition == "<=":
                                                if output[dataset['output'][0]['outputs']] <= dataset['value']:
                                                    data_counter[label] += 1  
                                else:
                                    for output in step['items']:
                                        data_counter.append(output)
                                                                    
                            datasets.append({
                                "label": dataset['name'],
                                "data": list(data_counter.values())
                            } if chosen_params.get('type') != 'table' else data_counter)
        db["chart_tasks"].update_one({"_id": ObjectId(self.request.id)}, {"$set": {
            "done": True,
            "result": True,
            "outputs": {
                "labels": labels if chosen_params.get('type') != 'table' else list(data_counter[0].keys()),
                "datasets": datasets if chosen_params.get('type') != 'table' else datasets[0]
            }
        }},upsert=True)
    else:
        db["chart_tasks"].update_one({"_id": ObjectId(self.request.id)}, {"$set": {
            "result": False,
            "message": message,
            "done": True
        }},upsert=True)

def get_task_meta(task_id):
    return celery.backend.get_task_meta(task_id)

def get_old_task(meta):
    print(meta)
    return celery.tasks[meta['name']]