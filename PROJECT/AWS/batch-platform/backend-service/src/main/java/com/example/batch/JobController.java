package com.example.batch;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/** Receive and Return Requests **/

@RestController
public class JobController {
  private final JobService jobs;

  JobController(JobService jobs) {this.jobs = jobs;}

  @PostMapping(value="/jobs", consumes = MediaType.APPLICATION_FORM_URLENCODED_VALUE)
  ResponseEntity<?> submit(@RequestParam MultiValueMap<String,String> input) {
    String id = jobs.submit(VerifyProcess.form(input));
    return ResponseEntity.accepted().body(Map.of("id", id));
  }

  @PostMapping(value = "/execution-claims", consumes = MediaType.APPLICATION_FORM_URLENCODED_VALUE)
  ResponseEntity<?> claim(@RequestParam MultiValueMap<String,String> input) {
    boolean claimed = jobs.claim(VerifyProcess.form(input));
    return ResponseEntity.status(claimed ? 201:409)
      .body(Map.of("claimed", claimed));
  }
}
