package com.example.batch;

import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
class BusinessController {
  private final BusinessService service;

  BusinessController(BusinessService service) {this.service = service;}

  @PostMapping(value = "/batch/reconcile", consumes = MediaType.APPLICATION_FORM_URLENCODED_VALUE)
  ResponseEntity<?> reconcile(@RequestParam MultiValueMap<String, String> input) throws java.io.IOException {
    var form = VerifyProcess.form(input);
    var result = service.run(VerifyProcess.id(form), VerifyProcess.date(form));
    return ResponseEntity.ok().header("X-Job-Status", result.status()).body(result);
  }
}
