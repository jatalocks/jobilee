import { DBService } from "src/app/db.service";
import { Job } from "src/app/job";
import { Component, OnInit } from "@angular/core";
import { ActivatedRoute, Router } from '@angular/router';
import { RunService } from "src/app/run.service";
import { JsonEditorOptions } from "@maaxgr/ang-jsoneditor";
import Swal from "sweetalert2";
import { toSelectItem } from "src/main";
@Component({
  selector: "app-play-job",
  templateUrl: "./play-job.component.html",
  styleUrls: ["./play-job.component.scss"]
})
export class PlayJobComponent implements OnInit {
  _id: string;
  toHide = true;
  job: Job;
  response: any;
  editorOptions: JsonEditorOptions;
  dynamicResultsError: any[] = [];
  dynamicResults: any[] = [];
  constructor(private route: ActivatedRoute,private router: Router,
    private dbService: DBService, private runService: RunService) { 
      this.editorOptions = new JsonEditorOptions()
      this.editorOptions.modes = ['code', 'tree'];
      this.editorOptions.mode = 'code';
    }
  toSelectItem = toSelectItem;
  ngOnInit() {
    this.job = new Job();

    this._id = this.route.snapshot.params['_id'];
    this.dbService.getObject("jobs",this._id)
      .subscribe(data => {
        this.job = data;
        this.regenerateParams()
      }, error => console.log(error));
  }
  hiddenCount(){
    if (this.job?.parameters != undefined) {
      return this.job?.parameters.filter(p=>p.hidden).length

    } else {
      return 0
    }
  }
  flatten(items) {
    const flat = [];
  
    items.forEach(item => {
      if (Array.isArray(item)) {
        flat.push(...this.flatten(item));
      } else {
        flat.push(item);
      }
    });
  
    return flat;
  }
  hasDynamic(){
    return this.job?.parameters.some(obj => obj.type === "dynamic")
  }
  regenerateParams()
  {
    this.dynamicResultsError = []
    if (this.job?.parameters != undefined)
    {
      for (const param of this.job?.parameters)
      {
        if (param.type == "dynamic")
        {
          this.dynamicResults[param.name] = []
          this.generateDynamicParams(param)
  
        }
      }
    }
  }

  async generateDynamicParams(param) {
    try {
        let result = await this.runService.runJob(param['job']['id'], param['job']['parameters']).toPromise();
        let response;
        await new Promise(resolve => setTimeout(resolve, 1000));
        while ((response == undefined) || !('result' in response)) {
          try {
            response = await this.dbService.getObject("tasks", result["task_id"]).toPromise();
            if ((response == undefined) || !('result' in response)) {
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
          } catch {
            response = await this.dbService.getObject("tasks", result["task_id"]).toPromise();
          }
        }
        let options: any = []

        if (response['result']){
          
            for (const item of param['job']['from']) {
              for (const stepResult of response['steps'])
              {
                if (stepResult['name'] == item['step']) {
                    for (const [stepKey, stepValue] of Object.entries(stepResult['outputs']))
                    {
                      if (item['outputs'].includes(stepKey))
                      {
                        options = options.concat(stepValue)
                      }
                    }
                }
              }
            }
        }

        if(Object.prototype.toString.call(param.default) === '[object Array]') {
          param.default = this.flatten(param.default)
        } else {
          param.default = param.default.split(",")
        }
        if (options.length == 0) {
          this.dynamicResultsError[param.name] = "No Results"
          this.dynamicResults[param.name] = param.default[0]
        }


        if (!options.includes(param.default))
        {
          options.push(param.default[0])
        }
        this.dynamicResults[param.name] = options
    } catch (error) {
        console.log(JSON.stringify(error.message));
        this.dynamicResultsError[param.name] = error.message
        this.dynamicResults[param.name] = param.default
    }
}


  list(){
    this.router.navigate(['jobs']);
  }
  deleteJob(_id: string) {
    
    Swal.fire({
      title: 'Are you sure?',
      // text: "This will break jobs that depend on this job.",
      icon: 'warning',
      showCancelButton: true,
      confirmButtonColor: '#d33',
      cancelButtonColor: '#3085d6',
      confirmButtonText: 'Delete'
    }).then((result) => {
      if (result.isConfirmed) {
        this.dbService.deleteObject("jobs",_id)
        .subscribe(
          data => {
            Swal.fire(
              'Deleted!',
              'Job has been deleted.',
              'success'
            )
            this.router.navigate(['jobs']);
          },
          error => console.log(error));
      }
    })

  }
  result: any;
  run(){
    const params = this.job.parameters.map(p=>{ return {"key": p.name, "value": p.default}}).reduce((obj, item) => Object.assign(obj, { [item.key]: item.value }), {});
    this.runService.runJob(this._id,params).subscribe(result=>{
      Swal.fire({
        icon: 'success',
        title: 'Job Triggered Succesfully',
        // text: result['task_id'],
        timer: 1000
      }).then(data=>{
        this.router.navigate(['jobs','result',this._id,result["task_id"]]);
      })
    },error=>{
      Swal.fire({
        icon: 'error',
        title: 'Job Trigger Failed',
        text: JSON.stringify(error.message),
      })
    })
  }
}