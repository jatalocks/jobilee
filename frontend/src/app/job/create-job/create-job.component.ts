import { Job } from 'src/app/job';
import { Component } from '@angular/core';
@Component({
  selector: 'app-create-job',
  templateUrl: './create-job.component.html',
  styleUrls: ['./create-job.component.css']
})
export class CreateJobComponent {

  job: Job = {
    parameters: [
      {
        "name": "string-param",
        "type": "text",
        "default": "mytext"
      },
      {
        "name": "choice-param",
        "type": "choice",
        "default": "b",
        "choices": ["a","b","c","d"]
      },
      {
        "name": "multi-choice-param",
        "type": "multi-choice",
        "default": "f,h",
        "choices": ["e","f","g","h"]
      },
      {
        "name": "dynamic-param",
        "type": "dynamic",
        "default": "a,b",
        "job": {
          "id": "63d421df3aa83db7370e5096",
          "parameters": {
            "size": "1"
          },          
          "from": [{
            "step": 0,
            "outputs": ["first_name","email"],
          }]
        }
      }
  ]
  };

}

