package com.example.batch;


import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.util.MultiValueMap;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
class AgentController {
  private final AgentService agent;

  AgentController(AgentService agent) {this.agent = agent;}

  @PostMapping(value = "/execute", consumes = MediaType.APPLICATION_FORM_URLENCODED_VALUE)
  ResponseEntity<?> execute(@RequestParam MultiValueMap<String, String> input) {
    return agent.execute(VerifyProcess.form(input));
  }
}
