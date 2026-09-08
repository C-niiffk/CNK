package com.example.batch;
import java.util.*;
import org.springframework.http.*;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.*;

@RestController
class FrontendController {
  private final DBUtils repository;
  private final BackendClient client;
  FrontendController(DBUtils repository,BackendClient client){this.repository=repository;this.client=client;}
  @GetMapping("/") ResponseEntity<Void> index(){return ResponseEntity.status(302).header("Location","/index.html").build();}
  @GetMapping("/api/jobs") Object list(){return repository.query("SELECT * FROM BATCH_OWNER.JOBS ORDER BY created_at DESC,id DESC FETCH FIRST 100 ROWS ONLY");}
  @GetMapping("/api/jobs/{id}") ResponseEntity<?> get(@PathVariable String id){
    var rows=repository.query("SELECT * FROM BATCH_OWNER.JOBS WHERE id=?",id);
    return rows.isEmpty()?ResponseEntity.status(404).body(Map.of("error","Not found")):ResponseEntity.ok(rows.get(0));
  }
  @PostMapping(value="/api/jobs",consumes=MediaType.APPLICATION_FORM_URLENCODED_VALUE)
  ResponseEntity<?> submit(@RequestParam MultiValueMap<String,String> input,@RequestHeader(value="X-Batch-Request",defaultValue="") String csrf){
    if(!csrf.equals("1"))return ResponseEntity.status(403).body(Map.of("error","X-Batch-Request: 1 required"));
    var form=VerifyProcess.form(input);VerifyProcess.validateJob(form);VerifyProcess.field(form,"requestKey","[A-Za-z0-9_-]{8,100}");
    var response=client.post(VerifyProcess.required("BACKEND_URL")+"/jobs",form,20);
    return ResponseEntity.status(response.getStatusCode()).contentType(MediaType.APPLICATION_JSON).body(response.getBody());
  }
}
